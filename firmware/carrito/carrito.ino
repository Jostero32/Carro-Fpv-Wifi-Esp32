// Firmware del carrito RC: ESP32 DevKit V1, L298N y servo MG995.
// Recibe comandos UDP desde la PC. El protocolo está en docs/protocolo.md.

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ESPmDNS.h>
#include <ArduinoOTA.h>

#include "motors.h"
#include "protocol.h"

// Va después de los headers del core para que sus macros (IN1, ENA...) no los afecten.
#if __has_include("config.h")
#include "config.h"
#if BATTERY_PIN >= 0 && (BATTERY_PIN < 32 || BATTERY_PIN > 39)
#error "BATTERY_PIN debe estar en ADC1 (GPIO 32 a 39): ADC2 no funciona con WiFi activo"
#endif
#else
#error "Falta config.h: copia config.example.h a config.h en firmware/carrito y llena los valores"
#endif

const uint32_t RAMP_PERIOD_MS = 10;
const uint32_t SEQ_RESYNC_MS = 1000;
const uint32_t WIFI_RETRY_MS = 3000;
const uint32_t LED_FAST_MS = 100;
const uint32_t LED_SLOW_MS = 500;
const uint32_t DEBUG_PERIOD_MS = 1000;
const int BATTERY_SAMPLES = 8;

// ---------- Estado ----------
WiFiUDP udp;

bool wifiConnected = false;
bool otaStarted = false;
uint32_t lastWifiAttemptMs = 0;

// commandReceived también indica que peerIp y peerPort ya son válidos.
bool commandReceived = false;
uint16_t lastSeq = 0;
uint32_t lastCommandMs = 0;
IPAddress peerIp;
uint16_t peerPort = 0;

int targetThrottle = 0;
int currentThrottle = 0;
int steeringCommand = 0;
uint8_t brakeCommand = 0;
bool lightsOn = false;
bool safeStateActive = false;
uint32_t lastRampMs = 0;

uint32_t lastTelemetryMs = 0;
uint32_t lastDebugMs = 0;
uint32_t packetsReceived = 0;
uint32_t packetsDropped = 0;

// ---------- Conducción ----------
// Motores detenidos, rampa en 0 y servo al centro. Se usa en failsafe, sin WiFi y en OTA.
void enterSafeState() {
  targetThrottle = 0;
  currentThrottle = 0;
  steeringCommand = 0;
  brakeCommand = 0;

  if (safeStateActive) return;
  safeStateActive = true;
  stopMotors();
  applySteering(0);
}

// Rampa y failsafe del firmware anterior, más el freno.
void updateDrive(uint32_t now) {
  if (!wifiConnected || !commandReceived || now - lastCommandMs > FAILSAFE_MS) {
    enterSafeState();
    return;
  }
  safeStateActive = false;

  if (now - lastRampMs < RAMP_PERIOD_MS) return;
  lastRampMs = now;

  if (brakeCommand > 0) {
    targetThrottle = 0;
    currentThrottle = 0;
    applyBrake(brakeCommand);
    return;
  }

  if (currentThrottle < targetThrottle) {
    currentThrottle = min(currentThrottle + THROTTLE_RAMP_STEP, targetThrottle);
  } else if (currentThrottle > targetThrottle) {
    currentThrottle = max(currentThrottle - THROTTLE_RAMP_STEP, targetThrottle);
  }
  applyMotors(currentThrottle);
}

void applyCommand(const CommandPacket &command) {
  targetThrottle = constrain(command.throttle, -1000, 1000);
  steeringCommand = constrain(command.steering, -1000, 1000);
  brakeCommand = command.brake;
  applySteering(steeringCommand);

#if LIGHTS_PIN >= 0
  lightsOn = (command.buttons & BUTTON_LIGHTS) != 0;
  digitalWrite(LIGHTS_PIN, lightsOn ? HIGH : LOW);
#endif
}

// ---------- UDP ----------
bool isNewSeq(uint16_t seq, uint32_t now) {
  if (!commandReceived || now - lastCommandMs > SEQ_RESYNC_MS) return true;
  return (int16_t)(seq - lastSeq) > 0;
}

void sendToPeer(const void *data, size_t size) {
  udp.beginPacket(peerIp, peerPort);
  udp.write((const uint8_t *)data, size);
  udp.endPacket();
}

// Solo un comando aceptado renueva lastCommandMs. Si un seq viejo también lo renovara,
// una PC reiniciada con seq bajo nunca llegaría a los SEQ_RESYNC_MS y quedaría ignorada.
bool acceptCommand(const uint8_t *data, uint32_t now, CommandPacket &command) {
  CommandPacket received;
  memcpy(&received, data, sizeof(received));
  if (!isNewSeq(received.seq, now)) return false;

  command = received;
  lastSeq = received.seq;
  lastCommandMs = now;
  commandReceived = true;
  peerIp = udp.remoteIP();
  peerPort = udp.remotePort();
  return true;
}

// El pong cambia el magic, por eso su CRC se recalcula.
bool answerPing(const uint8_t *data) {
  if (!commandReceived) return false;  // Aún no se conoce a quién responder.

  PingPacket pong;
  memcpy(&pong, data, sizeof(pong));
  pong.magic = MAGIC_PONG;
  pong.crc = crc8((const uint8_t *)&pong, sizeof(pong) - 1);
  sendToPeer(&pong, sizeof(pong));
  return true;
}

// Vacía la cola UDP y aplica solo el último comando aceptado.
void receivePackets(uint32_t now) {
  CommandPacket command;
  bool hasCommand = false;
  int size;

  while ((size = udp.parsePacket()) > 0) {
    packetsReceived++;
    bool accepted = false;
    uint8_t data[sizeof(CommandPacket)];

    if ((size_t)size <= sizeof(data)) {
      udp.read(data, size);
      if (data[0] == MAGIC_COMMAND && isValidPacket(data, size, sizeof(CommandPacket))) {
        bool isNew = acceptCommand(data, now, command);
        accepted = isNew;
        hasCommand = hasCommand || isNew;
      } else if (data[0] == MAGIC_PING && isValidPacket(data, size, sizeof(PingPacket))) {
        accepted = answerPing(data);
      }
    }

    // parsePacket devuelve 0 mientras quede un paquete a medio leer, como uno muy largo.
    udp.clear();
    if (!accepted) packetsDropped++;
  }

  if (hasCommand) applyCommand(command);
}

uint16_t readBatteryMv() {
#if BATTERY_PIN >= 0
  uint32_t sum = 0;
  for (int i = 0; i < BATTERY_SAMPLES; i++) {
    sum += analogReadMilliVolts(BATTERY_PIN);
  }
  float millivolts = (float)sum / BATTERY_SAMPLES * BATTERY_DIVIDER_RATIO;
  return (uint16_t)min(millivolts, 65535.0f);
#else
  return 0;
#endif
}

void sendTelemetry(uint32_t now) {
  if (!commandReceived || now - lastTelemetryMs < TELEMETRY_PERIOD_MS) return;
  lastTelemetryMs = now;

  TelemetryPacket telemetry;
  telemetry.magic = MAGIC_TELEMETRY;
  telemetry.version = PROTOCOL_VERSION;
  telemetry.lastSeq = lastSeq;
  telemetry.rssi = (int8_t)WiFi.RSSI();
  telemetry.batteryMv = readBatteryMv();
  telemetry.throttleApplied = (int16_t)currentThrottle;
  telemetry.flags = (safeStateActive ? FLAG_FAILSAFE : 0)
                    | (brakeCommand > 0 ? FLAG_BRAKING : 0)
                    | (lightsOn ? FLAG_LIGHTS : 0);
  telemetry.crc = crc8((const uint8_t *)&telemetry, sizeof(telemetry) - 1);
  sendToPeer(&telemetry, sizeof(telemetry));
}

// ---------- WiFi y OTA ----------
void onWifiConnected() {
  udp.begin(UDP_PORT);

  // ArduinoOTA.begin() también inicia mDNS, que anuncia HOSTNAME.local y el puerto de red.
  if (!otaStarted) {
    ArduinoOTA.begin();
    otaStarted = true;
  }

#if DEBUG_SERIAL
  Serial.printf("WiFi conectado, IP %s, UDP %d\n", WiFi.localIP().toString().c_str(), UDP_PORT);
#endif
}

void updateWifi(uint32_t now) {
  bool connected = WiFi.status() == WL_CONNECTED;
  if (connected && !wifiConnected) onWifiConnected();
  wifiConnected = connected;
  if (connected) return;

  // Ya asociado y esperando IP: un reintento cortaría el DHCP en curso.
  if (!WiFi.STA.connected() && now - lastWifiAttemptMs >= WIFI_RETRY_MS) {
    lastWifiAttemptMs = now;
    WiFi.reconnect();
  }
}

// ---------- Indicadores ----------
void updateStatusLed(uint32_t now) {
#if STATUS_LED_PIN >= 0
  bool on = true;
  if (!wifiConnected) {
    on = (now / LED_FAST_MS) % 2 == 0;
  } else if (safeStateActive) {
    on = (now / LED_SLOW_MS) % 2 == 0;
  }
  digitalWrite(STATUS_LED_PIN, on ? HIGH : LOW);
#else
  (void)now;
#endif
}

#if DEBUG_SERIAL
void printDebug(uint32_t now) {
  if (now - lastDebugMs < DEBUG_PERIOD_MS) return;
  lastDebugMs = now;

  Serial.printf("rx=%lu drop=%lu throttle=%d steering=%d rssi=%d\n", (unsigned long)packetsReceived,
                (unsigned long)packetsDropped, currentThrottle, steeringCommand, wifiConnected ? (int)WiFi.RSSI() : 0);
  packetsReceived = 0;
  packetsDropped = 0;
}
#endif

// ---------- Arduino ----------
void setup() {
  // Lo primero: pines de motor en LOW y servo al centro.
  bool pwmReady = setupMotors();
  enterSafeState();

#if STATUS_LED_PIN >= 0
  pinMode(STATUS_LED_PIN, OUTPUT);
#endif
#if LIGHTS_PIN >= 0
  pinMode(LIGHTS_PIN, OUTPUT);
  digitalWrite(LIGHTS_PIN, LOW);
#endif

#if DEBUG_SERIAL
  Serial.begin(115200);
  if (!pwmReady) Serial.println("Error al configurar el PWM");
#else
  (void)pwmReady;
#endif

  ArduinoOTA.setHostname(HOSTNAME);
  ArduinoOTA.setPassword(OTA_PASSWORD);
  ArduinoOTA.onStart(enterSafeState);

  // setHostname debe ir antes de mode(). La reconexión la lleva updateWifi cada 3 s.
  WiFi.setHostname(HOSTNAME);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastWifiAttemptMs = millis();
}

void loop() {
  // handle() bloquea durante toda la subida OTA. Si falla, el tiempo sin comandos ya
  // superó FAILSAFE_MS y el carrito sigue detenido. Por eso now se toma después.
  if (wifiConnected) ArduinoOTA.handle();

  uint32_t now = millis();
  updateWifi(now);

  if (wifiConnected) {
    receivePackets(now);
    sendTelemetry(now);
  }

  updateDrive(now);
  updateStatusLed(now);

#if DEBUG_SERIAL
  printDebug(now);
#endif

  delay(1);
}
