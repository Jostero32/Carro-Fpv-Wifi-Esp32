"""Prueba la cámara sin dependencias externas.

Consulta /status cada segundo y muestra fps y rssi en una línea que se actualiza. Con
--guardar N descarga N imágenes de /capture para revisar el encuadre y termina.

Uso desde la carpeta pc/:
    python tools/probar_camara.py
    python tools/probar_camara.py --guardar 3
    python tools/probar_camara.py --host 192.168.1.60 --puerto 81

Ctrl+C termina.
"""

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REQUEST_TIMEOUT_S = 6
STATUS_PERIOD_S = 1.0

# Sin proxy: la cámara está en la red local aunque Windows tenga uno configurado.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(url: str) -> bytes:
    with opener.open(url, timeout=REQUEST_TIMEOUT_S) as response:
        return response.read()


def describe_error(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')}"
    return f"sin respuesta ({error})"


def resolve_host(host: str) -> str:
    # Se resuelve una sola vez: con mDNS cada resolución puede tardar.
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except socket.gaierror as error:
        sys.exit(f"No se pudo resolver {host}: {error}. Prueba con --host y la IP de la cámara.")


def format_status(status: dict) -> str:
    state = "lista" if status["camera_ready"] else f"NO LISTA ({status['camera_error']})"
    return (
        f"cámara {state} | fps={status['fps']:.1f} rssi={status['rssi']}dBm "
        f"res={status['resolution']} frame={status['frame_kb']:.1f}KB "
        f"clientes={status['clients']} uptime={status['uptime_s']}s"
    )


def watch_status(base_url: str) -> None:
    print(f"Consultando {base_url}/status. Ctrl+C termina.")
    try:
        while True:
            try:
                line = format_status(json.loads(fetch(f"{base_url}/status")))
            except (OSError, ValueError, KeyError) as error:
                line = describe_error(error)
            sys.stdout.write("\r" + line[:110].ljust(110))
            sys.stdout.flush()
            time.sleep(STATUS_PERIOD_S)
    except KeyboardInterrupt:
        print()


def save_captures(base_url: str, count: int) -> None:
    failures = 0
    for number in range(1, count + 1):
        path = Path(f"captura_{number:03d}.jpg")
        try:
            image = fetch(f"{base_url}/capture")
        except OSError as error:
            failures += 1
            print(f"{path}: {describe_error(error)}")
            continue
        path.write_bytes(image)
        print(f"{path}: {len(image) / 1024:.1f} KB")

    if failures:
        sys.exit(f"Fallaron {failures} de {count} capturas.")


def positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("debe ser mayor que 0")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prueba la cámara por HTTP.")
    parser.add_argument("--host", default="camara.local", help="IP o nombre de la cámara")
    parser.add_argument("--puerto", type=int, default=81, help="puerto HTTP de la cámara")
    parser.add_argument("--guardar", type=positive_int, metavar="N", help="descarga N imágenes y termina")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_url = f"http://{resolve_host(args.host)}:{args.puerto}"
    if args.guardar:
        save_captures(base_url, args.guardar)
    else:
        watch_status(base_url)


if __name__ == "__main__":
    main()
