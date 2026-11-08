// Copiar a config.h en esta misma carpeta y llenar los valores.
#pragma once

// Red
#define WIFI_SSID "mi-red"
#define WIFI_PASSWORD "mi-clave"
#define HOSTNAME "carrito"  // Queda como carrito.local y como puerto de red en Arduino IDE.
#define OTA_PASSWORD "cambiar-esta-clave"
#define UDP_PORT 4210

// Pines. -1 significa deshabilitado.
#define ENA 23  // PWM motor izquierdo
#define IN1 19
#define IN2 18
#define ENB 5  // PWM motor derecho
#define IN3 17
#define IN4 16
#define SERVO_PIN 15
#define STATUS_LED_PIN 2
#define LIGHTS_PIN -1
#define BATTERY_PIN -1  // Debe ser un pin de ADC1 (GPIO 32 a 39): ADC2 no funciona con WiFi.

// Pasa la lectura del ADC al voltaje real de la batería.
#define BATTERY_DIVIDER_RATIO 2.0

// Calibración
#define LEFT_MOTOR_REVERSE false
#define RIGHT_MOTOR_REVERSE false
#define SERVO_REVERSE false
#define SERVO_CENTER_US 1380
#define SERVO_RANGE_US 350

// PWM de motores
#define MOTOR_PWM_FREQ 20000
#define MOTOR_PWM_BITS 8
#define PWM_MIN 145  // PWM donde los motores apenas empiezan a girar.
#define PWM_MAX 180

// Comportamiento
#define FAILSAFE_MS 300          // Sin comando válido en este tiempo, detiene y centra.
#define THROTTLE_RAMP_STEP 100   // Unidades de acelerador por cada 10 ms.
#define TELEMETRY_PERIOD_MS 100

// 1 imprime una línea de estado por segundo en el monitor serie.
#define DEBUG_SERIAL 0
