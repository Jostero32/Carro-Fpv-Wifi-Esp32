// Vista de calibración y configuración.

import { setStyle, setText } from "./hud.js";

const AXES = [
  { id: "direccion", label: "Dirección", bipolar: true },
  { id: "acelerador", label: "Acelerador", bipolar: false },
  { id: "freno", label: "Freno", bipolar: false },
];

const BUTTONS = [
  { id: "luces", label: "Luces" },
  { id: "claxon", label: "Claxon" },
  { id: "turbo", label: "Turbo" },
  { id: "reversa", label: "Reversa" },
];

const CAPTURE_TIMEOUT_S = 5;
const STATUS_VISIBLE_MS = 2500;

function clamp(value, low, high) {
  return Math.min(Math.max(value, low), high);
}

function formatRangeEnd(value, bipolar) {
  const pct = Math.round(value * 100);
  return bipolar && pct > 0 ? `+${pct} %` : `${pct} %`;
}

function formatSigned(pct) {
  return pct > 0 ? `+${pct} %` : `${pct} %`;
}

function formatButton(index) {
  return index === null || index === undefined ? "Sin asignar" : `Botón ${index}`;
}

// Lleva la lectura cruda a 0..1 según el rango calibrado.
function normalize(raw, axisConfig) {
  const span = axisConfig.max - axisConfig.min;
  if (span <= 0) return 0;
  const position = clamp((raw - axisConfig.min) / span, 0, 1);
  return axisConfig.invertir ? 1 - position : position;
}

export function createCalibration(root, api, onConfigSaved) {
  const find = (id) => root.querySelector(`#${id}`);

  const el = {
    device: find("device"),
    axes: find("axes"),
    buttonRows: find("button-rows"),
    host: find("net-host"),
    port: find("net-port"),
    video: find("net-video"),
    rotation: find("camera-rotation"),
    mirror: find("camera-mirror"),
    hz: find("net-hz"),
    testButton: find("test-connection"),
    testResult: find("test-result"),
    testDot: find("test-dot"),
    testText: find("test-text"),
    limit: find("limit"),
    limitValue: find("limit-value"),
    watchdog: find("watchdog"),
    saveButton: find("save-button"),
    saveStatus: find("save-status"),
    leaveDialog: find("leave-dialog"),
    leaveStay: find("leave-stay"),
    leaveConfirm: find("leave-confirm"),
  };

  const axisViews = new Map();
  const buttonViews = new Map();
  let draft = null;
  let saved = null;
  let calibratingAxis = null;
  let capturingButton = false;
  let statusTimer = null;

  const axisTemplate = document.getElementById("axis-template");
  const buttonTemplate = document.getElementById("button-template");

  function showSaveStatus(text, isError) {
    clearTimeout(statusTimer);
    el.saveStatus.textContent = text;
    el.saveStatus.classList.toggle("is-error", isError);
    statusTimer = setTimeout(() => {
      el.saveStatus.textContent = "";
    }, STATUS_VISIBLE_MS);
  }

  function updateBusyControls() {
    for (const [id, view] of axisViews) {
      view.calibrate.disabled = calibratingAxis !== null && calibratingAxis !== id;
    }
    for (const view of buttonViews.values()) {
      view.capture.disabled = capturingButton;
    }
  }

  // ---------- Ejes ----------
  function renderAxisRange(axis) {
    const view = axisViews.get(axis.id);
    const axisConfig = draft.ejes[axis.id];
    view.min.textContent = formatRangeEnd(axisConfig.min, axis.bipolar);
    view.max.textContent = formatRangeEnd(axisConfig.max, axis.bipolar);
  }

  async function toggleAxisCalibration(axis) {
    const view = axisViews.get(axis.id);

    if (calibratingAxis === null) {
      calibratingAxis = axis.id;
      view.calibrate.textContent = "Terminar";
      view.hint.hidden = false;
      updateBusyControls();
      await api.start_axis_calibration(axis.id);
      return;
    }

    await stopAxisCalibration();
  }

  // finish_axis_calibration ya guarda el rango, por eso se actualiza también lo guardado.
  async function stopAxisCalibration() {
    if (calibratingAxis === null) return;

    const axis = AXES.find((item) => item.id === calibratingAxis);
    const view = axisViews.get(axis.id);
    const range = await api.finish_axis_calibration(axis.id);

    calibratingAxis = null;
    view.calibrate.textContent = "Calibrar rango";
    view.hint.hidden = true;
    updateBusyControls();

    if (!range) return;
    for (const config of [draft, saved]) {
      config.ejes[axis.id].min = range.min;
      config.ejes[axis.id].max = range.max;
    }
    renderAxisRange(axis);
  }

  function buildAxes() {
    for (const axis of AXES) {
      const node = axisTemplate.content.firstElementChild.cloneNode(true);
      const part = (name) => node.querySelector(`.axis-${name}`);
      const view = {
        source: part("source"),
        value: part("value"),
        fill: part("fill"),
        marker: part("marker"),
        min: part("min"),
        max: part("max"),
        invert: part("invert"),
        deadzone: part("deadzone"),
        curve: part("curve"),
        calibrate: part("calibrate"),
        hint: part("hint"),
      };

      part("label").textContent = axis.label;
      view.invert.addEventListener("change", () => {
        draft.ejes[axis.id].invertir = view.invert.checked;
      });
      view.deadzone.addEventListener("input", () => {
        draft.ejes[axis.id].zona_muerta = view.deadzone.valueAsNumber;
      });
      view.curve.addEventListener("change", () => {
        draft.ejes[axis.id].curva = view.curve.value;
      });
      view.calibrate.addEventListener("click", () => toggleAxisCalibration(axis));

      axisViews.set(axis.id, view);
      el.axes.append(node);
    }
  }

  // ---------- Botones ----------
  async function captureButton(button) {
    const view = buttonViews.get(button.id);
    capturingButton = true;
    updateBusyControls();
    view.input.textContent = "Presiona un botón…";

    const index = await api.capture_next_button(CAPTURE_TIMEOUT_S);
    capturingButton = false;
    updateBusyControls();
    if (!draft) return;  // Se salió de la vista mientras se esperaba.

    if (index !== null) draft.botones[button.id] = index;
    view.input.textContent = formatButton(draft.botones[button.id]);
  }

  function buildButtons() {
    for (const button of BUTTONS) {
      const node = buttonTemplate.content.firstElementChild.cloneNode(true);
      const view = {
        input: node.querySelector(".button-input"),
        capture: node.querySelector(".button-capture"),
      };

      node.querySelector(".button-action").textContent = button.label;
      view.capture.addEventListener("click", () => captureButton(button));

      buttonViews.set(button.id, view);
      el.buttonRows.append(node);
    }
  }

  // ---------- Formulario ----------
  function fillForm() {
    el.device.value = draft.entrada.dispositivo;
    el.host.value = draft.carrito.host;
    el.port.value = draft.carrito.port;
    el.video.value = draft.camara.url;
    el.rotation.value = String(draft.camara.rotacion);
    el.mirror.checked = draft.camara.espejo;
    el.hz.value = String(draft.control.command_hz);
    el.limit.value = draft.seguridad.limite_velocidad;
    el.limitValue.textContent = `${draft.seguridad.limite_velocidad} %`;
    el.watchdog.value = draft.seguridad.watchdog_ms;

    for (const axis of AXES) {
      const view = axisViews.get(axis.id);
      const axisConfig = draft.ejes[axis.id];
      view.source.textContent = `Eje ${axisConfig.indice}`;
      view.invert.checked = axisConfig.invertir;
      view.deadzone.value = axisConfig.zona_muerta;
      view.curve.value = axisConfig.curva;
      renderAxisRange(axis);
    }

    for (const button of BUTTONS) {
      buttonViews.get(button.id).input.textContent = formatButton(draft.botones[button.id]);
    }
  }

  function bindForm() {
    el.device.addEventListener("change", () => {
      draft.entrada.dispositivo = el.device.value;
    });
    el.host.addEventListener("input", () => {
      draft.carrito.host = el.host.value.trim();
    });
    el.port.addEventListener("input", () => {
      draft.carrito.port = el.port.valueAsNumber;
    });
    el.video.addEventListener("input", () => {
      draft.camara.url = el.video.value.trim();
    });
    el.rotation.addEventListener("change", () => {
      draft.camara.rotacion = Number(el.rotation.value);
    });
    el.mirror.addEventListener("change", () => {
      draft.camara.espejo = el.mirror.checked;
    });
    el.hz.addEventListener("change", () => {
      draft.control.command_hz = Number(el.hz.value);
    });
    el.limit.addEventListener("input", () => {
      draft.seguridad.limite_velocidad = el.limit.valueAsNumber;
      el.limitValue.textContent = `${el.limit.value} %`;
    });
    el.watchdog.addEventListener("input", () => {
      draft.seguridad.watchdog_ms = el.watchdog.valueAsNumber;
    });

    el.testButton.addEventListener("click", testConnection);
    el.saveButton.addEventListener("click", save);
  }

  function findFormError() {
    const { port } = draft.carrito;
    if (!draft.carrito.host) return "Falta el host del carrito";
    if (!Number.isInteger(port) || port < 1 || port > 65535) return "Puerto UDP no válido";
    if (!(draft.seguridad.watchdog_ms > 0)) return "Timeout del watchdog no válido";
    for (const axis of AXES) {
      const deadzone = draft.ejes[axis.id].zona_muerta;
      if (!(deadzone >= 0 && deadzone <= 20)) return `Zona muerta de ${axis.label} no válida`;
    }
    return null;
  }

  async function save() {
    const formError = findFormError();
    if (formError) {
      showSaveStatus(formError, true);
      return;
    }

    try {
      const result = await api.save_config(structuredClone(draft));
      if (result && result.ok === false) throw new Error(result.error);
      saved = structuredClone(draft);
      onConfigSaved(draft);
      showSaveStatus("Guardado", false);
    } catch (error) {
      console.error("No se pudo guardar la configuración", error);
      showSaveStatus("No se pudo guardar", true);
    }
  }

  async function testConnection() {
    el.testButton.disabled = true;
    el.testResult.hidden = false;
    el.testDot.className = "test-dot";
    el.testText.textContent = "Probando…";

    let latencyMs = null;
    try {
      latencyMs = await api.test_connection();
    } catch (error) {
      console.error("Falló la prueba de conexión", error);
    }

    const answered = latencyMs !== null;
    el.testDot.classList.toggle("is-ok", answered);
    el.testDot.classList.toggle("is-error", !answered);
    el.testText.textContent = answered ? `Respondió en ${Math.round(latencyMs)} ms` : "Sin respuesta";
    el.testButton.disabled = false;
  }

  buildAxes();
  buildButtons();
  bindForm();

  return {
    async enter() {
      saved = await api.get_config();
      draft = structuredClone(saved);
      fillForm();
    },

    update(state) {
      if (!draft) return;

      for (const axis of AXES) {
        const view = axisViews.get(axis.id);
        const position = normalize(state.axes_raw[axis.id], draft.ejes[axis.id]);

        if (axis.bipolar) {
          const pct = Math.round((position * 2 - 1) * 100);
          setText(view.value, formatSigned(pct));
          setStyle(view.fill, "left", `${Math.min(position, 0.5) * 100}%`);
          setStyle(view.fill, "width", `${Math.abs(position - 0.5) * 100}%`);
        } else {
          setText(view.value, `${Math.round(position * 100)} %`);
          setStyle(view.fill, "left", "0%");
          setStyle(view.fill, "width", `${position * 100}%`);
        }
        setStyle(view.marker, "left", `${position * 100}%`);
      }
    },

    hasUnsavedChanges() {
      if (!draft) return false;
      return calibratingAxis !== null || JSON.stringify(draft) !== JSON.stringify(saved);
    },

    confirmLeave() {
      return new Promise((resolve) => {
        const finish = (leave) => {
          el.leaveStay.removeEventListener("click", stay);
          el.leaveConfirm.removeEventListener("click", leaveView);
          el.leaveDialog.removeEventListener("cancel", stay);
          el.leaveDialog.close();
          resolve(leave);
        };
        const stay = () => finish(false);
        const leaveView = () => finish(true);

        el.leaveStay.addEventListener("click", stay);
        el.leaveConfirm.addEventListener("click", leaveView);
        el.leaveDialog.addEventListener("cancel", stay);
        el.leaveDialog.showModal();
      });
    },

    // Al salir no debe quedar un eje registrando rango en segundo plano.
    async leave() {
      await stopAxisCalibration();
      draft = null;
    },
  };
}
