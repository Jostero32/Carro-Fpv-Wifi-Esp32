"""Ventana de pywebview y API que usa la interfaz de pc/ui."""

import copy

from carrito_pc.camera import CameraMonitor
from carrito_pc.config import AXES, Config, load_config, save_config, ui_dir, validate_config
from carrito_pc.controls import Controls
from carrito_pc.link import Link

WINDOW_TITLE = "Carrito ESP32"
WINDOW_SIZE = (1280, 800)
WINDOW_MIN_SIZE = (960, 600)
WINDOW_BACKGROUND = "#0B0D10"


def battery_percent(battery_mv: int | None, config: Config) -> int | None:
    if battery_mv is None:
        return None
    empty_mv = config["bateria"]["mv_vacia"]
    full_mv = config["bateria"]["mv_llena"]
    percent = round((battery_mv - empty_mv) * 100 / (full_mv - empty_mv))
    return min(max(percent, 0), 100)


def build_state(config: Config, controls: Controls, link: Link, camera: CameraMonitor) -> dict:
    """Foto del estado para la interfaz. Serializable a JSON."""
    command = controls.command()
    status = controls.status()
    connection = link.status()
    return {
        "connected": connection.connected,
        "failsafe": connection.failsafe,
        "latency_ms": connection.latency_ms,
        "rssi": connection.rssi,
        "battery_mv": connection.battery_mv,
        "battery_pct": battery_percent(connection.battery_mv, config),
        "steering": command.steering,
        "throttle": command.throttle,
        "brake": command.brake,
        "buttons": {"luces": command.luces, "claxon": command.claxon, "turbo": command.turbo},
        "gear": status["gear"],
        "input_device": status["input_device"],
        "camera_url": camera.stream_url(),
        "camera_generation": camera.generation(),
        "camera_rotation": config["camara"]["rotacion"],
        "camera_mirror": config["camara"]["espejo"],
        "axes_raw": status["axes_raw"],
    }


class Api:
    """Lo que ve JavaScript. Los atributos llevan guion bajo porque pywebview recorre los
    públicos para exponerlos, y controls y link no se pueden serializar."""

    def __init__(self, config: Config, controls: Controls, link: Link, camera: CameraMonitor) -> None:
        self._config = config
        self._controls = controls
        self._link = link
        self._camera = camera
        self._window = None

    def attach_window(self, window) -> None:
        self._window = window

    def get_state(self) -> dict:
        return build_state(self._config, self._controls, self._link, self._camera)

    def set_keys(self, keys: list[str]) -> None:
        self._controls.set_keys(keys)

    def toggle_button(self, name: str) -> None:
        if name in ("luces", "claxon", "turbo"):
            self._controls.toggle_button(name)

    def get_config(self) -> Config:
        return copy.deepcopy(self._config)

    def save_config(self, data: Config) -> dict:
        error = validate_config(data)
        if error:
            return {"ok": False, "error": error}

        self._config = data
        save_config(data)
        self._controls.configure(data)
        self._link.configure(data)
        self._camera.configure(data)
        return {"ok": True}

    def start_axis_calibration(self, axis: str) -> None:
        if axis in AXES:
            self._controls.start_axis_calibration(axis)

    def finish_axis_calibration(self, axis: str) -> dict | None:
        if axis not in AXES:
            return None
        axis_range = self._controls.finish_axis_calibration(axis)
        save_config(self._config)
        return axis_range

    def capture_next_button(self, timeout_s: float = 5) -> int | None:
        return self._controls.capture_next_button(timeout_s)

    def test_connection(self) -> float | None:
        return self._link.test_connection()

    def toggle_fullscreen(self) -> None:
        self._window.toggle_fullscreen()


def run_ui() -> None:
    import webview

    config = load_config()
    controls = Controls(config)
    link = Link(controls.command)
    link.configure(config)
    camera = CameraMonitor()
    camera.configure(config)
    api = Api(config, controls, link, camera)

    window = webview.create_window(
        WINDOW_TITLE,
        url=str(ui_dir() / "index.html"),
        js_api=api,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=WINDOW_MIN_SIZE,
        background_color=WINDOW_BACKGROUND,
    )
    api.attach_window(window)

    controls.start()
    link.start()
    camera.start()
    try:
        # La ventana ocupa el hilo principal hasta cerrarse. El servidor http local hace
        # falta porque WebView2, como Chrome, bloquea los módulos ES desde file://.
        webview.start(http_server=True)
    finally:
        camera.stop()
        link.stop()
        controls.stop()
