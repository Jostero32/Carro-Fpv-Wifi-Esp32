"""Lectura de volante, control y teclado en su propio hilo."""

import os
import threading
import time
from dataclasses import dataclass, field

from carrito_pc.config import AXES, Config

# Deben definirse antes de importar pygame. Sin ventana SDL con foco, SDL ignora el joystick.
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

POLL_PERIOD_S = 0.005
PEDAL_ARM_THRESHOLD = 0.02
MIN_CALIBRATED_SPAN = 0.1
MAX_CAPTURE_TIMEOUT_S = 5.0

# El carrito no tiene sensor de velocidad: se estima cuánto puede seguir rodando tras acelerar.
COAST_STOP_S = 1.5
BRAKE_STOP_S = 0.4

CURVE_EXPONENTS = {"lineal": 1.0, "suave": 1.5, "fuerte": 2.5}

KEYS_LEFT = {"ArrowLeft", "KeyA"}
KEYS_RIGHT = {"ArrowRight", "KeyD"}
KEYS_FORWARD = {"ArrowUp", "KeyW"}
KEYS_REVERSE = {"ArrowDown", "KeyS"}
KEYS_BRAKE = {"Space"}
KEYS_LIGHTS = {"KeyL"}
KEYS_HORN = {"KeyH"}
KEYS_TURBO = {"ShiftLeft", "ShiftRight"}

XBOX_NAME_HINTS = ("xbox", "x-box", "xinput", "gamepad", "controller")


@dataclass(frozen=True)
class Command:
    steering: int = 0
    throttle: int = 0
    brake: int = 0
    luces: bool = False
    claxon: bool = False
    turbo: bool = False
    timestamp: float = field(default_factory=time.monotonic)


def apply_deadzone(value: float, deadzone_pct: float) -> float:
    """value va de 0 a 1. Reescala para que el recorrido siga partiendo de 0."""
    deadzone = deadzone_pct / 100
    if value <= deadzone:
        return 0.0
    return (value - deadzone) / (1 - deadzone)


def _position(raw: float, axis: Config) -> float:
    span = axis["max"] - axis["min"]
    position = min(max((raw - axis["min"]) / span, 0.0), 1.0)
    return 1 - position if axis["invertir"] else position


def shape_steering(raw: float, axis: Config) -> float:
    """Lleva la lectura cruda a -1..1 con zona muerta y curva."""
    centered = _position(raw, axis) * 2 - 1
    magnitude = apply_deadzone(abs(centered), axis["zona_muerta"])
    shaped = magnitude ** CURVE_EXPONENTS[axis["curva"]]
    return -shaped if centered < 0 else shaped


def shape_pedal(raw: float, axis: Config) -> float:
    """Lleva la lectura cruda a 0..1 con zona muerta y curva."""
    magnitude = apply_deadzone(_position(raw, axis), axis["zona_muerta"])
    return magnitude ** CURVE_EXPONENTS[axis["curva"]]


def pick_joystick(names: list[str], device: str) -> int | None:
    """Elige el joystick según el dispositivo configurado."""
    if device == "teclado" or not names:
        return None

    for index, name in enumerate(names):
        looks_like_gamepad = any(hint in name.lower() for hint in XBOX_NAME_HINTS)
        if looks_like_gamepad == (device == "xbox"):
            return index
    return 0


class JoystickSource:
    """Acceso a pygame. Debe crearse y usarse en un solo hilo."""

    def __init__(self) -> None:
        pygame.display.init()
        pygame.joystick.init()
        self._joysticks: list[pygame.joystick.JoystickType] = []
        self._active: pygame.joystick.JoystickType | None = None
        self._device = "volante"
        self._refresh()

    def _refresh(self) -> None:
        count = pygame.joystick.get_count()
        self._joysticks = [pygame.joystick.Joystick(i) for i in range(count)]
        index = pick_joystick([stick.get_name() for stick in self._joysticks], self._device)
        # Teclado no conduce con el joystick, pero la calibración sigue viendo sus ejes.
        if index is None and self._joysticks:
            index = 0
        self._active = None if index is None else self._joysticks[index]

    def set_device(self, device: str) -> None:
        if device != self._device:
            self._device = device
            self._refresh()

    def poll(self) -> list[tuple[int, bool]]:
        """Devuelve los botones que cambiaron como (índice, presionado)."""
        changes = []
        for event in pygame.event.get():
            if event.type in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
                self._refresh()
            elif event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
                if self._active and event.instance_id == self._active.get_instance_id():
                    changes.append((event.button, event.type == pygame.JOYBUTTONDOWN))
        return changes

    @property
    def name(self) -> str | None:
        return self._active.get_name() if self._active else None

    def axes(self) -> list[float]:
        if not self._active:
            return []
        return [self._active.get_axis(i) for i in range(self._active.get_numaxes())]

    def close(self) -> None:
        pygame.quit()


def list_joysticks() -> list[dict]:
    """Datos de los joysticks conectados, para --listar."""
    pygame.display.init()
    pygame.joystick.init()
    sticks = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
    found = [
        {"index": i, "name": stick.get_name(), "axes": stick.get_numaxes(), "buttons": stick.get_numbuttons()}
        for i, stick in enumerate(sticks)
    ]
    pygame.quit()
    return found


class Controls:
    def __init__(self, config: Config, source_factory=JoystickSource, clock=time.monotonic) -> None:
        self._config = config
        self._source_factory = source_factory
        self._clock = clock
        self._source = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._keys: set[str] = set()
        self._latched = {"luces": False, "claxon": False, "turbo": False}
        self._held: set[str] = set()

        self._all_axes: list[float] = []
        self._raw = dict.fromkeys(AXES, 0.0)
        self._pedal_rest: dict[str, float] = {}
        self._device_name: str | None = None

        self._ranges: dict[str, dict[str, float]] = {}
        self._capture_done: threading.Event | None = None
        self._captured: int | None = None

        self._command = Command()
        self._gear = "D"
        self._roll_s = 0.0
        self._last_roll_update = clock()

    # ---------- Hilo ----------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="controls", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _run(self) -> None:
        self.open_source()
        while not self._stop.wait(POLL_PERIOD_S):
            self.tick()
        if self._source:
            self._source.close()

    def open_source(self) -> None:
        try:
            self._source = self._source_factory()
        except pygame.error as error:
            # Sin joystick utilizable el teclado sigue funcionando.
            print(f"No se pudo iniciar la lectura de joystick: {error}")

    def tick(self) -> None:
        changes: list[tuple[int, bool]] = []
        axes: list[float] = []
        name = None
        if self._source:
            self._source.set_device(self._config["entrada"]["dispositivo"])
            changes = self._source.poll()
            axes = self._source.axes()
            name = self._source.name

        with self._lock:
            if name != self._device_name:
                self._device_name = name
                self._held.clear()
                self._pedal_rest.clear()
            self._read_axes(axes)
            for index, pressed in changes:
                self._on_joystick_button(index, pressed)
            self._command = self._build_command()
            if self._command.throttle != 0:
                self._gear = "R" if self._command.throttle < 0 else "D"

    # ---------- Entradas ----------
    def _read_axes(self, axes: list[float]) -> None:
        self._all_axes = axes
        for name in AXES:
            index = self._config["ejes"][name]["indice"]
            raw = axes[index] if index < len(axes) else 0.0
            self._raw[name] = raw

            if name in self._ranges:
                current = self._ranges[name]
                current["min"] = min(current["min"], raw)
                current["max"] = max(current["max"], raw)

    def _on_joystick_button(self, index: int, pressed: bool) -> None:
        if self._capture_done is not None:
            if pressed:
                self._captured = index
                self._capture_done.set()
            return

        for action, assigned in self._config["botones"].items():
            if assigned != index:
                continue
            if pressed:
                self._held.add(action)
                if action == "luces":
                    self._latched["luces"] = not self._latched["luces"]
            else:
                self._held.discard(action)

    def set_keys(self, keys: list[str]) -> None:
        with self._lock:
            new_keys = set(keys)
            if KEYS_LIGHTS & (new_keys - self._keys):
                self._latched["luces"] = not self._latched["luces"]
            self._keys = new_keys

    def toggle_button(self, name: str) -> None:
        with self._lock:
            self._latched[name] = not self._latched[name]

    # ---------- Salida ----------
    def _pedal(self, name: str) -> float:
        """Un pedal no cuenta hasta moverse. SDL puede reportar 0 antes del primer evento,
        y con un rango de -1 a 1 ese 0 se leería como medio pedal al arrancar."""
        raw = self._raw[name]
        rest = self._pedal_rest.setdefault(name, raw)
        if rest is not None:
            if abs(raw - rest) < PEDAL_ARM_THRESHOLD:
                return 0.0
            self._pedal_rest[name] = None
        return shape_pedal(raw, self._config["ejes"][name])

    def _brake_or_reverse(self, throttle: float, brake: float) -> tuple[float, float]:
        """El freno detiene el carrito y, si sigue presionado, pasa a ser la reversa.
        Acelerar a la vez nunca retrocede: gana el freno, igual que en el firmware."""
        now = self._clock()
        elapsed = now - self._last_roll_update
        self._last_roll_update = now

        if self._command.throttle > 0:
            self._roll_s = COAST_STOP_S
        else:
            # Frenar a fondo agota COAST_STOP_S en BRAKE_STOP_S. Frenar suave tarda más.
            burn_rate = 1 + brake * (COAST_STOP_S / BRAKE_STOP_S - 1)
            self._roll_s = max(0.0, self._roll_s - elapsed * burn_rate)

        if throttle == 0 and brake > 0 and self._roll_s == 0:
            return -brake, 0.0
        return throttle, brake

    def _build_command(self) -> Command:
        keys = self._keys
        muted = bool(self._ranges) or self._capture_done is not None
        latched = self._latched
        lights = latched["luces"]

        # Calibrar pide llevar los ejes a sus extremos: eso no debe mover el carrito.
        if muted:
            return Command(luces=lights)

        steering = throttle = brake = 0.0
        use_joystick = self._device_name is not None and self._config["entrada"]["dispositivo"] != "teclado"
        if use_joystick:
            axes = self._config["ejes"]
            steering = shape_steering(self._raw["direccion"], axes["direccion"])
            throttle = self._pedal("acelerador")
            brake = self._pedal("freno")
            if "reversa" in self._held:
                throttle = -throttle
            throttle, brake = self._brake_or_reverse(throttle, brake)

        # Las teclas de manejo tienen prioridad sobre el eje mientras están presionadas.
        if keys & (KEYS_LEFT | KEYS_RIGHT):
            steering = bool(keys & KEYS_RIGHT) - bool(keys & KEYS_LEFT)
        if keys & (KEYS_FORWARD | KEYS_REVERSE):
            throttle = bool(keys & KEYS_FORWARD) - bool(keys & KEYS_REVERSE)
        if keys & KEYS_BRAKE:
            brake = 1.0

        limit = self._config["seguridad"]["limite_velocidad"] / 100
        return Command(
            steering=round(steering * 1000),
            throttle=round(throttle * limit * 1000),
            brake=round(brake * 255),
            luces=lights,
            claxon=latched["claxon"] or "claxon" in self._held or bool(keys & KEYS_HORN),
            turbo=latched["turbo"] or "turbo" in self._held or bool(keys & KEYS_TURBO),
        )

    def command(self) -> Command:
        with self._lock:
            return self._command

    def status(self) -> dict:
        with self._lock:
            device = self._device_name
            if self._config["entrada"]["dispositivo"] == "teclado":
                device = "Teclado"
            return {"input_device": device, "gear": self._gear, "axes_raw": dict(self._raw)}

    def all_axes(self) -> list[float]:
        with self._lock:
            return list(self._all_axes)

    # ---------- Configuración y calibración ----------
    def configure(self, config: Config) -> None:
        with self._lock:
            self._config = config

    def start_axis_calibration(self, axis: str) -> None:
        with self._lock:
            raw = self._raw[axis]
            self._ranges[axis] = {"min": raw, "max": raw}

    def finish_axis_calibration(self, axis: str) -> dict[str, float]:
        """Guarda el rango en la configuración y lo devuelve. Si el eje casi no se movió,
        conserva el rango anterior para no dejar el eje inutilizable."""
        with self._lock:
            measured = self._ranges.pop(axis, None)
            settings = self._config["ejes"][axis]
            if measured and measured["max"] - measured["min"] >= MIN_CALIBRATED_SPAN:
                settings["min"] = measured["min"]
                settings["max"] = measured["max"]
            return {"min": settings["min"], "max": settings["max"]}

    def capture_next_button(self, timeout_s: float) -> int | None:
        done = threading.Event()
        with self._lock:
            self._captured = None
            self._capture_done = done
        done.wait(min(timeout_s, MAX_CAPTURE_TIMEOUT_S))
        with self._lock:
            self._capture_done = None
            return self._captured
