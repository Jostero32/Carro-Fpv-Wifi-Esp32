import struct

from carrito_pc import protocol


def test_crc8_reference_value():
    assert protocol.crc8(b"123456789") == 0xF4


def test_packet_sizes_match_the_spec():
    assert protocol.COMMAND.size == 12
    assert protocol.PING.size == 7
    assert protocol.TELEMETRY.size == 11


def test_command_fields_and_crc():
    packet = protocol.encode_command(65535, -1000, 250, 255, 0b101)
    assert struct.unpack("<BBHhhBHB", packet)[:7] == (0xC4, 1, 65535, -1000, 250, 255, 0b101)
    assert protocol.crc8(packet[:-1]) == packet[-1]


def make_telemetry(flags=0b101):
    body = struct.pack("<BBHbHhB", 0xC5, 1, 321, -55, 7400, -120, flags)
    return body + bytes([protocol.crc8(body)])


def test_decode_telemetry():
    packet = protocol.decode_packet(make_telemetry())
    assert packet == protocol.Telemetry(321, -55, 7400, -120, 0b101)
    assert packet.failsafe


def test_decode_pong():
    ping = protocol.encode_ping(0xFFFFFFFF)
    body = bytes([protocol.MAGIC_PONG]) + ping[1:-1]
    assert protocol.decode_packet(body + bytes([protocol.crc8(body)])) == protocol.Pong(0xFFFFFFFF)


def test_rejects_bad_packets():
    good = make_telemetry()
    wrong_version = good[:1] + b"\x02" + good[2:]
    assert protocol.decode_packet(b"") is None
    assert protocol.decode_packet(good[:-1]) is None
    assert protocol.decode_packet(good[:-1] + bytes([good[-1] ^ 1])) is None
    assert protocol.decode_packet(wrong_version) is None
    assert protocol.decode_packet(b"\xFF" * 11) is None
    # Un pong con el CRC del ping se rechaza: el CRC cubre el magic.
    assert protocol.decode_packet(bytes([protocol.MAGIC_PONG]) + protocol.encode_ping(1)[1:]) is None
