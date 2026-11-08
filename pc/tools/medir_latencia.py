"""Mide la latencia del control y del video, sin dependencias externas.

Envía comandos neutros a 50 Hz: acelerador 0, freno 0 y dirección al centro. El carrito solo
responde pings a quien ya le mandó un comando, y así el tráfico se parece al de la app. Los
motores no giran, pero el servo vuelve al centro.

Mide tres cosas:
  1. Ping UDP al carrito sin video.
  2. Ping UDP al carrito con el video activo, para ver si compiten por el WiFi.
  3. Con --comparar, fps, tamaño y tiempo de transferencia en varias combinaciones de
     resolución y calidad. Al terminar restaura los valores que tenía la cámara.

Cierra la app antes de medir: la cámara acepta un solo cliente de video.

Uso desde la carpeta pc/:
    python tools/medir_latencia.py
    python tools/medir_latencia.py --comparar
    python tools/medir_latencia.py --carrito 192.168.1.50 --camara 192.168.1.51 --segundos 20
"""

import argparse
import json
import random
import socket
import statistics
import struct
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

MAGIC_COMMAND = 0xC4
MAGIC_PING = 0xC6
MAGIC_PONG = 0xC7
PROTOCOL_VERSION = 1
COMMAND_FORMAT = "<BBHhhBHB"
PING_FORMAT = "<BBIB"

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"
DEFAULT_CAR = "carrito.local"
DEFAULT_CAR_PORT = 4210
DEFAULT_CAMERA = "camara.local"
DEFAULT_CAMERA_PORT = 81

COMMAND_PERIOD_S = 1 / 50
PING_PERIOD_S = 0.05
PONG_GRACE_S = 0.5
HTTP_TIMEOUT_S = 5
STREAM_START_TIMEOUT_S = 5
SETTLE_S = 2.0
REBOOT_HINT = "Si la cámara se reinició, revisa su alimentación: perdió los ajustes y estos datos no valen."

# Índices de framesize según /status y /control: 6 QVGA, 8 CIF, 9 HVGA, 10 VGA, 11 SVGA.
PRESETS = [
    ("SVGA q12", 11, 12),
    ("VGA q12", 10, 12),
    ("VGA q20", 10, 20),
    ("HVGA q12", 9, 12),
    ("CIF q12", 8, 12),
    ("CIF q20", 8, 20),
    ("QVGA q12", 6, 12),
]


def crc8(data: bytes) -> int:
    """CRC-8 con polinomio 0x07 y valor inicial 0x00."""
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def build_neutral_command(seq: int) -> bytes:
    body = struct.pack(COMMAND_FORMAT[:-1], MAGIC_COMMAND, PROTOCOL_VERSION, seq, 0, 0, 0, 0)
    return body + bytes([crc8(body)])


def build_ping(timestamp_us: int) -> bytes:
    body = struct.pack(PING_FORMAT[:-1], MAGIC_PING, PROTOCOL_VERSION, timestamp_us)
    return body + bytes([crc8(body)])


def parse_pong(data: bytes) -> int | None:
    """Devuelve la marca de tiempo del pong, o None si el paquete no es uno válido."""
    if len(data) != struct.calcsize(PING_FORMAT) or data[0] != MAGIC_PONG or data[1] != PROTOCOL_VERSION:
        return None
    if crc8(data[:-1]) != data[-1]:
        return None
    return struct.unpack(PING_FORMAT, data)[2]


def now_us() -> int:
    return time.perf_counter_ns() // 1000 & 0xFFFFFFFF


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * fraction), len(ordered) - 1)]


def spread(values: list[float]) -> str:
    if not values:
        return "sin datos"
    return (
        f"mediana {statistics.median(values):6.1f}  p95 {percentile(values, 0.95):6.1f}  "
        f"máx {max(values):6.1f} ms"
    )


def resolve_host(host: str) -> str:
    # Se resuelve una vez para que el tiempo de mDNS no entre en las mediciones.
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
    except socket.gaierror as error:
        sys.exit(f"No se pudo resolver {host}: {error}. Prueba con la IP.")


# ---------- Control ----------
@dataclass
class PingResult:
    sent: int
    rtts_ms: list[float]

    @property
    def lost_pct(self) -> float:
        return 100 * (self.sent - len(self.rtts_ms)) / self.sent if self.sent else 0.0


def measure_ping(address: tuple[str, int], seconds: float) -> PingResult:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        # En Windows, recvfrom falla en un socket sin dirección local.
        sock.bind(("", 0))
        sock.setblocking(False)

        pending: set[int] = set()
        rtts_ms: list[float] = []
        sent = 0
        seq = random.randrange(0x10000)
        end_of_sending = time.perf_counter() + seconds
        next_ping = time.perf_counter()
        next_command = next_ping

        while True:
            now = time.perf_counter()
            if now >= end_of_sending + PONG_GRACE_S:
                return PingResult(sent, rtts_ms)

            if now < end_of_sending and now >= next_command:
                seq = (seq + 1) & 0xFFFF
                try:
                    sock.sendto(build_neutral_command(seq), address)
                except OSError:
                    pass
                next_command += COMMAND_PERIOD_S

            if now < end_of_sending and now >= next_ping:
                timestamp = now_us()
                try:
                    sock.sendto(build_ping(timestamp), address)
                    pending.add(timestamp)
                    sent += 1
                except OSError:
                    pass
                next_ping += PING_PERIOD_S

            try:
                data, _ = sock.recvfrom(2048)
            except (BlockingIOError, ConnectionResetError):
                time.sleep(0.0005)
                continue

            timestamp = parse_pong(data)
            if timestamp in pending:
                pending.discard(timestamp)
                rtts_ms.append(((now_us() - timestamp) & 0xFFFFFFFF) / 1000)


# ---------- Video ----------
@dataclass
class StreamStats:
    arrivals: list[float] = field(default_factory=list)
    sizes: list[int] = field(default_factory=list)
    transfers_ms: list[float] = field(default_factory=list)

    def reset(self) -> None:
        self.arrivals, self.sizes, self.transfers_ms = [], [], []

    def line(self) -> str:
        if len(self.arrivals) < 2:
            return "sin frames suficientes"
        duration = self.arrivals[-1] - self.arrivals[0]
        intervals = [(b - a) * 1000 for a, b in zip(self.arrivals, self.arrivals[1:])]
        fps = (len(self.arrivals) - 1) / duration
        size_kb = statistics.mean(self.sizes) / 1024
        mbps = sum(self.sizes[1:]) * 8 / duration / 1_000_000
        transfer = statistics.median(self.transfers_ms)
        return (
            f"{fps:5.1f} fps  {size_kb:6.1f} KB  {mbps:5.2f} Mbit/s  "
            f"intervalo p95 {percentile(intervals, 0.95):5.0f} máx {max(intervals):5.0f} ms  "
            f"transferencia {transfer:5.1f} ms"
        )


class StreamReader(threading.Thread):
    """Lee /stream sin guardar imágenes y registra cuándo llega cada frame."""

    def __init__(self, url: str) -> None:
        super().__init__(name="stream", daemon=True)
        self._url = url
        self._halt = threading.Event()
        self.stats = StreamStats()
        self.error: str | None = None

    def run(self) -> None:
        try:
            with urllib.request.urlopen(self._url, timeout=HTTP_TIMEOUT_S) as response:
                while not self._halt.is_set():
                    self._read_frame(response)
        except urllib.error.HTTPError as error:
            hint = " ¿La app sigue abierta?" if error.code == 503 else ""
            self.error = f"/stream respondió {error.code}.{hint}"
        except (OSError, ValueError) as error:
            if not self._halt.is_set():
                self.error = f"el stream se cortó: {error}"

    def _read_frame(self, response) -> None:
        length = None
        while True:
            line = response.readline()
            if not line:
                raise ValueError("fin del stream")
            lowered = line.strip().lower()
            if lowered.startswith(b"content-length:"):
                length = int(lowered.split(b":", 1)[1])
                started = time.perf_counter()
            elif not lowered and length is not None:
                break

        response.read(length)
        finished = time.perf_counter()
        stats = self.stats
        stats.arrivals.append(finished)
        stats.sizes.append(length)
        stats.transfers_ms.append((finished - started) * 1000)

    def wait_for_frames(self) -> bool:
        deadline = time.perf_counter() + STREAM_START_TIMEOUT_S
        while time.perf_counter() < deadline and self.error is None:
            if self.stats.arrivals:
                return True
            time.sleep(0.02)
        return False

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=HTTP_TIMEOUT_S + 1)


def fetch_status(base_url: str) -> dict:
    with urllib.request.urlopen(f"{base_url}/status", timeout=HTTP_TIMEOUT_S) as response:
        return json.load(response)


def set_camera(base_url: str, name: str, value: int) -> None:
    urllib.request.urlopen(f"{base_url}/control?var={name}&val={value}", timeout=HTTP_TIMEOUT_S).close()


def compare_presets(base_url: str, reader: StreamReader, seconds: float, original: dict) -> None:
    print(f"\nComparación de video, {seconds:g} s por combinación:")
    try:
        for name, framesize, quality in PRESETS:
            set_camera(base_url, "framesize", framesize)
            set_camera(base_url, "quality", quality)
            time.sleep(SETTLE_S)
            reader.stats.reset()
            time.sleep(seconds)
            if reader.error:
                print(f"  {name:9} {reader.error}")
                return
            print(f"  {name:9} {reader.stats.line()}", flush=True)
    finally:
        set_camera(base_url, "framesize", original["framesize"])
        set_camera(base_url, "quality", original["quality"])
        print(f"Cámara restaurada: framesize {original['framesize']}, quality {original['quality']}.")


# ---------- Principal ----------
def load_defaults() -> tuple[str, int, str, int]:
    car, car_port = DEFAULT_CAR, DEFAULT_CAR_PORT
    camera, camera_port = DEFAULT_CAMERA, DEFAULT_CAMERA_PORT
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as file:
            config = tomllib.load(file)
        car = config.get("carrito", {}).get("host", car)
        car_port = config.get("carrito", {}).get("port", car_port)
        url = urlsplit(config.get("camara", {}).get("url", ""))
        camera = url.hostname or camera
        camera_port = url.port or camera_port
    return car, car_port, camera, camera_port


def parse_args() -> argparse.Namespace:
    car, car_port, camera, camera_port = load_defaults()
    parser = argparse.ArgumentParser(description="Mide la latencia del control y del video.")
    parser.add_argument("--carrito", default=car, help="IP o nombre del carrito")
    parser.add_argument("--puerto-carrito", type=int, default=car_port, help="puerto UDP del carrito")
    parser.add_argument("--camara", default=camera, help="IP o nombre de la cámara")
    parser.add_argument("--puerto-camara", type=int, default=camera_port, help="puerto HTTP de la cámara")
    parser.add_argument("--segundos", type=float, default=10, help="duración de cada medición")
    parser.add_argument("--comparar", action="store_true", help="prueba varias resoluciones y calidades")
    return parser.parse_args()


def print_ping(label: str, result: PingResult) -> None:
    print(f"  {label:12} {spread(result.rtts_ms)}  perdidos {result.lost_pct:.1f}% de {result.sent}")


def main() -> int:
    args = parse_args()
    if args.segundos <= 0:
        sys.exit("--segundos debe ser mayor que 0.")

    car_address = (resolve_host(args.carrito), args.puerto_carrito)
    camera_ip = resolve_host(args.camara)
    base_url = f"http://{camera_ip}:{args.puerto_camara}"
    print(f"Carrito {car_address[0]}:{car_address[1]}  Cámara {base_url}")

    try:
        original = fetch_status(base_url)
    except (OSError, ValueError) as error:
        sys.exit(f"La cámara no respondió en /status: {error}")
    print(
        f"Cámara: {original['resolution']} calidad {original['quality']}, "
        f"rssi {original['rssi']} dBm, clientes {original['clients']}"
    )

    print("\nPing al carrito (ida y vuelta):")
    print_ping("sin video", measure_ping(car_address, args.segundos))

    reader = StreamReader(f"{base_url}/stream")
    reader.start()
    if not reader.wait_for_frames():
        reader.stop()
        sys.exit(reader.error or "El stream no entregó frames.")

    time.sleep(1)
    reader.stats.reset()
    with_video = measure_ping(car_address, args.segundos)
    if reader.error:
        # Si el video se cortó, parte de esta medición corrió sin video y no sirve.
        print(f"  con video    INVÁLIDA: {reader.error}")
        print(f"\nVideo hasta el corte:\n  {reader.stats.line()}")
        reader.stop()
        print(REBOOT_HINT)
        return 1
    print_ping("con video", with_video)
    print(f"\nVideo actual:\n  {reader.stats.line()}")

    try:
        status = fetch_status(base_url)
        print(f"  rssi con video {status['rssi']} dBm, fps según la cámara {status['fps']}")
        if args.comparar:
            compare_presets(base_url, reader, args.segundos, original)
    except OSError as error:
        print(f"La cámara dejó de responder: {error}.")
        print(REBOOT_HINT)
        return 1
    finally:
        reader.stop()

    if reader.error:
        print(f"Aviso: {reader.error}")
        print(REBOOT_HINT)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
