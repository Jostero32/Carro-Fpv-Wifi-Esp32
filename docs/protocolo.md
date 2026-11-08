# Protocolo

Todos los paquetes son UDP y little endian. El CRC8 usa polinomio 0x07 con valor inicial 0x00 y se calcula sobre todos los bytes anteriores al CRC.

1. Comando (PC a carrito, puerto 4210, 50 Hz). 12 bytes, formato struct de Python "<BBHhhBHB":  
   magic uint8 = 0xC4  
   version uint8 = 1  
   seq uint16, sube en cada envío y da la vuelta después de 65535  
   steering int16, de -1000 a 1000 (negativo es izquierda)  
   throttle int16, de -1000 a 1000 (negativo es reversa)  
   brake uint8, de 0 a 255  
   buttons uint16, bit 0 luces, bit 1 claxon, bit 2 turbo  
   crc8 uint8

2. Ping (PC a carrito, mismo puerto, una vez por segundo). 7 bytes, formato "<BBIB":  
   magic 0xC6, version 1, timestamp uint32 en microsegundos de la PC, crc8.  
   El carrito responde de inmediato con el mismo contenido pero magic 0xC7 (pong). La PC calcula la latencia de ida y vuelta.

3. Telemetría (carrito a PC, 10 Hz). 11 bytes, formato "<BBHbHhBB":  
   magic 0xC5, version 1  
   last_seq uint16, último seq aceptado  
   rssi int8 en dBm  
   battery_mv uint16, 0 si no hay sensor  
   throttle_applied int16, el valor real después de la rampa  
   flags uint8, bit 0 failsafe activo, bit 1 frenando, bit 2 luces encendidas  
   crc8

Reglas

- El carrito envía telemetría y pong a la IP y puerto de origen del último comando válido. Así la PC usa un solo socket para enviar y recibir.
- El carrito descarta paquetes con magic, version, tamaño o CRC incorrectos.
- Un comando es nuevo si (int16)(seq - last_seq) > 0. Si pasaron más de 1000 ms sin comandos válidos, acepta cualquier seq porque la PC pudo reiniciarse.
- Si no llega un comando válido en FAILSAFE_MS, el carrito detiene los motores y centra la dirección.
