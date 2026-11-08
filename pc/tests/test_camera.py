"""Pruebas del vigilante de la cámara, sin red."""

import copy

import pytest

from carrito_pc.camera import IDLE_POLLS_BEFORE_RECONNECT, CameraMonitor
from carrito_pc.config import DEFAULTS


class FakeCamera:
    def __init__(self):
        self.status = {"clients": 1}
        self.ip = "192.168.1.106"
        self.online = True
        self.requests = []
        self.resolved = []

    def read(self, url):
        self.requests.append(url)
        if not self.online:
            raise TimeoutError("sin red")
        return self.status

    def resolve(self, host):
        self.resolved.append(host)
        return self.ip if self.online else None


def config_with(url):
    config = copy.deepcopy(DEFAULTS)
    config["camara"]["url"] = url
    return config


@pytest.fixture
def camera():
    return FakeCamera()


@pytest.fixture
def monitor(camera):
    created = CameraMonitor(status_reader=camera.read, resolver=camera.resolve)
    created.configure(config_with("http://camara.local:81/stream"))
    created.poll()
    return created


def test_stream_url_uses_resolved_ip_and_keeps_port_and_path(monitor, camera):
    assert monitor.stream_url() == "http://192.168.1.106:81/stream"
    assert camera.requests[-1] == "http://192.168.1.106:81/status"


def test_stream_url_is_the_configured_one_until_resolved():
    created = CameraMonitor(status_reader=lambda url: {}, resolver=lambda host: None)
    created.configure(config_with("http://camara.local:81/stream"))
    created.poll()
    assert created.stream_url() == "http://camara.local:81/stream"


def test_url_without_port():
    created = CameraMonitor(status_reader=lambda url: {"clients": 1}, resolver=lambda host: "10.0.0.5")
    created.configure(config_with("http://camara.local/stream?x=1"))
    created.poll()
    assert created.stream_url() == "http://10.0.0.5/stream?x=1"


def test_does_not_resolve_on_every_poll(monitor, camera):
    monitor.poll()
    monitor.poll()
    assert len(camera.resolved) == 1


def test_healthy_stream_never_reconnects(monitor):
    for _ in range(10):
        monitor.poll()
    assert monitor.generation() == 0


def test_reconnects_when_camera_reports_no_clients(monitor, camera):
    camera.status = {"clients": 0}
    for _ in range(IDLE_POLLS_BEFORE_RECONNECT - 1):
        monitor.poll()
    assert monitor.generation() == 0

    monitor.poll()
    assert monitor.generation() == 1


def test_stream_starting_up_does_not_reconnect(monitor, camera):
    camera.status = {"clients": 0}
    for _ in range(IDLE_POLLS_BEFORE_RECONNECT - 1):
        monitor.poll()
    camera.status = {"clients": 1}
    monitor.poll()
    camera.status = {"clients": 0}
    monitor.poll()
    assert monitor.generation() == 0


def test_reconnects_once_when_camera_returns(monitor, camera):
    camera.online = False
    for _ in range(5):
        monitor.poll()
    assert monitor.generation() == 0

    camera.online = True
    monitor.poll()
    monitor.poll()
    assert monitor.generation() == 1


def test_camera_returning_with_another_ip(monitor, camera):
    camera.online = False
    monitor.poll()
    camera.online = True
    camera.ip = "192.168.1.200"
    monitor.poll()
    assert monitor.stream_url() == "http://192.168.1.200:81/stream"
    assert monitor.generation() == 1


def test_keeps_known_ip_when_resolution_fails(monitor, camera):
    camera.online = False
    monitor.poll()
    assert monitor.stream_url() == "http://192.168.1.106:81/stream"


def test_status_without_clients_field_is_ignored(monitor, camera):
    camera.status = {}
    for _ in range(5):
        monitor.poll()
    assert monitor.generation() == 0


def test_invalid_json_counts_as_offline(camera):
    def broken(url):
        raise ValueError("no es json")

    created = CameraMonitor(status_reader=broken, resolver=camera.resolve)
    created.configure(config_with("http://camara.local:81/stream"))
    created.poll()
    assert created.generation() == 0


def test_changing_url_resolves_the_new_host(monitor, camera):
    monitor.configure(config_with("http://otra.local:81/stream"))
    assert monitor.stream_url() == "http://otra.local:81/stream"
    monitor.poll()
    assert camera.resolved[-1] == "otra.local"


def test_empty_url_does_nothing(camera):
    created = CameraMonitor(status_reader=camera.read, resolver=camera.resolve)
    created.configure(config_with(""))
    created.poll()
    assert camera.requests == []
    assert created.stream_url() == ""
