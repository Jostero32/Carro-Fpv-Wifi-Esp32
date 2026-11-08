"""Prueba el carrito según docs/protocolo.md, sin dependencias externas.

Envía comandos a 50 Hz con un barrido lento de dirección y acelerador bajo, un ping por
segundo, y muestra la telemetría y la latencia en una línea que se actualiza.

Uso desde la carpeta pc/:
    python tools/probar_carrito.py --sin-motor
    python tools/probar_carrito.py --host 192.168.1.50 --puerto 4210

Con motores, mantén las ruedas en el aire. Ctrl+C termina.
"""

import argparse
import math
import socket
import struct
import sys
import time

MAGIC_COMMAND = 0xC4
MAGIC_TELEMETRY = 0xC5
MAGIC_PING = 0xC6
MAGIC_PONG = 0xC7
PROTOCOL_VERSION = 1

COMMAND_FORMAT = "<BBHhhBHB"
PING_FORMAT = "<BBIB"
TELEMETRY_FORMAT = "<BBHbHhBB"

FLAG_FAILSAFE = 1 << 0
FLAG_BRAKING = 1 << 1
FLAG_LIGHTS = 1 << 2

COMMAND_PERIOD_S = 1 / 50
PING_PERIOD_S = 1.0
DISPLAY_PERIOD_S = 0.1
TELEMETRY_STALE_S = 1.0
STOP_PACKETS = 5

SWEEP_PERIOD_S = 6.0
SWEEP_AMPLITUDE = 1000
LOW_THROTTLE = 250


def crc8(data: bytes) -> int:
    """CRC-8 con polinomio 0x07 y valor inicial 0x00."""
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def with_crc(packet_format: str, *fields: int) -> bytes:
    body = struct.pack(packet_format[:-1], *fields)
    return body + bytes([crc8(body)])


def build_command(seq: int, steering: int, throttle: int) -> bytes:
    return with_crc(COMMAND_FORMAT, MAGIC_COMMAND, PROTOCOL_VERSION, seq, steering, throttle, 0, 0)


def build_ping(timestamp_us: int) -> bytes:
    return with_crc(PING_FORMAT, MAGIC_PING, PROTOCOL_VERSION, timestamp_us)


def has_valid_crc(data: bytes) -> bool:
    return crc8(data[:-1]) == data[-1]


def now_us() -> int:
    return time.perf_counter_ns() // 1000 & 0xFFFFFFFF


def resolve_host(host: str) -> str:
    # Se resuelve una sola vez: resolver en cada envío frenaría el ciclo de 50 Hz.
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]
    except socket.gaierror as error:
        sys.exit(f"No se pudo resolver {host}: {error}. Prueba con --host y la IP del carrito.")


def format_flags(flags: int) -> str:
    names = [
        (FLAG_FAILSAFE, "FAILSAFE"),
        (FLAG_BRAKING, "FRENO"),
        (FLAG_LIGHTS, "LUCES"),
    ]
    return ",".join(name for bit, name in names if flags & bit) or "-"


class Status:
    """Último estado recibido del carrito."""

    def __init__(self) -> None:
        self.telemetry: tuple | None = None
        self.telemetry_time = 0.0
        self.rtt_ms: float | None = None
        self.invalid = 0

    def handle_packet(self, data: bytes, now: float) -> None:
        if not data:
            return
        if data[0] == MAGIC_TELEMETRY and self._is_valid(data, TELEMETRY_FORMAT):
            self.telemetry = struct.unpack(TELEMETRY_FORMAT, data)
            self.telemetry_time = now
        elif data[0] == MAGIC_PONG and self._is_valid(data, PING_FORMAT):
            _, _, timestamp_us, _ = struct.unpack(PING_FORMAT, data)
            self.rtt_ms = ((now_us() - timestamp_us) & 0xFFFFFFFF) / 1000
        else:
            self.invalid += 1

    @staticmethod
    def _is_valid(data: bytes, packet_format: str) -> bool:
        return (
            len(data) == struct.calcsize(packet_format)
            and data[1] == PROTOCOL_VERSION
            and has_valid_crc(data)
        )

    def line(self, steering: int, throttle: int, now: float) -> str:
        sent = f"dir={steering:+5d} acel={throttle:+4d}"
        rtt = f"rtt={self.rtt_ms:.1f}ms" if self.rtt_ms is not None else "rtt=---"
        if self.telemetry is None or now - self.telemetry_time > TELEMETRY_STALE_S:
            return f"{sent} | sin telemetría | {rtt} inv={self.invalid}"

        _, _, last_seq, rssi, battery_mv, applied, flags, _ = self.telemetry
        received = (
            f"seq={last_seq:5d} rssi={rssi:4d}dBm bat={battery_mv:5d}mV "
            f"real={applied:+5d} {format_flags(flags)}"
        )
        return f"{sent} | {received} | {rtt} inv={self.invalid}"


def send(sock: socket.socket, data: bytes, address: tuple[str, int]) -> None:
    try:
        sock.sendto(data, address)
    except OSError:
        # La red de la PC puede caer un momento. La línea mostrará "sin telemetría".
        pass


def receive_pending(sock: socket.socket, status: Status) -> None:
    while True:
        try:
            # Grande a propósito: Windows da error si el datagrama no cabe en el buffer.
            data, _ = sock.recvfrom(2048)
        except BlockingIOError:
            return
        except ConnectionResetError:
            # Windows avisa así del ICMP "puerto inalcanzable". El carrito puede estar apagado.
            continue
        status.handle_packet(data, time.perf_counter())


def run(sock: socket.socket, address: tuple[str, int], no_motor: bool) -> int:
    status = Status()
    seq = 0
    steering = 0
    throttle = 0
    start = time.perf_counter()
    next_command = start
    next_ping = start
    next_display = start

    try:
        while True:
            now = time.perf_counter()

            if now >= next_command:
                seq = (seq + 1) & 0xFFFF
                phase = (now - start) / SWEEP_PERIOD_S
                steering = round(SWEEP_AMPLITUDE * math.sin(2 * math.pi * phase))
                throttle = 0 if no_motor else LOW_THROTTLE
                send(sock, build_command(seq, steering, throttle), address)
                # Sin esto, tras una pausa larga saldría una ráfaga para recuperar el retraso.
                next_command = max(next_command + COMMAND_PERIOD_S, now)

            if now >= next_ping:
                send(sock, build_ping(now_us()), address)
                next_ping += PING_PERIOD_S

            receive_pending(sock, status)

            if now >= next_display:
                sys.stdout.write("\r" + status.line(steering, throttle, now).ljust(110))
                sys.stdout.flush()
                next_display += DISPLAY_PERIOD_S

            time.sleep(0.001)
    except KeyboardInterrupt:
        return seq


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prueba el carrito por UDP.")
    parser.add_argument("--host", default="carrito.local", help="IP o nombre del carrito")
    parser.add_argument("--puerto", type=int, default=4210, help="puerto UDP del carrito")
    parser.add_argument("--sin-motor", action="store_true", help="manda throttle 0")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ip = resolve_host(args.host)
    address = (ip, args.puerto)
    mode = "sin motor" if args.sin_motor else f"acelerador {LOW_THROTTLE}"
    print(f"Enviando a {ip}:{args.puerto} ({mode}). Ctrl+C termina.")

    # Un solo socket: el carrito responde a la IP y puerto de origen de los comandos.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("", 0))
        sock.setblocking(False)
        seq = run(sock, address, args.sin_motor)

        for _ in range(STOP_PACKETS):
            seq = (seq + 1) & 0xFFFF
            send(sock, build_command(seq, 0, 0), address)
            time.sleep(COMMAND_PERIOD_S)
    print("\nDetenido.")


if __name__ == "__main__":
    main()
