// Copiar a config.h en esta misma carpeta y llenar los valores.
#pragma once

// Red. Debe ser la misma red que la del carrito.
#define WIFI_SSID "mi-red"
#define WIFI_PASSWORD "mi-clave"
#define HOSTNAME "camara"  // Queda como camara.local y como puerto de red en Arduino IDE.
#define OTA_PASSWORD "cambiar-esta-clave"
#define STREAM_PORT 81

// Imagen
#define FRAME_SIZE FRAMESIZE_HVGA  // 480x320. FRAMESIZE_VGA da 640x480 con menos fps.
#define JPEG_QUALITY 12  // De 4 a 63. Menor es mejor calidad y más datos.
#define MAX_FPS 25       // El video comparte el canal WiFi con los comandos del carrito.

// Según cómo quede montada la cámara. La app también puede girar la imagen sin cargar firmware.
#define FLIP_VERTICAL false
#define MIRROR_HORIZONTAL false

// Pines. -1 significa deshabilitado. Los del sensor son los de la AI Thinker y están en camara.ino.
#define FLASH_LED_PIN 4
#define STATUS_LED_PIN 33  // Activo en bajo.

// 1 imprime una línea de estado por segundo en el monitor serie.
#define DEBUG_SERIAL 0
