#include "protocol.h"

uint8_t crc8(const uint8_t *data, size_t len) {
  uint8_t crc = 0x00;
  for (size_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (int bit = 0; bit < 8; bit++) {
      crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
    }
  }
  return crc;
}

bool isValidPacket(const uint8_t *data, int size, size_t expectedSize) {
  return (size_t)size == expectedSize && data[1] == PROTOCOL_VERSION
         && crc8(data, expectedSize - 1) == data[expectedSize - 1];
}
