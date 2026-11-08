"""Lectura y escritura de config.toml."""

import copy
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

AXES = ("direccion", "acelerador", "freno")
BUTTONS = ("luces", "claxon", "turbo", "reversa")
CURVES = ("lineal", "suave", "fuerte")
DEVICES = ("volante", "xbox", "teclado")
ROTATIONS = (0, 90, 180, 270)

Config = dict[str, Any]

DEFAULTS: Config = {
    "carrito": {"host": "carrito.local", "port": 4210},
    "control": {"command_hz": 50, "ping_interval_s": 1.0},
    "camara": {"url": "http://camara.local:81/stream", "rotacion": 0, "espejo": False},
    "entrada": {"dispositivo": "volante"},
    "ejes": {
        "direccion": {"indice": 0, "invertir": False, "zona_muerta": 3, "curva": "suave", "min": -1.0, "max": 1.0},
        "acelerador": {"indice": 2, "invertir": False, "zona_muerta": 2, "curva": "lineal", "min": -1.0, "max": 1.0},
        "freno": {"indice": 3, "invertir": False, "zona_muerta": 2, "curva": "lineal", "min": -1.0, "max": 1.0},
    },
    "botones": {"luces": 4, "claxon": 5, "turbo": 6, "reversa": 7},
    "seguridad": {"limite_velocidad": 70, "watchdog_ms": 250},
    "bateria": {"mv_vacia": 6400, "mv_llena": 8400},
    "interfaz": {"acento": "#FF8A3D"},
}


def app_dir() -> Path:
    """Carpeta pc/, o la del ejecutable cuando se empaqueta con PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def ui_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "ui"
    return app_dir() / "ui"


def config_path() -> Path:
    return app_dir() / "config.toml"


def _merge(base: Config, overrides: Config) -> Config:
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path | None = None) -> Config:
    """Lee config.toml. Lo que falte toma el valor por defecto."""
    path = path or config_path()
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    with path.open("rb") as file:
        return _merge(DEFAULTS, tomllib.load(file))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_config(config: Config) -> str | None:
    """Devuelve el motivo si la configuración no es válida."""
    if not str(config["carrito"]["host"]).strip():
        return "Falta el host del carrito"

    port = config["carrito"]["port"]
    if not _is_number(port) or not float(port).is_integer() or not 1 <= port <= 65535:
        return "Puerto UDP no válido"
    if not _is_number(config["control"]["command_hz"]) or config["control"]["command_hz"] <= 0:
        return "Frecuencia de envío no válida"
    if config["entrada"]["dispositivo"] not in DEVICES:
        return "Dispositivo no válido"

    camera = config["camara"]
    if not _is_number(camera["rotacion"]) or camera["rotacion"] not in ROTATIONS:
        return "Rotación de la cámara no válida"
    if not isinstance(camera["espejo"], bool):
        return "Espejo de la cámara no válido"

    limit = config["seguridad"]["limite_velocidad"]
    if not _is_number(limit) or not 10 <= limit <= 100:
        return "Límite de velocidad no válido"
    if not _is_number(config["seguridad"]["watchdog_ms"]) or config["seguridad"]["watchdog_ms"] <= 0:
        return "Timeout del watchdog no válido"

    for name in AXES:
        axis = config["ejes"][name]
        if axis["curva"] not in CURVES:
            return f"Curva de {name} no válida"
        if not _is_number(axis["zona_muerta"]) or not 0 <= axis["zona_muerta"] <= 20:
            return f"Zona muerta de {name} no válida"
        if not _is_number(axis["min"]) or not _is_number(axis["max"]) or axis["min"] >= axis["max"]:
            return f"Rango de {name} no válido"

    if config["bateria"]["mv_vacia"] >= config["bateria"]["mv_llena"]:
        return "Rango de batería no válido"
    return None


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return repr(value)


def _format_table(table: Config, prefix: str, lines: list[str]) -> None:
    values = {key: value for key, value in table.items() if not isinstance(value, dict)}
    tables = {key: value for key, value in table.items() if isinstance(value, dict)}

    if values and prefix:
        lines.append(f"[{prefix}]")
    for key, value in values.items():
        lines.append(f"{key} = {_format_value(value)}")
    if values:
        lines.append("")

    for key, value in tables.items():
        _format_table(value, f"{prefix}.{key}" if prefix else key, lines)


def dumps_config(config: Config) -> str:
    lines: list[str] = []
    _format_table(config, "", lines)
    return "\n".join(lines).rstrip() + "\n"


def save_config(config: Config, path: Path | None = None) -> None:
    """Guarda en un archivo temporal primero para no dejar config.toml a medias."""
    path = path or config_path()
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_text(dumps_config(config), encoding="utf-8")
    os.replace(temporary, path)
