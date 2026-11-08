// Arranque, router por hash y bucle de actualización.

import { loadApi } from "./api.js";
import { createCalibration } from "./calibration.js";
import { createHud } from "./hud.js";
import { createKeyboard } from "./keyboard.js";

const POLL_INTERVAL_MS = 1000 / 30;
const DEFAULT_HASH = "#/manejo";
const VIEWS = { "#/manejo": "manejo", "#/calibracion": "calibracion" };

function applyAccent(config) {
  const accent = config.interfaz?.acento;
  if (accent) document.documentElement.style.setProperty("--accent", accent);
}

function startPolling(api, render) {
  let lastState = null;
  let lastPollMs = -Infinity;
  let waiting = false;

  async function poll() {
    waiting = true;
    try {
      lastState = await api.get_state();
      render(lastState);
    } catch (error) {
      console.error("Falló get_state", error);
      if (lastState) render({ ...lastState, connected: false });
    }
    waiting = false;
  }

  function frame(nowMs) {
    requestAnimationFrame(frame);
    if (waiting || nowMs - lastPollMs < POLL_INTERVAL_MS) return;
    lastPollMs = nowMs;
    poll();
  }

  requestAnimationFrame(frame);
}

function createRouter({ keyboard, calibration, sections }) {
  let currentHash = null;

  async function show(hash) {
    const leavingCalibration = currentHash === "#/calibracion";
    currentHash = hash;

    keyboard.setEnabled(VIEWS[hash] === "manejo");
    if (leavingCalibration) await calibration.leave();
    if (VIEWS[hash] === "calibracion") await calibration.enter();

    for (const [name, section] of Object.entries(sections)) {
      section.hidden = name !== VIEWS[hash];
    }
  }

  async function route() {
    const hash = location.hash in VIEWS ? location.hash : DEFAULT_HASH;
    if (hash === currentHash) return;

    if (currentHash === "#/calibracion" && calibration.hasUnsavedChanges()) {
      // Se vuelve a la URL actual mientras se decide. replaceState no dispara hashchange.
      history.replaceState(null, "", currentHash);
      if (!(await calibration.confirmLeave())) return;
    }

    history.replaceState(null, "", hash);
    await show(hash);
  }

  window.addEventListener("hashchange", route);

  return {
    start: route,
    currentView: () => VIEWS[currentHash],
  };
}

async function main() {
  const api = await loadApi();
  const sections = {
    manejo: document.getElementById("view-manejo"),
    calibracion: document.getElementById("view-calibracion"),
  };

  applyAccent(await api.get_config());

  const updateHud = createHud(sections.manejo, api);
  const calibration = createCalibration(sections.calibracion, api, applyAccent);
  const keyboard = createKeyboard(api);
  const router = createRouter({ keyboard, calibration, sections });

  await router.start();

  startPolling(api, (state) => {
    if (router.currentView() === "manejo") updateHud(state);
    else calibration.update(state);
  });
}

main();
