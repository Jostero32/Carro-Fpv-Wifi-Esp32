"""Paquetes UDP del carrito. La especificación está en docs/protocolo.md."""

import struct
from dataclasses import dataclass

MAGIC_COMMAND = 0xC4
MAGIC_TELEMETRY = 0xC5
MAGIC_PING = 0xC6
MAGIC_PONG = 0xC7
PROTOCOL_VERSION = 1

COMMAND = struct.Struct("<BBHhhBHB")
PING = struct.Struct("<BBIB")
TELEMETRY = struct.Struct("<BBHbHhBB")

BUTTON_LIGHTS = 1 << 0
BUTTON_HORN = 1 << 1
BUTTON_TURBO = 1 << 2

FLAG_FAILSAFE = 1 << 0
FLAG_BRAKING = 1 << 1
FLAG_LIGHTS = 1 << 2


@dataclass(frozen=True)
class Telemetry:
    last_seq: int
    rssi: int
    battery_mv: int
    throttle_applied: int
    flags: int

    @property
    def failsafe(self) -> bool:
        return bool(self.flags & FLAG_FAILSAFE)


@dataclass(frozen=True)
class Pong:
    timestamp_us: int


def crc8(data: bytes) -> int:
    """CRC-8 con polinomio 0x07 y valor inicial 0x00."""
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _seal(packet: struct.Struct, *fields: int) -> bytes:
    body = packet.pack(*fields, 0)[:-1]
    return body + bytes([crc8(body)])


def encode_command(seq: int, steering: int, throttle: int, brake: int, buttons: int) -> bytes:
    return _seal(COMMAND, MAGIC_COMMAND, PROTOCOL_VERSION, seq, steering, throttle, brake, buttons)


def encode_ping(timestamp_us: int) -> bytes:
    return _seal(PING, MAGIC_PING, PROTOCOL_VERSION, timestamp_us)


def decode_packet(data: bytes) -> Telemetry | Pong | None:
    """Devuelve el paquete recibido, o None si no es válido."""
    if not data:
        return None

    if data[0] == MAGIC_TELEMETRY and len(data) == TELEMETRY.size:
        fields = TELEMETRY.unpack(data)
    elif data[0] == MAGIC_PONG and len(data) == PING.size:
        fields = PING.unpack(data)
    else:
        return None

    if fields[1] != PROTOCOL_VERSION or crc8(data[:-1]) != data[-1]:
        return None

    if data[0] == MAGIC_PONG:
        return Pong(timestamp_us=fields[2])
    return Telemetry(*fields[2:7])
