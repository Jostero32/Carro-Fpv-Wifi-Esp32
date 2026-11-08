# carro-fpv-wifi-esp32

Carro RC en primera persona (FPV) controlado por WiFi desde la PC. Un ESP32 maneja motores y dirección, una ESP32-CAM transmite el video y una app de escritorio lee un volante, un control o el teclado y muestra el video con la telemetría.

- Comandos UDP a 50 Hz con failsafe: si no llega nada en 300 ms, el carro se detiene y centra la dirección.
- Video MJPEG de 480x320 a unos 25 fps, con rotación y espejo configurables desde la app.
- Volante (probado con Logitech G29), DualSense, controles tipo Xbox o teclado, con calibración de ejes desde la app.
- R2 acelera; L2 frena y, si lo mantienes, pasa a reversa.
- Carga de firmware por OTA después de la primera carga por USB.
- Si se corta el WiFi, el carro y el video se reconectan solos.

Latencia medida en una red doméstica: unos 5 ms de ida y vuelta para los comandos con la dirección quieta y 15 ms de mediana mientras gira. Ver [Medir latencia](#medir-latencia).

## Materiales

| Pieza | Uso |
|---|---|
| ESP32 DevKit V1 (DOIT, 30 pines) | Controlador del carro |
| Puente H L298N | Dos motores DC |
| 2 motores DC | Tracción, los dos reciben el mismo acelerador |
| Servo MG995 | Dirección |
| ESP32-CAM AI Thinker (OV2640, con PSRAM) | Video |
| Batería para motores y servo | La app asume 2S (6.4 a 8.4 V) al mostrar el porcentaje |
| Router o hotspot de 2.4 GHz | El ESP32 no usa 5 GHz |
| PC con Windows | App de control |
| Adaptador USB serie de 3.3 V | Solo si tu ESP32-CAM no trae base con USB |

## Conexiones

### Carro (ESP32 DevKit V1)

| ESP32 | Va a | Nota |
|---|---|---|
| GPIO 23 | L298N ENA | PWM motor izquierdo |
| GPIO 19 | L298N IN1 | |
| GPIO 18 | L298N IN2 | |
| GPIO 5 | L298N ENB | PWM motor derecho |
| GPIO 17 | L298N IN3 | |
| GPIO 16 | L298N IN4 | |
| GPIO 15 | Señal del servo | |
| GPIO 2 | LED de la placa | Estado de WiFi y failsafe |
| GND | GND del L298N y del servo | Tierra común obligatoria |

- Quita los jumpers de ENA y ENB del L298N: el ESP32 controla la velocidad por esos pines.
- Alimenta el servo desde la batería o un regulador de 5 a 6 V, no desde el pin de 3.3 V del ESP32.
- Opcionales en `config.h`: `LIGHTS_PIN` para luces y `BATTERY_PIN` para medir la batería con un divisor de voltaje. La batería debe ir a un pin de ADC1 (GPIO 32 a 39) porque ADC2 no funciona con WiFi.

Todos los pines se cambian en `firmware/carrito/config.h`.

### Cámara (ESP32-CAM AI Thinker)

El sensor ya viene conectado en la placa. El LED de flash es GPIO 4 y el LED de estado es GPIO 33. Solo necesita 5 V y GND.

## Estructura

- `docs/protocolo.md`: especificación del protocolo UDP. Es la fuente de verdad para el firmware y para Python.
- `docs/diseno/`: diseño de las pantallas de la app.
- `firmware/carrito/`: sketch del carro.
- `firmware/camara/`: sketch de la cámara.
- `pc/`: app de escritorio en Python 3.12. La interfaz está en `pc/ui`.

Las credenciales van en `firmware/*/config.h` y `pc/config.toml`. Git los ignora: en el repositorio solo están las plantillas `.example`.

## Firmware

1. En Arduino IDE 2, agrega esta URL en las preferencias, en "Additional boards manager URLs":
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
2. Instala el paquete "esp32 by Espressif Systems" versión 3.x desde el gestor de placas.
3. En cada carpeta de sketch, copia `config.example.h` a `config.h` y llena la red, la clave de OTA y los pines. El carro y la cámara deben estar en la misma red que la PC.
4. Elige la placa "DOIT ESP32 DEVKIT V1" para el carro y "AI Thinker ESP32-CAM" para la cámara.
5. En la cámara, elige en Herramientas el esquema de particiones "Minimal SPIFFS (1.9MB APP with OTA/128KB SPIFFS)". El que trae por defecto la placa no tiene OTA.
6. Después de la primera carga por USB, cada placa aparece como puerto de red para subir por OTA.

Sin `config.h`, la compilación se detiene con un `#error` que indica copiarlo.

### Carro

En `config.h` se calibran el centro y el recorrido del servo (`SERVO_CENTER_US`, `SERVO_RANGE_US`), el sentido de cada motor y `PWM_MIN`, el PWM donde los motores apenas empiezan a girar.

Para probar el carro sin la app, desde la carpeta `pc/`:

```
python tools/probar_carrito.py --sin-motor
```

Sin `--sin-motor` también gira los motores con acelerador bajo, así que mantén las ruedas en el aire.

### Cámara

La AI Thinker no trae USB: la primera carga va con un adaptador serie de 3.3 V o 5 V según tu módulo, con GPIO0 conectado a GND durante la carga. Luego quita ese puente y reinicia. Aliméntala con 5 V estables: con poca corriente se reinicia al usar WiFi o el flash.

Si la placa da problemas con la opción AI Thinker, esta configuración probó funcionar por USB y OTA: placa "ESP32 Wrover Module", 40 MHz, DIO, 115200 y el mismo esquema Minimal SPIFFS. Por USB, mantén BOOT unos 15 segundos desde que empieza la carga.

Para compilar por línea de comandos:

```
arduino-cli compile --fqbn esp32:esp32:esp32cam:PartitionScheme=min_spiffs firmware/camara
```

Para probar el stream, abre `http://camara.local:81/stream` en el navegador. Solo se permite un cliente a la vez: si la app ya lo está mostrando, el navegador recibe un 503.

- `/capture`: un JPEG.
- `/status`: JSON con fps, resolución, calidad, rssi, clientes, uptime y estado de la cámara.
- `/control?var=X&val=Y`: cambia en caliente `framesize` (0 a 15, VGA es 10), `quality` (4 a 63), `vflip`, `hmirror` y `flash` (0 a 255).

Desde la carpeta `pc/`, `python tools/probar_camara.py` muestra fps y rssi cada segundo, y con `--guardar 3` descarga tres imágenes para revisar el encuadre.

Si cambias `HOSTNAME` en `config.h`, cambia también `url` en la sección `[camara]` de `config.toml`.

Si montas la cámara de lado o de cabeza, ajusta "Rotación de la imagen" en la vista de calibración y guarda. También están `rotacion` (0, 90, 180 o 270 grados en sentido horario) y `espejo` en la sección `[camara]` de `config.toml`. Se aplica al mostrar el video en la app, sin cargar firmware, así que `/capture` y `/stream` siguen dando la imagen original.

Si se corta el WiFi, el carro se detiene por failsafe y la app reconecta el video cuando la cámara vuelve a responder. Para eso consulta `/status` cada 2 s.

## App de PC

Necesita [uv](https://docs.astral.sh/uv/). Desde la carpeta `pc/`:

```
copy config.example.toml config.toml
uv run carrito-pc
```

En Linux o macOS usa `cp` en lugar de `copy`. Guardar desde la interfaz reescribe `config.toml` y quita sus comentarios.

`uv run carrito-pc` abre la interfaz. En Windows usa Edge WebView2, que ya viene con el sistema. Otras formas de correrla:

- `uv run carrito-pc --consola`: maneja con el joystick, sin interfaz.
- `uv run carrito-pc --listar`: lista los joysticks conectados.
- `uv run carrito-pc --calibrar`: detecta los ejes y sus rangos en la terminal, sin enviar nada al carro.

**Antes de manejar con un joystick, calibra los ejes.** Los índices por defecto (acelerador 2, freno 3) no corresponden a todos los controles. En uno como el DualSense son el stick derecho, cuyo reposo se leería como medio acelerador. Corre `--calibrar` y haz la primera prueba con las ruedas en el aire.

Con joystick, R2 acelera y L2 frena. Si mantienes L2 presionado, cuando el carro ya debería estar detenido pasa a retroceder con la fuerza del gatillo. Como no hay sensor de velocidad, ese momento se estima: 0.4 s con L2 a fondo tras acelerar, más con freno suave, y al instante si llevas 1.5 s sin acelerar. Con R2 y L2 a la vez solo frena. El botón de reversa sigue funcionando: mantenlo presionado y R2 retrocede.

Teclado en la vista de manejo:

| Tecla | Acción |
|---|---|
| Flechas o WASD | Dirección y acelerador |
| Espacio | Freno |
| L | Luces |
| H | Claxon |
| Shift | Turbo |
| F11 | Pantalla completa |

Pruebas: `uv run pytest` desde `pc/`.

### Medir latencia

Cierra la app y, desde la carpeta `pc/`:

```
python tools/medir_latencia.py
python tools/medir_latencia.py --comparar
```

Mide el ping al carro con y sin video, y fps, tamaño y tiempo de transferencia de los frames. `--comparar` prueba varias resoluciones y calidades, y al final restaura las que tenía la cámara. Envía comandos neutros: los motores no giran, pero el servo vuelve al centro.

### Interfaz en modo simulado

Para ajustar el diseño sin la app ni el carro, sirve `pc/ui` con un servidor local:

```
cd pc/ui
python -m http.server 8000
```

Abre `http://localhost:8000` en Chrome. Si `window.pywebview` no aparece en 1 s, la interfaz usa una API simulada con datos en movimiento y, cada 30 s, unos segundos de failsafe. Hace falta el servidor porque Chrome bloquea los módulos ES abiertos con doble clic desde `file://`.
