"""Enlace UDP con el carrito: envío de comandos, ping y telemetría en un solo socket."""

import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from carrito_pc import protocol
from carrito_pc.controls import Command

LOOP_SLEEP_S = 0.001
CONNECTED_TIMEOUT_S = 1.0
LATENCY_TIMEOUT_S = 3.0
RESOLVE_RETRY_S = 2.0
PING_TEST_TIMEOUT_S = 1.0
STOP_PACKETS = 5
MDNS_TIMEOUT_MS = 1500


@dataclass(frozen=True)
class LinkStatus:
    connected: bool = False
    failsafe: bool = False
    latency_ms: float | None = None
    rssi: int | None = None
    battery_mv: int | None = None


def resolve_host(host: str) -> str | None:
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]
    except socket.gaierror:
        pass

    if host.endswith(".local"):
        return _resolve_mdns(host)
    return None


def _resolve_mdns(host: str) -> str | None:
    from zeroconf import AddressResolver, Zeroconf

    zeroconf = Zeroconf()
    try:
        resolver = AddressResolver(host.rstrip(".") + ".")
        if resolver.request(zeroconf, MDNS_TIMEOUT_MS):
            addresses = resolver.parsed_addresses()
            return addresses[0] if addresses else None
    finally:
        zeroconf.close()
    return None


def _now_us() -> int:
    return time.perf_counter_ns() // 1000 & 0xFFFFFFFF


def buttons_mask(command: Command) -> int:
    return (
        (protocol.BUTTON_LIGHTS if command.luces else 0)
        | (protocol.BUTTON_HORN if command.claxon else 0)
        | (protocol.BUTTON_TURBO if command.turbo else 0)
    )


class Link:
    def __init__(self, read_command: Callable[[], Command]) -> None:
        self._read_command = read_command
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

        self._host = ""
        self._port = 0
        self._ip: str | None = None
        self._resolved_host = ""
        self._period_s = 0.02
        self._ping_period_s = 1.0
        self._watchdog_s = 0.25

        self._seq = 0
        self._telemetry: protocol.Telemetry | None = None
        self._telemetry_time = 0.0
        self._latency_ms: float | None = None
        self._latency_time = 0.0
        self._battery_mv: int | None = None
        self._ping_requested = False
        self._pong_received = threading.Event()

    def configure(self, config: dict) -> None:
        """Aplica host, puerto y tiempos en caliente."""
        with self._lock:
            self._host = config["carrito"]["host"]
            self._port = config["carrito"]["port"]
            self._period_s = 1 / config["control"]["command_hz"]
            self._ping_period_s = config["control"]["ping_interval_s"]
            self._watchdog_s = config["seguridad"]["watchdog_ms"] / 1000

    def start(self) -> None:
        for target, name in ((self._run, "link"), (self._resolve_loop, "link-resolve")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=1)

    # ---------- Resolución de nombre ----------
    # Va en otro hilo porque puede tardar segundos y el envío no debe detenerse.
    def _resolve_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                host = self._host
                stale = host != self._resolved_host or self._ip is None or not self._is_connected()
            if host and stale:
                ip = resolve_host(host)
                with self._lock:
                    if host == self._host:
                        self._resolved_host = host
                        self._ip = ip
            self._stop.wait(RESOLVE_RETRY_S)

    # ---------- Envío y recepción ----------
    def _safe_command(self) -> Command:
        """Si la lectura de entrada se congeló, manda neutro en vez del último acelerador.
        El carrito no lo detectaría: sus comandos siguen llegando y no entra en failsafe."""
        command = self._read_command()
        if time.monotonic() - command.timestamp > self._watchdog_s:
            return replace(command, steering=0, throttle=0, brake=0, claxon=False, turbo=False)
        return command

    def _send(self, sock: socket.socket, data: bytes) -> None:
        with self._lock:
            address = (self._ip, self._port) if self._ip else None
        if address is None:
            return
        try:
            sock.sendto(data, address)
        except OSError:
            pass  # La red de la PC puede caer un momento. El estado mostrará desconectado.

    def _send_command(self, sock: socket.socket, command: Command) -> None:
        self._seq = (self._seq + 1) & 0xFFFF
        packet = protocol.encode_command(
            self._seq, command.steering, command.throttle, command.brake, buttons_mask(command)
        )
        self._send(sock, packet)

    def _receive(self, sock: socket.socket) -> None:
        while True:
            try:
                # Grande a propósito: Windows da error si el datagrama no cabe en el buffer.
                data, _ = sock.recvfrom(2048)
            except BlockingIOError:
                return
            except ConnectionResetError:
                continue  # Windows avisa así del ICMP "puerto inalcanzable".
            except OSError:
                return
            self._on_packet(protocol.decode_packet(data))

    def _on_packet(self, packet: protocol.Telemetry | protocol.Pong | None) -> None:
        now = time.monotonic()
        with self._lock:
            if isinstance(packet, protocol.Telemetry):
                self._telemetry = packet
                self._telemetry_time = now
                self._battery_mv = packet.battery_mv or None
            elif isinstance(packet, protocol.Pong):
                self._latency_ms = ((_now_us() - packet.timestamp_us) & 0xFFFFFFFF) / 1000
                self._latency_time = now
                self._pong_received.set()

    def _run(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            # En Windows, recvfrom falla en un socket sin enlazar, y sin IP aún no se envía nada.
            sock.bind(("", 0))
            sock.setblocking(False)
            next_command = time.perf_counter()
            next_ping = next_command

            while not self._stop.is_set():
                now = time.perf_counter()

                if now >= next_command:
                    self._send_command(sock, self._safe_command())
                    # Tras una pausa no se manda una ráfaga para recuperar el retraso.
                    next_command = max(next_command + self._period_s, now)

                if now >= next_ping or self._ping_requested:
                    self._ping_requested = False
                    self._send(sock, protocol.encode_ping(_now_us()))
                    next_ping = now + self._ping_period_s

                self._receive(sock)
                time.sleep(LOOP_SLEEP_S)

            for _ in range(STOP_PACKETS):
                self._send_command(sock, Command(luces=self._read_command().luces))
                time.sleep(self._period_s)

    # ---------- Estado ----------
    def _is_connected(self) -> bool:
        return (
            self._telemetry is not None
            and time.monotonic() - self._telemetry_time < CONNECTED_TIMEOUT_S
        )

    def status(self) -> LinkStatus:
        with self._lock:
            connected = self._is_connected()
            telemetry = self._telemetry
            fresh_latency = time.monotonic() - self._latency_time < LATENCY_TIMEOUT_S
            return LinkStatus(
                connected=connected,
                failsafe=connected and telemetry.failsafe,
                latency_ms=self._latency_ms if fresh_latency else None,
                rssi=telemetry.rssi if connected else None,
                battery_mv=self._battery_mv,
            )

    def test_connection(self) -> float | None:
        """Pide un ping al hilo de envío y espera el pong."""
        self._pong_received.clear()
        self._ping_requested = True
        if not self._pong_received.wait(PING_TEST_TIMEOUT_S):
            return None
        with self._lock:
            return self._latency_ms
