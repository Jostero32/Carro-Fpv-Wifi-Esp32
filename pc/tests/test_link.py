"""Pruebas del enlace contra un carrito falso en localhost."""

import copy
import socket
import struct
import threading
import time

import pytest

from carrito_pc import protocol
from carrito_pc.config import DEFAULTS
from carrito_pc.controls import Command
from carrito_pc.link import Link, buttons_mask
from carrito_pc.ui_app import battery_percent


class FakeCar:
    """Responde como el firmware: pong y telemetría al origen del último comando."""

    def __init__(self, flags=0, battery_mv=7400):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.02)
        self.port = self.sock.getsockname()[1]
        self.flags = flags
        self.battery_mv = battery_mv
        self.silent = False
        self.commands = []
        self.pings = 0
        self.running = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def telemetry(self):
        body = struct.pack("<BBHbHhB", 0xC5, 1, 1, -60, self.battery_mv, 0, self.flags)
        return body + bytes([protocol.crc8(body)])

    def run(self):
        peer = None
        last_telemetry = 0.0
        while self.running:
            try:
                data, address = self.sock.recvfrom(64)
            except TimeoutError:
                data = None
            except OSError:
                continue

            if data and data[0] == protocol.MAGIC_COMMAND and protocol.crc8(data[:-1]) == data[-1]:
                self.commands.append(protocol.COMMAND.unpack(data))
                peer = address
            elif data and data[0] == protocol.MAGIC_PING and protocol.crc8(data[:-1]) == data[-1]:
                self.pings += 1
                if peer and not self.silent:
                    body = bytes([protocol.MAGIC_PONG]) + data[1:-1]
                    self.sock.sendto(body + bytes([protocol.crc8(body)]), peer)

            if peer and not self.silent and time.monotonic() - last_telemetry >= 0.05:
                last_telemetry = time.monotonic()
                self.sock.sendto(self.telemetry(), peer)

    def close(self):
        self.running = False
        self.thread.join(timeout=1)
        self.sock.close()


def wait_for(condition, timeout_s=2.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def make_link(car, read_command, hz=100, watchdog_ms=250):
    config = copy.deepcopy(DEFAULTS)
    config["carrito"].update(host="127.0.0.1", port=car.port)
    config["control"].update(command_hz=hz, ping_interval_s=0.1)
    config["seguridad"]["watchdog_ms"] = watchdog_ms
    link = Link(read_command)
    link.configure(config)
    return link


@pytest.fixture
def car():
    fake = FakeCar()
    yield fake
    fake.close()


def fresh_command():
    return Command(steering=100, throttle=200, brake=3, luces=True)


def test_sends_commands_and_reports_status(car):
    link = make_link(car, fresh_command)
    link.start()
    try:
        assert wait_for(lambda: link.status().connected)
        assert wait_for(lambda: link.status().latency_ms is not None)
        status = link.status()
        assert (status.rssi, status.battery_mv, status.failsafe) == (-60, 7400, False)
    finally:
        link.stop()

    seqs = [command[2] for command in car.commands]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))
    assert car.commands[0][3:7] == (100, 200, 3, protocol.BUTTON_LIGHTS)


def test_stop_sends_neutral(car):
    link = make_link(car, fresh_command)
    link.start()
    assert wait_for(lambda: len(car.commands) > 3)
    link.stop()
    assert wait_for(lambda: car.commands[-1][3:6] == (0, 0, 0))


def test_failsafe_flag(car):
    car.flags = protocol.FLAG_FAILSAFE
    link = make_link(car, fresh_command)
    link.start()
    try:
        assert wait_for(lambda: link.status().failsafe)
    finally:
        link.stop()


def test_disconnects_when_telemetry_stops(car, monkeypatch):
    monkeypatch.setattr("carrito_pc.link.CONNECTED_TIMEOUT_S", 0.2)
    link = make_link(car, fresh_command)
    link.start()
    try:
        assert wait_for(lambda: link.status().connected)
        car.silent = True
        assert wait_for(lambda: not link.status().connected)
        assert link.status().rssi is None
        car.silent = False
        assert wait_for(lambda: link.status().connected)
    finally:
        link.stop()


def test_frozen_input_sends_neutral(car):
    frozen = Command(steering=500, throttle=900, brake=0, luces=True, timestamp=time.monotonic() - 10)
    link = make_link(car, lambda: frozen)
    link.start()
    try:
        assert wait_for(lambda: len(car.commands) > 3)
    finally:
        link.stop()

    assert all(command[3:5] == (0, 0) for command in car.commands)
    assert car.commands[0][6] == protocol.BUTTON_LIGHTS


def test_test_connection(car):
    link = make_link(car, fresh_command)
    link.start()
    try:
        assert wait_for(lambda: link.status().connected)
        latency = link.test_connection()
        assert latency is not None and latency < 500

        car.silent = True
        assert link.test_connection() is None
    finally:
        link.stop()


def test_command_rate(car):
    link = make_link(car, fresh_command, hz=50)
    link.start()
    time.sleep(1.0)
    link.stop()
    assert 44 <= len(car.commands) <= 56


def test_changes_target_while_running(car):
    other = FakeCar()
    link = make_link(car, fresh_command)
    link.start()
    try:
        assert wait_for(lambda: len(car.commands) > 0)
        config = copy.deepcopy(DEFAULTS)
        config["carrito"].update(host="127.0.0.1", port=other.port)
        link.configure(config)
        assert wait_for(lambda: len(other.commands) > 0)
    finally:
        link.stop()
        other.close()


def test_buttons_mask():
    assert buttons_mask(Command(luces=True, claxon=True, turbo=True)) == 0b111
    assert buttons_mask(Command(claxon=True)) == 0b010


def test_battery_percent():
    config = copy.deepcopy(DEFAULTS)
    assert battery_percent(None, config) is None
    assert battery_percent(7400, config) == 50
    assert battery_percent(6000, config) == 0
    assert battery_percent(9000, config) == 100
