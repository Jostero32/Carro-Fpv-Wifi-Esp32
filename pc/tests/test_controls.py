import copy
import threading

import pytest

from carrito_pc.config import DEFAULTS
from carrito_pc.controls import Controls, apply_deadzone, pick_joystick, shape_pedal, shape_steering


class FakeSource:
    name = "Volante de prueba"

    def __init__(self):
        self.values = [0.0] * 6
        self.changes = []

    def set_device(self, device):
        pass

    def poll(self):
        changes, self.changes = self.changes, []
        return changes

    def axes(self):
        return list(self.values)

    def close(self):
        pass


def linear_config():
    config = copy.deepcopy(DEFAULTS)
    config["seguridad"]["limite_velocidad"] = 100
    for axis in config["ejes"].values():
        axis.update(zona_muerta=0, curva="lineal")
    return config


@pytest.fixture
def setup():
    config = linear_config()
    source = FakeSource()
    controls = Controls(config, source_factory=lambda: source)
    controls.open_source()
    controls.tick()
    return controls, source, config


def axis_config(**changes):
    return {"invertir": False, "zona_muerta": 0, "curva": "lineal", "min": -1.0, "max": 1.0, **changes}


def test_deadzone_rescales():
    assert apply_deadzone(0.1, 20) == 0
    assert apply_deadzone(0.6, 20) == pytest.approx(0.5)
    assert apply_deadzone(1.0, 20) == 1


def test_steering_uses_calibrated_range():
    axis = axis_config(min=-0.5, max=0.5)
    assert shape_steering(-0.5, axis) == -1
    assert shape_steering(0.0, axis) == 0
    assert shape_steering(0.9, axis) == 1


def test_steering_invert_deadzone_and_curve():
    assert shape_steering(0.5, axis_config(invertir=True)) == pytest.approx(-0.5)
    assert shape_steering(0.02, axis_config(zona_muerta=3)) == 0
    assert shape_steering(0.5, axis_config(curva="suave")) == pytest.approx(0.5**1.5)
    assert shape_steering(-0.5, axis_config(curva="fuerte")) == pytest.approx(-(0.5**2.5))


def test_pedal_handles_inverted_rest_position():
    assert shape_pedal(-1.0, axis_config()) == 0
    assert shape_pedal(1.0, axis_config()) == 1
    assert shape_pedal(1.0, axis_config(invertir=True)) == 0
    assert shape_pedal(-1.0, axis_config(invertir=True)) == 1


def test_pick_joystick():
    names = ["Xbox Wireless Controller", "Logitech G29"]
    assert pick_joystick(names, "volante") == 1
    assert pick_joystick(names, "xbox") == 0
    assert pick_joystick(names, "teclado") is None
    assert pick_joystick([], "volante") is None
    assert pick_joystick(["Logitech G29"], "xbox") == 0


def test_pedal_at_zero_before_first_event_does_not_accelerate(setup):
    controls, source, _ = setup
    # SDL reporta 0 al arrancar, que en un rango de -1 a 1 sería medio pedal.
    controls.tick()
    assert controls.command().throttle == 0

    source.values[2] = 0.5
    controls.tick()
    assert controls.command().throttle == 750


def test_speed_limit_is_applied(setup):
    controls, source, config = setup
    config["seguridad"]["limite_velocidad"] = 70
    source.values[2] = 1.0
    controls.tick()
    assert controls.command().throttle == 700


def test_keyboard(setup):
    controls, _, _ = setup
    controls.set_keys(["ArrowUp", "KeyA", "Space"])
    controls.tick()
    command = controls.command()
    assert (command.steering, command.throttle, command.brake) == (-1000, 1000, 255)

    controls.set_keys(["KeyS", "KeyD"])
    controls.tick()
    command = controls.command()
    assert (command.steering, command.throttle, command.brake) == (1000, -1000, 0)
    assert controls.status()["gear"] == "R"

    controls.set_keys([])
    controls.tick()
    assert controls.command().throttle == 0
    assert controls.status()["gear"] == "R"


def test_opposite_keys_cancel(setup):
    controls, _, _ = setup
    controls.set_keys(["KeyW", "KeyS", "KeyA", "KeyD"])
    controls.tick()
    assert (controls.command().steering, controls.command().throttle) == (0, 0)


def test_lights_key_toggles_once_per_press(setup):
    controls, _, _ = setup
    controls.set_keys(["KeyL"])
    controls.set_keys(["KeyL", "KeyW"])
    controls.tick()
    assert controls.command().luces

    controls.set_keys([])
    controls.set_keys(["KeyL"])
    controls.tick()
    assert not controls.command().luces


def test_toggle_button_from_ui(setup):
    controls, _, _ = setup
    controls.toggle_button("turbo")
    controls.tick()
    assert controls.command().turbo
    controls.toggle_button("turbo")
    controls.tick()
    assert not controls.command().turbo


def test_joystick_buttons(setup):
    controls, source, _ = setup
    source.changes = [(4, True), (4, False), (5, True)]
    controls.tick()
    command = controls.command()
    assert command.luces and command.claxon

    source.changes = [(5, False)]
    controls.tick()
    assert not controls.command().claxon


def test_reverse_is_held(setup):
    controls, source, _ = setup
    source.values[2] = 1.0
    source.changes = [(7, True)]
    controls.tick()
    assert controls.command().throttle == -1000

    source.changes = [(7, False)]
    controls.tick()
    assert controls.command().throttle == 1000


@pytest.fixture
def pedals():
    """Control con los gatillos en reposo y un reloj manual."""
    source = FakeSource()
    source.values[2] = source.values[3] = -1.0
    clock = [0.0]
    controls = Controls(linear_config(), source_factory=lambda: source, clock=lambda: clock[0])
    controls.open_source()
    controls.tick()

    def run_for(seconds):
        for _ in range(round(seconds / 0.01)):
            clock[0] += 0.01
            controls.tick()
        return controls.command()

    return controls, source, run_for


def test_brake_pedal_stops_the_car_before_reversing(pedals):
    controls, source, run_for = pedals
    source.values[2] = 1.0
    assert run_for(0.5).throttle == 1000

    source.values[2] = -1.0
    source.values[3] = 1.0
    command = run_for(0.38)
    assert (command.throttle, command.brake) == (0, 255)

    command = run_for(0.04)
    assert (command.throttle, command.brake) == (-1000, 0)
    assert controls.status()["gear"] == "R"


def test_light_brake_takes_longer_before_reversing(pedals):
    _, source, run_for = pedals
    source.values[2] = 1.0
    run_for(0.1)

    source.values[2] = -1.0
    source.values[3] = 0.0
    command = run_for(0.5)
    assert (command.throttle, command.brake) == (0, 128)

    command = run_for(0.2)
    assert (command.throttle, command.brake) == (-500, 0)


def test_brake_pedal_reverses_at_once_from_standstill(pedals):
    _, source, run_for = pedals
    source.values[3] = 1.0
    command = run_for(0.01)
    assert (command.throttle, command.brake) == (-1000, 0)


def test_brake_pedal_reverses_at_once_after_coasting_to_a_stop(pedals):
    _, source, run_for = pedals
    source.values[2] = 1.0
    run_for(0.1)
    source.values[2] = -1.0
    run_for(1.6)

    source.values[3] = 1.0
    assert run_for(0.01).throttle == -1000


def test_both_pedals_brake_and_never_reverse(pedals):
    _, source, run_for = pedals
    source.values[2] = source.values[3] = 1.0
    command = run_for(3)
    assert (command.throttle, command.brake) == (1000, 255)


def test_releasing_brake_pedal_stops_reversing(pedals):
    _, source, run_for = pedals
    source.values[3] = 1.0
    run_for(0.1)
    source.values[3] = -1.0
    command = run_for(0.01)
    assert (command.throttle, command.brake) == (0, 0)


def test_speed_limit_applies_to_brake_pedal_reverse(pedals):
    controls, source, run_for = pedals
    controls._config["seguridad"]["limite_velocidad"] = 70
    source.values[3] = 1.0
    assert run_for(0.01).throttle == -700


def test_axis_calibration_saves_range_and_does_not_move_the_car(setup):
    controls, source, config = setup
    controls.start_axis_calibration("acelerador")
    for value in (-0.9, 1.0, 0.8):
        source.values[2] = value
        controls.tick()
        assert controls.command().throttle == 0

    assert controls.finish_axis_calibration("acelerador") == {"min": -0.9, "max": 1.0}
    assert (config["ejes"]["acelerador"]["min"], config["ejes"]["acelerador"]["max"]) == (-0.9, 1.0)
    controls.tick()
    assert controls.command().throttle > 0


def test_calibration_without_movement_keeps_range(setup):
    controls, _, config = setup
    controls.start_axis_calibration("freno")
    controls.tick()
    assert controls.finish_axis_calibration("freno") == {"min": -1.0, "max": 1.0}
    assert config["ejes"]["freno"]["max"] == 1.0


def run_capture(controls, timeout_s):
    result = []
    thread = threading.Thread(target=lambda: result.append(controls.capture_next_button(timeout_s)))
    thread.start()
    return thread, result


def test_capture_next_button(setup):
    controls, source, _ = setup
    thread, result = run_capture(controls, 2)
    while controls._capture_done is None:
        pass

    source.changes = [(9, True)]
    controls.tick()
    thread.join()
    assert result == [9]
    # Capturar no debe activar la acción que ese botón ya tenga asignada.
    assert not controls.command().luces


def test_capture_times_out(setup):
    controls, _, _ = setup
    assert controls.capture_next_button(0.01) is None


def test_keyboard_only_ignores_joystick_axes(setup):
    controls, source, config = setup
    config["entrada"]["dispositivo"] = "teclado"
    source.values[0] = 1.0
    source.values[2] = 1.0
    controls.tick()
    assert (controls.command().steering, controls.command().throttle) == (0, 0)
    assert controls.status()["input_device"] == "Teclado"
    assert controls.status()["axes_raw"]["direccion"] == 1.0


def test_without_joystick_keyboard_still_drives():
    def broken_source():
        import pygame

        raise pygame.error("sin video")

    controls = Controls(linear_config(), source_factory=broken_source)
    controls.open_source()
    controls.set_keys(["KeyW"])
    controls.tick()
    assert controls.command().throttle == 1000
