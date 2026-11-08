// Control de los motores por L298N y del servo de dirección.
#pragma once

#include <Arduino.h>

// Deja los pines de motor en LOW y configura el PWM. Devuelve false si falla algún canal.
// Después el servo aún no recibe pulsos: hay que llamar a applySteering.
bool setupMotors();

void stopMotors();

// throttle va de -1000 a 1000. Negativo es reversa.
void applyMotors(int throttle);

// Freno activo con fuerza de 0 a 255.
void applyBrake(uint8_t brake);

// value va de -1000 a 1000. Negativo es izquierda.
void applySteering(int value);
