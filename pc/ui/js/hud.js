// Dibujo del HUD de manejo. Solo toca el DOM cuando un valor cambió.

const GAUGE_CENTER_X = 180;
const GAUGE_CENTER_Y = 165;
const GAUGE_START_DEG = 210;
const GAUGE_SWEEP_DEG = 240;
const THROTTLE_RADIUS = 140;
const BRAKE_RADIUS = 122;
const TICK_COUNT = 11;

const BATTERY_BAR_WIDTH_PX = 120;
const BATTERY_ICON_WIDTH = 14;
const BATTERY_LOW_PCT = 20;

const VIDEO_RETRY_MS = 2000;

// Guías en viewBox 1000x360. STRAIGHT es la dirección en 0; TURNED es la curva exacta del
// diseño, que corresponde a steering 180 a la derecha.
const GUIDE_TOP_Y = 110;
const GUIDE_BOTTOM_Y = 360;
const GUIDE_REFERENCE = 0.18;
const GUIDE_EXPONENT = 0.6;
const GUIDE_STRAIGHT_Y = [255, 175];
const GUIDE_LEFT_STRAIGHT = [400, 420, 450, 470];
const GUIDE_LEFT_TURNED = { x: [400, 430, 500, 590], y: [250, 170] };
const GUIDE_RIGHT_STRAIGHT = [600, 580, 550, 530];
const GUIDE_RIGHT_TURNED = { x: [600, 615, 650, 700], y: [260, 180] };

const lastValues = new WeakMap();

function changed(element, key, value) {
  let values = lastValues.get(element);
  if (!values) {
    values = {};
    lastValues.set(element, values);
  }
  if (values[key] === value) return false;
  values[key] = value;
  return true;
}

export function setText(element, value) {
  if (changed(element, "text", value)) element.textContent = value;
}

export function setAttr(element, name, value) {
  if (changed(element, name, value)) element.setAttribute(name, value);
}

export function setStyle(element, property, value) {
  if (changed(element, property, value)) element.style.setProperty(property, value);
}

export function setClass(element, name, enabled) {
  if (changed(element, `class:${name}`, enabled)) element.classList.toggle(name, enabled);
}

export function setHidden(element, hidden) {
  if (changed(element, "hidden", hidden)) element.hidden = hidden;
}

function round1(value) {
  return Math.round(value * 10) / 10;
}

function polar(radius, degrees) {
  const radians = (degrees * Math.PI) / 180;
  return {
    x: round1(GAUGE_CENTER_X + radius * Math.cos(radians)),
    y: round1(GAUGE_CENTER_Y - radius * Math.sin(radians)),
  };
}

// Vacío en 0: con trazo redondeado, un arco de largo cero se vería como un punto.
function arcPath(radius, fraction) {
  if (fraction <= 0) return "";
  const sweep = GAUGE_SWEEP_DEG * Math.min(fraction, 1);
  const start = polar(radius, GAUGE_START_DEG);
  const end = polar(radius, GAUGE_START_DEG - sweep);
  const largeArc = sweep > 180 ? 1 : 0;
  return `M${start.x} ${start.y} A${radius} ${radius} 0 ${largeArc} 1 ${end.x} ${end.y}`;
}

function guidePath(straightX, turned, amount) {
  const x = straightX.map((value, i) => value + (turned.x[i] - value) * amount);
  const y = GUIDE_STRAIGHT_Y.map(
    (value, i) => value + (turned.y[i] - value) * Math.min(amount, 1),
  );
  return { x, y };
}

function mirror(guide) {
  return { x: guide.x.map((value) => 1000 - value), y: guide.y };
}

function formatGuide({ x, y }) {
  const [x0, x1, x2, x3] = x.map(round1);
  const [y1, y2] = y.map(round1);
  return `M${x0} ${GUIDE_BOTTOM_Y} C ${x1} ${y1}, ${x2} ${y2}, ${x3} ${GUIDE_TOP_Y}`;
}

// amount vale 1 en la curva del diseño. La potencia menor que 1 abre el giro desde el centro
// y evita que las guías se salgan de pantalla en los extremos.
function guidePaths(steering) {
  const magnitude = Math.min(Math.abs(steering) / 1000, 1);
  const amount = (magnitude / GUIDE_REFERENCE) ** GUIDE_EXPONENT;
  const left = guidePath(GUIDE_LEFT_STRAIGHT, GUIDE_LEFT_TURNED, amount);
  const right = guidePath(GUIDE_RIGHT_STRAIGHT, GUIDE_RIGHT_TURNED, amount);

  // A la izquierda es el mismo dibujo en espejo: cada guía toma la forma de la opuesta.
  if (steering < 0) return { left: mirror(right), right: mirror(left) };
  return { left, right };
}

function signalBars(state) {
  if (!state.connected || state.rssi === null) return 0;
  if (state.rssi >= -55) return 4;
  if (state.rssi >= -65) return 3;
  if (state.rssi >= -75) return 2;
  return 1;
}

function addCacheBuster(url) {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}_=${Date.now()}`;
}

function setupVideo(video) {
  let cameraUrl = "";
  let shownGeneration = null;
  let retryTimer = null;

  function load(url) {
    video.src = url;
  }

  video.addEventListener("load", () => {
    video.hidden = false;
  });

  video.addEventListener("error", () => {
    video.hidden = true;
    clearTimeout(retryTimer);
    if (!cameraUrl) return;
    retryTimer = setTimeout(() => load(addCacheBuster(cameraUrl)), VIDEO_RETRY_MS);
  });

  return function updateVideo(url, generation, rotation, mirror) {
    setStyle(video, "transform", `rotate(${rotation}deg) scaleX(${mirror ? -1 : 1})`);
    setClass(video, "is-sideways", rotation % 180 !== 0);

    // Un stream MJPEG puede no disparar load, así que también se revisa si ya hay imagen.
    if (video.hidden && video.naturalWidth > 0) video.hidden = false;

    // La misma URL no volvería a pedirse al servidor, de ahí el cache buster.
    const reconnect = generation !== shownGeneration;
    if (url === cameraUrl && !reconnect) return;
    cameraUrl = url;
    shownGeneration = generation;
    clearTimeout(retryTimer);
    video.hidden = true;
    if (url) load(addCacheBuster(url));
    else video.removeAttribute("src");
  };
}

export function createHud(root, api) {
  const find = (id) => root.querySelector(`#${id}`);

  const el = {
    statusDot: find("status-dot"),
    statusText: find("status-text"),
    signal: find("signal"),
    signalBars: [...root.querySelectorAll(".signal-bar")],
    battery: find("battery"),
    batteryIcon: find("battery-icon"),
    batteryLevel: find("battery-level"),
    batteryBar: find("battery-bar"),
    gaugeSvg: find("gauge-svg"),
    throttleArc: find("throttle-arc"),
    brakeArc: find("brake-arc"),
    ticks: [...root.querySelectorAll(".tick")],
    gear: find("gear"),
    speed: find("speed"),
    steering: find("steering"),
    steeringMarker: find("steering-marker"),
    guideLeft: find("guide-left"),
    guideRight: find("guide-right"),
    noSignal: find("no-signal"),
    toggles: [...root.querySelectorAll(".toggle")],
  };

  const updateVideo = setupVideo(find("video"));

  for (const toggle of el.toggles) {
    // Sin esto el botón conserva el foco y la barra espaciadora de manejo lo pulsaría.
    toggle.addEventListener("mousedown", (event) => event.preventDefault());
    toggle.addEventListener("click", () => api.toggle_button(toggle.dataset.button));
  }

  function updateStatus(state) {
    const linked = state.connected && !state.failsafe;
    setClass(el.statusDot, "is-connected", linked);
    setText(el.statusText, linked ? "Conectado" : "Sin señal");
    setHidden(el.noSignal, linked);

    const bars = signalBars(state);
    el.signalBars.forEach((bar, i) => setAttr(bar, "fill-opacity", i < bars ? "1" : "0.3"));
    setAttr(el.signal, "aria-label", `Señal: ${bars} de 4`);
  }

  function updateBattery(pct) {
    setHidden(el.battery, pct === null);
    if (pct === null) return;

    const clamped = Math.min(Math.max(pct, 0), 100);
    setStyle(el.batteryBar, "width", `${round1((BATTERY_BAR_WIDTH_PX * clamped) / 100)}px`);
    setClass(el.batteryBar, "is-low", clamped < BATTERY_LOW_PCT);
    setAttr(el.batteryLevel, "width", String(round1((BATTERY_ICON_WIDTH * clamped) / 100)));
    setAttr(el.batteryIcon, "aria-label", `Batería ${clamped} por ciento`);
  }

  function updateGauge(state) {
    const fraction = Math.abs(state.throttle) / 1000;
    const pct = Math.round(fraction * 100);

    setAttr(el.throttleArc, "d", arcPath(THROTTLE_RADIUS, fraction));
    setAttr(el.brakeArc, "d", arcPath(BRAKE_RADIUS, state.brake / 255));
    setText(el.gear, state.gear);
    setText(el.speed, String(pct));

    el.ticks.forEach((tick, i) => {
      setClass(tick, "is-lit", pct > 0 && i / (TICK_COUNT - 1) <= fraction);
    });

    const gearName = state.gear === "R" ? "reversa" : "adelante";
    const brakePct = Math.round((state.brake / 255) * 100);
    setAttr(el.gaugeSvg, "aria-label", `Acelerador ${pct} por ciento, freno ${brakePct}, marcha ${gearName}`);
  }

  function updateSteering(steering) {
    const position = Math.min(Math.max(50 + steering / 20, 0), 100);
    setStyle(el.steeringMarker, "left", `${round1(position)}%`);

    const pct = Math.abs(Math.round(steering / 10));
    const side = steering < 0 ? "a la izquierda" : "a la derecha";
    setAttr(el.steering, "aria-label", pct === 0 ? "Dirección al centro" : `Dirección ${pct} por ciento ${side}`);

    const guides = guidePaths(steering);
    setAttr(el.guideLeft, "d", formatGuide(guides.left));
    setAttr(el.guideRight, "d", formatGuide(guides.right));
  }

  function updateButtons(buttons) {
    for (const toggle of el.toggles) {
      const on = Boolean(buttons[toggle.dataset.button]);
      setClass(toggle, "is-on", on);
      setAttr(toggle, "aria-pressed", String(on));
    }
  }

  return function updateHud(state) {
    updateStatus(state);
    updateBattery(state.battery_pct);
    updateGauge(state);
    updateSteering(state.steering);
    updateButtons(state.buttons);
    updateVideo(state.camera_url, state.camera_generation, state.camera_rotation, state.camera_mirror);
  };
}
