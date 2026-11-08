#include "motors.h"

// La misma comprobación que en carrito.ino: este archivo puede compilarse primero.
#if __has_include("config.h")
#include "config.h"
#if PWM_MAX >= (1 << MOTOR_PWM_BITS)
#error "PWM_MAX no cabe en MOTOR_PWM_BITS"
#endif
#if PWM_MIN >= PWM_MAX
#error "PWM_MIN debe ser menor que PWM_MAX"
#endif
#else
#error "Falta config.h: copia config.example.h a config.h en firmware/carrito y llena los valores"
#endif

const uint8_t LEFT_MOTOR_CHANNEL = 0;
const uint8_t RIGHT_MOTOR_CHANNEL = 1;
const uint8_t SERVO_CHANNEL = 2;
const uint32_t SERVO_PWM_FREQ = 50;
const uint8_t SERVO_PWM_BITS = 16;
const uint32_t SERVO_PERIOD_US = 20000;

static uint32_t leftDuty = UINT32_MAX;
static uint32_t rightDuty = UINT32_MAX;
static uint32_t servoDuty = UINT32_MAX;

// Solo escribe si el valor cambia: loop llama a estas funciones aunque nada haya cambiado.
static void writeDuty(uint8_t pin, uint32_t &lastDuty, uint32_t duty) {
  if (duty == lastDuty) return;
  lastDuty = duty;
  ledcWrite(pin, duty);
}

bool setupMotors() {
  const uint8_t motorPins[] = { ENA, IN1, IN2, ENB, IN3, IN4 };
  for (uint8_t pin : motorPins) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
  }

  // No usar pinMode en ENA, ENB ni SERVO_PIN después de esto: los quitaría del LEDC.
  // Motores y servo tienen frecuencias distintas, así que el core les da timers distintos.
  bool ready = ledcAttachChannel(ENA, MOTOR_PWM_FREQ, MOTOR_PWM_BITS, LEFT_MOTOR_CHANNEL);
  ready = ledcAttachChannel(ENB, MOTOR_PWM_FREQ, MOTOR_PWM_BITS, RIGHT_MOTOR_CHANNEL) && ready;
  ready = ledcAttachChannel(SERVO_PIN, SERVO_PWM_FREQ, SERVO_PWM_BITS, SERVO_CHANNEL) && ready;
  return ready;
}

void stopMotors() {
  writeDuty(ENA, leftDuty, 0);
  writeDuty(ENB, rightDuty, 0);
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
}

static void setOneMotor(int pwmPin, uint32_t &lastDuty, int inA, int inB, int value, bool reverseMotor) {
  value = constrain(value, -1000, 1000);
  if (reverseMotor) value = -value;

  if (value == 0) {
    writeDuty(pwmPin, lastDuty, 0);
    digitalWrite(inA, LOW);
    digitalWrite(inB, LOW);
    return;
  }

  digitalWrite(inA, value > 0 ? HIGH : LOW);
  digitalWrite(inB, value > 0 ? LOW : HIGH);
  // Parte de PWM_MIN: por debajo de eso los motores no giran y el recorrido se perdería.
  writeDuty(pwmPin, lastDuty, map(abs(value), 1, 1000, PWM_MIN, PWM_MAX));
}

void applyMotors(int throttle) {
  setOneMotor(ENA, leftDuty, IN1, IN2, throttle, LEFT_MOTOR_REVERSE);
  setOneMotor(ENB, rightDuty, IN3, IN4, throttle, RIGHT_MOTOR_REVERSE);
}

// Freno activo del L298N: ambos IN en HIGH y EN proporcional a la fuerza de frenado.
void applyBrake(uint8_t brake) {
  digitalWrite(IN1, HIGH);
  digitalWrite(IN2, HIGH);
  digitalWrite(IN3, HIGH);
  digitalWrite(IN4, HIGH);

  uint32_t pwm = map(brake, 0, 255, 0, PWM_MAX);
  writeDuty(ENA, leftDuty, pwm);
  writeDuty(ENB, rightDuty, pwm);
}

void applySteering(int value) {
  value = constrain(value, -1000, 1000);
  if (SERVO_REVERSE) value = -value;

  int pulseUs = SERVO_CENTER_US + (value * SERVO_RANGE_US) / 1000;
  pulseUs = constrain(pulseUs, SERVO_CENTER_US - SERVO_RANGE_US, SERVO_CENTER_US + SERVO_RANGE_US);
  writeDuty(SERVO_PIN, servoDuty, (uint32_t)pulseUs * 65535 / SERVO_PERIOD_US);
}
