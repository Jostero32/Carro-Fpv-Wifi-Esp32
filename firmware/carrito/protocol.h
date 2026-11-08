// Formato de los paquetes UDP. La especificación está en docs/protocolo.md.
#pragma once

#include <Arduino.h>

const uint8_t MAGIC_COMMAND = 0xC4;
const uint8_t MAGIC_TELEMETRY = 0xC5;
const uint8_t MAGIC_PING = 0xC6;
const uint8_t MAGIC_PONG = 0xC7;
const uint8_t PROTOCOL_VERSION = 1;

const uint16_t BUTTON_LIGHTS = 1 << 0;

const uint8_t FLAG_FAILSAFE = 1 << 0;
const uint8_t FLAG_BRAKING = 1 << 1;
const uint8_t FLAG_LIGHTS = 1 << 2;

struct __attribute__((packed)) CommandPacket {
  uint8_t magic;
  uint8_t version;
  uint16_t seq;
  int16_t steering;
  int16_t throttle;
  uint8_t brake;
  uint16_t buttons;
  uint8_t crc;
};

struct __attribute__((packed)) PingPacket {
  uint8_t magic;
  uint8_t version;
  uint32_t timestamp;
  uint8_t crc;
};

struct __attribute__((packed)) TelemetryPacket {
  uint8_t magic;
  uint8_t version;
  uint16_t lastSeq;
  int8_t rssi;
  uint16_t batteryMv;
  int16_t throttleApplied;
  uint8_t flags;
  uint8_t crc;
};

static_assert(sizeof(CommandPacket) == 12, "CommandPacket debe medir 12 bytes");
static_assert(sizeof(PingPacket) == 7, "PingPacket debe medir 7 bytes");
static_assert(sizeof(TelemetryPacket) == 11, "TelemetryPacket debe medir 11 bytes");

// CRC-8 con polinomio 0x07 y valor inicial 0x00.
uint8_t crc8(const uint8_t *data, size_t len);

// Comprueba tamaño, versión y CRC. El magic lo revisa quien decide el tipo de paquete.
bool isValidPacket(const uint8_t *data, int size, size_t expectedSize);
