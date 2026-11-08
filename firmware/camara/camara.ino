// Firmware de la cámara: AI Thinker ESP32-CAM con stream MJPEG por HTTP.
// Rutas: /stream, /capture, /status y /control?var=X&val=Y.

#include <WiFi.h>
#include <ESPmDNS.h>
#include <ArduinoOTA.h>
#include <esp_camera.h>
#include <esp_http_server.h>
#include <lwip/sockets.h>


///
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// Va después de los headers del core para que sus macros no los afecten.
#if __has_include("config.h")
#include "config.h"
#else
#error "Falta config.h: copia config.example.h a config.h en firmware/camara y llena los valores"
#endif

static_assert(FRAME_SIZE <= FRAMESIZE_UXGA, "FRAME_SIZE supera lo que da el OV2640");
static_assert(JPEG_QUALITY >= 4 && JPEG_QUALITY <= 63, "JPEG_QUALITY debe ir de 4 a 63");
static_assert(MAX_FPS > 0 && MAX_FPS <= 60, "MAX_FPS debe ir de 1 a 60");

// ---------- Pines de la AI Thinker ----------
const int PIN_PWDN = 32;
const int PIN_RESET = -1;
const int PIN_XCLK = 0;
const int PIN_SIOD = 26;
const int PIN_SIOC = 27;
const int PIN_D7 = 35;
const int PIN_D6 = 34;
const int PIN_D5 = 39;
const int PIN_D4 = 36;
const int PIN_D3 = 21;
const int PIN_D2 = 19;
const int PIN_D1 = 18;
const int PIN_D0 = 5;
const int PIN_VSYNC = 25;
const int PIN_HREF = 23;
const int PIN_PCLK = 22;

// ---------- Constantes internas ----------
const uint32_t XCLK_FREQ_HZ = 20000000;
const size_t FRAME_BUFFER_COUNT = 2;

// El core solo conoce sus propios pines al elegir timer. El flash toma el timer 0 y la
// cámara debe usar otro: si compartieran, el PWM del flash cambiaría el reloj del sensor.
const ledc_timer_t CAMERA_LEDC_TIMER = LEDC_TIMER_1;
const ledc_channel_t CAMERA_LEDC_CHANNEL = LEDC_CHANNEL_1;
const uint8_t FLASH_CHANNEL = 2;
const uint32_t FLASH_PWM_FREQ = 5000;
const uint8_t FLASH_PWM_BITS = 8;

const int QUALITY_MIN = 4;  // Por debajo los JPEG pueden no caber en el frame buffer.
const int QUALITY_MAX = 63;

const uint32_t FRAME_PERIOD_MS = 1000 / MAX_FPS;
const uint32_t CAMERA_RETRY_MS = 5000;
const uint32_t WIFI_RETRY_MS = 3000;
const uint32_t OTA_STOP_TIMEOUT_MS = 3000;
const uint32_t STATS_PERIOD_MS = 1000;
const uint32_t LED_FAST_MS = 100;
const uint32_t LED_SLOW_MS = 500;
const uint16_t SEND_TIMEOUT_S = 2;
const size_t HTTP_STACK_SIZE = 6144;
const uint32_t STREAM_STACK_SIZE = 6144;

#define STREAM_BOUNDARY "frame"
const char *STATUS_400 = "400 Bad Request";
const char *STATUS_404 = "404 Not Found";
const char *STATUS_405 = "405 Method Not Allowed";
const char *STATUS_503 = "503 Service Unavailable";
const char *TYPE_TEXT = "text/plain; charset=utf-8";
const char *TYPE_JSON = "application/json";

// ---------- Estado ----------
httpd_handle_t server = nullptr;
TaskHandle_t streamTask = nullptr;
httpd_req_t *volatile streamRequest = nullptr;

// Se comparten entre loop, el servidor HTTP y la tarea del stream.
volatile bool cameraReady = false;
volatile bool streamActive = false;
volatile bool stopStream = false;
volatile bool captureBusy = false;
volatile bool otaActive = false;
volatile uint32_t framesSent = 0;
volatile uint32_t bytesSent = 0;

bool cameraStarted = false;
esp_err_t cameraError = ESP_OK;
uint32_t lastCameraAttemptMs = 0;

framesize_t currentFrameSize = FRAME_SIZE;
int currentQuality = JPEG_QUALITY;
bool currentVFlip = FLIP_VERTICAL;
bool currentMirror = MIRROR_HORIZONTAL;
int currentFlash = 0;

bool wifiConnected = false;
bool otaStarted = false;
uint32_t lastWifiAttemptMs = 0;

float currentFps = 0;
float currentFrameKb = 0;
uint32_t lastStatsMs = 0;

// ---------- Cámara ----------
void setFlash(int duty) {
  currentFlash = duty;
#if FLASH_LED_PIN >= 0
  ledcWrite(FLASH_LED_PIN, duty);
#endif
}

void applySensorSettings(sensor_t *sensor) {
  sensor->set_framesize(sensor, currentFrameSize);
  sensor->set_quality(sensor, currentQuality);
  sensor->set_vflip(sensor, currentVFlip);
  sensor->set_hmirror(sensor, currentMirror);
}

void startCamera() {
  if (cameraStarted) {
    esp_camera_deinit();
    cameraStarted = false;
  }

  camera_config_t config = {};
  config.pin_pwdn = PIN_PWDN;
  config.pin_reset = PIN_RESET;
  config.pin_xclk = PIN_XCLK;
  config.pin_sccb_sda = PIN_SIOD;
  config.pin_sccb_scl = PIN_SIOC;
  config.pin_d7 = PIN_D7;
  config.pin_d6 = PIN_D6;
  config.pin_d5 = PIN_D5;
  config.pin_d4 = PIN_D4;
  config.pin_d3 = PIN_D3;
  config.pin_d2 = PIN_D2;
  config.pin_d1 = PIN_D1;
  config.pin_d0 = PIN_D0;
  config.pin_vsync = PIN_VSYNC;
  config.pin_href = PIN_HREF;
  config.pin_pclk = PIN_PCLK;
  config.xclk_freq_hz = XCLK_FREQ_HZ;
  config.ledc_timer = CAMERA_LEDC_TIMER;
  config.ledc_channel = CAMERA_LEDC_CHANNEL;
  config.pixel_format = PIXFORMAT_JPEG;
  // Los buffers se reservan según este tamaño. Se inicia en el máximo para que /control
  // pueda subir la resolución después sin que los JPEG queden cortados.
  config.frame_size = FRAMESIZE_UXGA;
  config.jpeg_quality = currentQuality;
  config.fb_count = FRAME_BUFFER_COUNT;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_LATEST;

  cameraError = esp_camera_init(&config);
  if (cameraError != ESP_OK) {
    Serial.printf("La cámara no inició: %s. Reintento en %lu s\n", esp_err_to_name(cameraError),
                  (unsigned long)(CAMERA_RETRY_MS / 1000));
    return;
  }

  cameraStarted = true;
  applySensorSettings(esp_camera_sensor_get());
  cameraReady = true;
  Serial.println("Cámara lista");
}

void updateCamera(uint32_t now) {
  if (cameraReady || otaActive || now - lastCameraAttemptMs < CAMERA_RETRY_MS) return;
  lastCameraAttemptMs = now;
  startCamera();
}

// ---------- Stream ----------
void addCommonHeaders(httpd_req_t *req) {
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
}

esp_err_t sendFrame(httpd_req_t *req, const camera_fb_t *frame) {
  char partHeader[96];
  int length = snprintf(partHeader, sizeof(partHeader),
                        "\r\n--" STREAM_BOUNDARY "\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                        (unsigned)frame->len);
  esp_err_t result = httpd_resp_send_chunk(req, partHeader, length);
  if (result != ESP_OK) return result;
  return httpd_resp_send_chunk(req, (const char *)frame->buf, frame->len);
}

void runStream(httpd_req_t *req) {
  httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=" STREAM_BOUNDARY);
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  addCommonHeaders(req);

  // Sin esto, TCP retiene el final de muchos frames hasta recibir el ACK anterior.
  int noDelay = 1;
  setsockopt(httpd_req_to_sockfd(req), IPPROTO_TCP, TCP_NODELAY, &noDelay, sizeof(noDelay));

  while (!stopStream) {
    uint32_t frameStartMs = millis();

    camera_fb_t *frame = esp_camera_fb_get();
    if (frame) {
      size_t frameSize = frame->len;
      esp_err_t result = sendFrame(req, frame);
      esp_camera_fb_return(frame);
      if (result != ESP_OK) return;  // El cliente se fue.
      framesSent = framesSent + 1;
      bytesSent = bytesSent + frameSize;
    }

    // Limita los FPS para no saturar el canal que usa también el carrito.
    uint32_t elapsedMs = millis() - frameStartMs;
    delay(elapsedMs < FRAME_PERIOD_MS ? FRAME_PERIOD_MS - elapsedMs : 1);
  }

  httpd_resp_send_chunk(req, nullptr, 0);
}

// esp_http_server atiende todo en un solo hilo. El stream corre aquí para que /status,
// /control y el 503 del segundo cliente sigan respondiendo mientras alguien mira el video.
void streamTaskMain(void *) {
  for (;;) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    runStream(streamRequest);
    httpd_req_async_handler_complete(streamRequest);
    streamRequest = nullptr;
    streamActive = false;
  }
}

// ---------- Respuestas HTTP ----------
esp_err_t sendResponse(httpd_req_t *req, const char *status, const char *type, const char *body) {
  httpd_resp_set_status(req, status);
  httpd_resp_set_type(req, type);
  addCommonHeaders(req);
  return httpd_resp_sendstr(req, body);
}

esp_err_t sendText(httpd_req_t *req, const char *status, const char *body) {
  return sendResponse(req, status, TYPE_TEXT, body);
}

esp_err_t handleNotFound(httpd_req_t *req, httpd_err_code_t) {
  return sendText(req, STATUS_404, "Ruta no encontrada. Usa /stream, /capture, /status o /control.");
}

esp_err_t handleBadMethod(httpd_req_t *req, httpd_err_code_t) {
  return sendText(req, STATUS_405, "Método no permitido. Usa GET.");
}

esp_err_t handleStream(httpd_req_t *req) {
  if (otaActive) return sendText(req, STATUS_503, "Actualización OTA en curso.");
  if (!cameraReady) return sendText(req, STATUS_503, "La cámara no está lista. Revisa /status.");
  if (streamActive) {
    return sendText(req, STATUS_503, "Ya hay un cliente viendo el stream. Solo se permite uno a la vez.");
  }

  httpd_req_t *asyncRequest = nullptr;
  if (httpd_req_async_handler_begin(req, &asyncRequest) != ESP_OK) {
    return sendText(req, STATUS_503, "No se pudo iniciar el stream.");
  }

  streamActive = true;
  streamRequest = asyncRequest;
  xTaskNotifyGive(streamTask);
  return ESP_OK;
}

esp_err_t handleCapture(httpd_req_t *req) {
  captureBusy = true;  // Antes de revisar cameraReady: OTA espera este flag para apagar la cámara.

  camera_fb_t *frame = cameraReady ? esp_camera_fb_get() : nullptr;
  if (!frame) {
    captureBusy = false;
    return sendText(req, STATUS_503, "La cámara no está lista. Revisa /status.");
  }

  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  addCommonHeaders(req);
  esp_err_t result = httpd_resp_send(req, (const char *)frame->buf, frame->len);
  esp_camera_fb_return(frame);
  captureBusy = false;
  return result;
}

esp_err_t handleStatus(httpd_req_t *req) {
  char body[320];
  const resolution_info_t &size = resolution[currentFrameSize];
  snprintf(body, sizeof(body),
           "{\"camera_ready\":%s,\"camera_error\":\"%s\",\"fps\":%.1f,\"frame_kb\":%.1f,\"resolution\":\"%ux%u\","
           "\"framesize\":%d,\"quality\":%d,\"vflip\":%d,\"hmirror\":%d,\"flash\":%d,"
           "\"rssi\":%d,\"clients\":%d,\"uptime_s\":%lu}",
           cameraReady ? "true" : "false", cameraReady ? "" : esp_err_to_name(cameraError), currentFps,
           currentFrameKb, size.width, size.height, (int)currentFrameSize, currentQuality, currentVFlip,
           currentMirror, currentFlash, (int)WiFi.RSSI(), streamActive ? 1 : 0, (unsigned long)(millis() / 1000));
  return sendResponse(req, HTTPD_200, TYPE_JSON, body);
}

// Lee un parámetro entero de la URL. Rechaza texto como "12x" en vez de tomarlo como 12.
bool readIntParam(const char *query, const char *key, int &value) {
  char text[12];
  if (httpd_query_key_value(query, key, text, sizeof(text)) != ESP_OK || text[0] == '\0') return false;

  char *end = nullptr;
  long parsed = strtol(text, &end, 10);
  if (*end != '\0') return false;
  value = (int)parsed;
  return true;
}

esp_err_t handleControl(httpd_req_t *req) {
  char query[48];
  char variable[16];
  int value = 0;

  if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK
      || httpd_query_key_value(query, "var", variable, sizeof(variable)) != ESP_OK
      || !readIntParam(query, "val", value)) {
    return sendText(req, STATUS_400, "Usa /control?var=X&val=Y con Y entero.");
  }

  if (strcmp(variable, "flash") == 0) {
#if FLASH_LED_PIN < 0
    return sendText(req, STATUS_400, "El flash está deshabilitado en config.h.");
#else
    if (value < 0 || value > 255) return sendText(req, STATUS_400, "flash va de 0 a 255.");
    setFlash(value);
    return sendText(req, HTTPD_200, "OK");
#endif
  }

  sensor_t *sensor = cameraReady ? esp_camera_sensor_get() : nullptr;

  if (strcmp(variable, "framesize") == 0) {
    if (value < 0 || value > FRAMESIZE_UXGA) return sendText(req, STATUS_400, "framesize va de 0 a 15.");
    if (!sensor) return sendText(req, STATUS_503, "La cámara no está lista.");
    currentFrameSize = (framesize_t)value;
    sensor->set_framesize(sensor, currentFrameSize);
  } else if (strcmp(variable, "quality") == 0) {
    if (value < QUALITY_MIN || value > QUALITY_MAX) return sendText(req, STATUS_400, "quality va de 4 a 63.");
    if (!sensor) return sendText(req, STATUS_503, "La cámara no está lista.");
    currentQuality = value;
    sensor->set_quality(sensor, currentQuality);
  } else if (strcmp(variable, "vflip") == 0 || strcmp(variable, "hmirror") == 0) {
    if (value != 0 && value != 1) return sendText(req, STATUS_400, "vflip y hmirror son 0 o 1.");
    if (!sensor) return sendText(req, STATUS_503, "La cámara no está lista.");
    if (variable[0] == 'v') {
      currentVFlip = value;
      sensor->set_vflip(sensor, value);
    } else {
      currentMirror = value;
      sensor->set_hmirror(sensor, value);
    }
  } else {
    return sendText(req, STATUS_400, "var debe ser framesize, quality, vflip, hmirror o flash.");
  }

  return sendText(req, HTTPD_200, "OK");
}

void registerRoute(const char *uri, esp_err_t (*handler)(httpd_req_t *)) {
  httpd_uri_t route = {};
  route.uri = uri;
  route.method = HTTP_GET;
  route.handler = handler;
  httpd_register_uri_handler(server, &route);
}

void startServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = STREAM_PORT;
  config.stack_size = HTTP_STACK_SIZE;
  config.send_wait_timeout = SEND_TIMEOUT_S;  // Un cliente colgado libera el stream pronto.
  config.lru_purge_enable = true;

  if (httpd_start(&server, &config) != ESP_OK) {
    server = nullptr;
    Serial.println("No se pudo iniciar el servidor HTTP");
    return;
  }

  registerRoute("/stream", handleStream);
  registerRoute("/capture", handleCapture);
  registerRoute("/status", handleStatus);
  registerRoute("/control", handleControl);
  httpd_register_err_handler(server, HTTPD_404_NOT_FOUND, handleNotFound);
  httpd_register_err_handler(server, HTTPD_405_METHOD_NOT_ALLOWED, handleBadMethod);
}

// ---------- OTA ----------
// Corta el stream y apaga la cámara para liberar el canal y evitar que su DMA interfiera con
// la escritura en flash. Si la subida falla, updateCamera la vuelve a iniciar.
void onOtaStart() {
  otaActive = true;
  cameraReady = false;
  stopStream = true;
  setFlash(0);

  uint32_t startMs = millis();
  while ((streamActive || captureBusy) && millis() - startMs < OTA_STOP_TIMEOUT_MS) {
    delay(10);
  }

  if (cameraStarted && !streamActive && !captureBusy) {
    esp_camera_deinit();
    cameraStarted = false;
  }
}

// ---------- WiFi ----------
void onWifiConnected() {
  // ArduinoOTA.begin() también inicia mDNS, que anuncia HOSTNAME.local y el puerto de red.
  if (!otaStarted) {
    ArduinoOTA.begin();
    otaStarted = true;
  }
  if (!server) startServer();

  Serial.printf("WiFi conectado: http://%s:%d/stream\n", WiFi.localIP().toString().c_str(), STREAM_PORT);
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
  } else if (!streamActive) {
    on = (now / LED_SLOW_MS) % 2 == 0;
  }
  digitalWrite(STATUS_LED_PIN, on ? LOW : HIGH);  // Activo en bajo.
#else
  (void)now;
#endif
}

void updateStats(uint32_t now) {
  uint32_t elapsedMs = now - lastStatsMs;
  if (elapsedMs < STATS_PERIOD_MS) return;
  lastStatsMs = now;

  uint32_t frames = framesSent;
  uint32_t bytes = bytesSent;
  framesSent = 0;
  bytesSent = 0;
  currentFps = frames * 1000.0f / elapsedMs;
  currentFrameKb = frames > 0 ? bytes / 1024.0f / frames : 0;

#if DEBUG_SERIAL
  Serial.printf("fps=%.1f frame=%.1fKB rssi=%d\n", currentFps, currentFrameKb, wifiConnected ? (int)WiFi.RSSI() : 0);
#endif
}

// ---------- Arduino ----------
void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
  Serial.begin(115200);

#if STATUS_LED_PIN >= 0
  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, HIGH);
#endif
#if FLASH_LED_PIN >= 0
  // Antes que la cámara: así el flash queda en el timer 0, libre de su reloj.
  ledcAttachChannel(FLASH_LED_PIN, FLASH_PWM_FREQ, FLASH_PWM_BITS, FLASH_CHANNEL);
  setFlash(0);
#endif

  xTaskCreatePinnedToCore(streamTaskMain, "stream", STREAM_STACK_SIZE, nullptr, 1, &streamTask, 1);

  lastCameraAttemptMs = millis();
  startCamera();

  ArduinoOTA.setHostname(HOSTNAME);
  ArduinoOTA.setPassword(OTA_PASSWORD);
  ArduinoOTA.onStart(onOtaStart);
  // Sin esto, un fallo al iniciar la subida no deja rastro: el PC solo ve "No response".
  ArduinoOTA.onError([](ota_error_t error) {
    Serial.printf("OTA falló (%u): %s\n", (unsigned)error, Update.errorString());
  });

  // setHostname debe ir antes de mode(). La reconexión la lleva updateWifi cada 3 s.
  WiFi.setHostname(HOSTNAME);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastWifiAttemptMs = millis();
}

void loop() {
  // handle() bloquea durante toda la subida. Si vuelve, la subida falló o no hubo ninguna.
  if (wifiConnected) ArduinoOTA.handle();
  if (otaActive) {
    otaActive = false;
    stopStream = false;
  }

  uint32_t now = millis();
  updateWifi(now);
  updateCamera(now);
  updateStats(now);
  updateStatusLed(now);

  delay(5);
}
