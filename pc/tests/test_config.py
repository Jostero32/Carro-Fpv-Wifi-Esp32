import copy
import tomllib

import pytest

from carrito_pc import config as cfg


def test_missing_file_uses_defaults(tmp_path):
    assert cfg.load_config(tmp_path / "no-existe.toml") == cfg.DEFAULTS


def test_partial_file_keeps_other_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[carrito]\nhost = "1.2.3.4"\n\n[ejes.freno]\nindice = 5\n', encoding="utf-8")
    loaded = cfg.load_config(path)
    assert loaded["carrito"] == {"host": "1.2.3.4", "port": 4210}
    assert loaded["ejes"]["freno"]["indice"] == 5
    assert loaded["ejes"]["freno"]["curva"] == "lineal"
    assert loaded["interfaz"]["acento"] == "#FF8A3D"


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "config.toml"
    original = copy.deepcopy(cfg.DEFAULTS)
    original["carrito"]["host"] = 'con "comillas" y \\ barra'
    original["ejes"]["direccion"]["min"] = -0.93
    original["ejes"]["direccion"]["invertir"] = True
    cfg.save_config(original, path)
    assert cfg.load_config(path) == original
    assert not list(tmp_path.glob("*.tmp"))


def test_example_file_matches_defaults():
    with (cfg.app_dir() / "config.example.toml").open("rb") as file:
        assert tomllib.load(file) == cfg.DEFAULTS


def test_defaults_are_valid():
    assert cfg.validate_config(cfg.DEFAULTS) is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda c: c["carrito"].update(host=" "), "host"),
        (lambda c: c["carrito"].update(port=70000), "Puerto"),
        (lambda c: c["carrito"].update(port=True), "Puerto"),
        (lambda c: c["control"].update(command_hz=0), "Frecuencia"),
        (lambda c: c["entrada"].update(dispositivo="otro"), "Dispositivo"),
        (lambda c: c["camara"].update(rotacion=45), "Rotación"),
        (lambda c: c["camara"].update(rotacion=False), "Rotación"),
        (lambda c: c["camara"].update(espejo="si"), "Espejo"),
        (lambda c: c["seguridad"].update(limite_velocidad=5), "Límite"),
        (lambda c: c["seguridad"].update(watchdog_ms=0), "watchdog"),
        (lambda c: c["ejes"]["freno"].update(curva="otra"), "Curva"),
        (lambda c: c["ejes"]["freno"].update(zona_muerta=21), "Zona muerta"),
        (lambda c: c["ejes"]["freno"].update(min=1.0, max=1.0), "Rango"),
        (lambda c: c["bateria"].update(mv_vacia=9000), "batería"),
    ],
)
def test_rejects_invalid_values(change, message):
    config = copy.deepcopy(cfg.DEFAULTS)
    change(config)
    assert message in cfg.validate_config(config)
