"""Vigila la cámara para recuperar el video después de un corte de red.

El <img> de la interfaz solo recibe datos: si el WiFi cae, la conexión muere sin aviso y se
queda en la última imagen para siempre. Este hilo pregunta a /status y pide reconectar.
"""

import json
import threading
import urllib.request
from collections.abc import Callable
from urllib.parse import SplitResult, urlsplit, urlunsplit

from carrito_pc.link import resolve_host

POLL_PERIOD_S = 2.0
STATUS_TIMEOUT_S = 1.5
IDLE_POLLS_BEFORE_RECONNECT = 2  # Al abrir el stream, la cámara tarda un momento en contarlo.


def fetch_status(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=STATUS_TIMEOUT_S) as response:
        return json.load(response)


def _with_host(parts: SplitResult, host: str) -> SplitResult:
    netloc = f"{host}:{parts.port}" if parts.port else host
    return parts._replace(netloc=netloc)


class CameraMonitor:
    def __init__(
        self,
        status_reader: Callable[[str], dict] = fetch_status,
        resolver: Callable[[str], str | None] = resolve_host,
    ) -> None:
        self._status_reader = status_reader
        self._resolver = resolver
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._url = ""
        self._ip: str | None = None
        self._must_resolve = True
        self._offline = False
        self._idle_polls = 0
        self._generation = 0

    def configure(self, config: dict) -> None:
        url = config["camara"]["url"]
        with self._lock:
            if url != self._url:
                self._url = url
                self._ip = None
                self._must_resolve = True
                self._idle_polls = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=STATUS_TIMEOUT_S + 1)

    def _run(self) -> None:
        while True:
            self.poll()
            if self._stop.wait(POLL_PERIOD_S):
                return

    def poll(self) -> None:
        with self._lock:
            url = self._url
            ip = self._ip
            must_resolve = self._must_resolve

        parts = urlsplit(url)
        if not parts.hostname:
            return

        if must_resolve:
            # La cámara puede volver con otra IP. Sin respuesta se conserva la conocida.
            ip = self._resolver(parts.hostname) or ip
        if ip is None:
            self._mark_offline(url)
            return

        status_url = urlunsplit(_with_host(parts, ip)._replace(path="/status", query="", fragment=""))
        try:
            status = self._status_reader(status_url)
        except (OSError, ValueError):
            self._mark_offline(url)
            return
        self._mark_online(url, ip, status.get("clients"))

    def _mark_offline(self, url: str) -> None:
        with self._lock:
            if url == self._url:
                self._offline = True
                self._must_resolve = True
                self._idle_polls = 0

    def _mark_online(self, url: str, ip: str, clients: int | None) -> None:
        with self._lock:
            if url != self._url:
                return
            self._ip = ip
            self._must_resolve = False

            if self._offline:
                self._offline = False
                self._idle_polls = 0
                self._generation += 1
            elif clients == 0:
                # Sin clientes, nuestra conexión ya no existe aunque la PC no lo sepa.
                self._idle_polls += 1
                if self._idle_polls >= IDLE_POLLS_BEFORE_RECONNECT:
                    self._idle_polls = 0
                    self._generation += 1
            else:
                self._idle_polls = 0

    def stream_url(self) -> str:
        """URL del video con la IP ya resuelta: Windows no siempre resuelve los nombres .local."""
        with self._lock:
            url, ip = self._url, self._ip
        return urlunsplit(_with_host(urlsplit(url), ip)) if ip else url

    def generation(self) -> int:
        """Cambia cada vez que la interfaz debe reconectar el video."""
        with self._lock:
            return self._generation
