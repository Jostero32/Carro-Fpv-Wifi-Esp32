"""Punto de entrada: interfaz por defecto, o --consola, --listar y --calibrar."""

import argparse
import sys
import threading
import time

from carrito_pc.camera import CameraMonitor
from carrito_pc.config import AXES, config_path, load_config, save_config
from carrito_pc.controls import Controls, list_joysticks
from carrito_pc.link import Link
from carrito_pc.ui_app import build_state, run_ui

CONSOLE_PERIOD_S = 0.1
READY_WAIT_S = 0.5
SAMPLE_PERIOD_S = 0.01
AXIS_LABELS = {"direccion": "dirección", "acelerador": "acelerador", "freno": "freno"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="carrito-pc", description="Controla el carrito ESP32 por WiFi.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--consola", action="store_true", help="maneja sin interfaz y muestra el estado en una línea")
    mode.add_argument("--listar", action="store_true", help="lista los joysticks conectados")
    mode.add_argument("--calibrar", action="store_true", help="detecta y calibra los ejes en la terminal")
    return parser.parse_args()


def run_list() -> None:
    joysticks = list_joysticks()
    if not joysticks:
        print("No hay joysticks conectados.")
    for stick in joysticks:
        print(f"{stick['index']}: {stick['name']} ({stick['axes']} ejes, {stick['buttons']} botones)")


def format_status(state: dict) -> str:
    link = "conectado" if state["connected"] else "sin señal"
    if state["failsafe"]:
        link = "FAILSAFE"
    latency = "---" if state["latency_ms"] is None else f"{state['latency_ms']:.1f}ms"
    return (
        f"{link:9} rtt={latency} rssi={state['rssi']} dir={state['steering']:+5d} "
        f"acel={state['throttle']:+5d} freno={state['brake']:3d} marcha={state['gear']}"
    )


def run_console() -> None:
    config = load_config()
    controls = Controls(config)
    link = Link(controls.command)
    link.configure(config)
    # Sin interfaz no hay video que recuperar, así que no hace falta iniciar su hilo.
    camera = CameraMonitor()
    camera.configure(config)
    controls.start()
    link.start()

    print("Manejo por consola con el joystick. Ctrl+C termina.")
    try:
        while True:
            line = format_status(build_state(config, controls, link, camera))
            sys.stdout.write("\r" + line.ljust(90))
            sys.stdout.flush()
            time.sleep(CONSOLE_PERIOD_S)
    except KeyboardInterrupt:
        print()
    finally:
        link.stop()
        controls.stop()


def measure_axis(controls: Controls) -> tuple[int, float, float] | None:
    """Mide los ejes hasta que se presione Enter y devuelve el que más se movió."""
    print("Muévelo a sus dos extremos y presiona Enter.")
    entered = []
    threading.Thread(target=lambda: entered.append(input()), daemon=True).start()

    lows: list[float] = []
    highs: list[float] = []
    while not entered:
        controls.tick()
        for index, value in enumerate(controls.all_axes()):
            if index == len(lows):
                lows.append(value)
                highs.append(value)
            lows[index] = min(lows[index], value)
            highs[index] = max(highs[index], value)
        time.sleep(SAMPLE_PERIOD_S)

    if not lows:
        return None
    spans = [high - low for low, high in zip(lows, highs)]
    index = spans.index(max(spans))
    return index, lows[index], highs[index]


def run_calibration() -> None:
    config = load_config()
    controls = Controls(config)
    controls.open_source()
    time.sleep(READY_WAIT_S)
    controls.tick()

    if not controls.all_axes():
        print("No hay joystick con ejes. Revisa con --listar.")
        return

    print("Calibración sin enviar nada al carrito.")
    for name in AXES:
        print(f"\nEje de {AXIS_LABELS[name]}:")
        measured = measure_axis(controls)
        if measured is None or measured[2] - measured[1] < 0.1:
            print("  No se detectó movimiento. Se conserva lo anterior.")
            continue
        index, low, high = measured
        config["ejes"][name].update(indice=index, min=low, max=high)
        print(f"  Eje {index}, rango {low:.2f} a {high:.2f}")

    save_config(config)
    print(f"\nGuardado en {config_path()}")


def main() -> None:
    args = parse_args()
    if args.listar:
        run_list()
    elif args.consola:
        run_console()
    elif args.calibrar:
        run_calibration()
    else:
        run_ui()


if __name__ == "__main__":
    main()
