// Envoltorio de window.pywebview.api con un modo simulado para trabajar sin la app.
//
// get_state() devuelve lo que arma build_state en carrito_pc/ui_app.py. get_config() devuelve:
// {
//   carrito: { host, port }, control: { command_hz }, camara: { url, rotacion, espejo },
//   entrada: { dispositivo },
//   ejes: { direccion | acelerador | freno: { indice, invertir, zona_muerta, curva, min, max } },
//   botones: { luces, claxon, turbo, reversa },
//   seguridad: { limite_velocidad, watchdog_ms },
//   bateria: { mv_vacia, mv_llena }, interfaz: { acento }
// }

const PYWEBVIEW_WAIT_MS = 1000;

function isPywebviewReady() {
  return typeof window.pywebview?.api?.get_state === "function";
}

// Sin window.pywebview después de 1 s se usa la simulación. Si existe pero aún no está
// lista, se sigue esperando pywebviewready: nunca se cae a datos falsos dentro de la app.
export function loadApi() {
  return new Promise((resolve) => {
    if (isPywebviewReady()) {
      resolve(window.pywebview.api);
      return;
    }

    window.addEventListener("pywebviewready", () => resolve(window.pywebview.api), { once: true });

    setTimeout(() => {
      if (!window.pywebview) {
        console.info("Sin pywebview: se usa la API simulada.");
        resolve(createSimulatedApi());
      }
    }, PYWEBVIEW_WAIT_MS);
  });
}

// ---------- Simulación ----------
function createSimulatedConfig() {
  return {
    carrito: { host: "carrito.local", port: 4210 },
    control: { command_hz: 50 },
    camara: { url: "http://camara.local:81/stream", rotacion: 0, espejo: false },
    entrada: { dispositivo: "volante" },
    ejes: {
      direccion: { indice: 0, invertir: false, zona_muerta: 3, curva: "suave", min: -1, max: 1 },
      acelerador: { indice: 2, invertir: false, zona_muerta: 2, curva: "lineal", min: 0, max: 1 },
      freno: { indice: 3, invertir: false, zona_muerta: 2, curva: "lineal", min: 0, max: 1 },
    },
    botones: { luces: 4, claxon: 5, turbo: 6, reversa: 7 },
    seguridad: { limite_velocidad: 70, watchdog_ms: 250 },
    bateria: { mv_vacia: 6400, mv_llena: 8400 },
    interfaz: { acento: "#FF8A3D" },
  };
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function createSimulatedApi() {
  const startMs = performance.now();
  const buttons = { luces: true, claxon: false, turbo: false };
  const ranges = {};
  let config = createSimulatedConfig();
  let gear = "D";
  let lastRaw = { direccion: 0, acelerador: 0, freno: 0 };

  function secondsRunning() {
    return (performance.now() - startMs) / 1000;
  }

  function readAxes(t) {
    return {
      direccion: Math.sin(t * 0.8),
      acelerador: Math.max(0, Math.sin(t * 0.5)),
      freno: Math.max(0, Math.sin(t * 0.5 + 3.5)),
    };
  }

  return {
    async get_state() {
      const t = secondsRunning();
      lastRaw = readAxes(t);

      for (const [axis, range] of Object.entries(ranges)) {
        range.min = Math.min(range.min, lastRaw[axis]);
        range.max = Math.max(range.max, lastRaw[axis]);
      }

      const throttle = Math.round(900 * Math.sin(t * 0.5));
      if (throttle !== 0) gear = throttle < 0 ? "R" : "D";

      const failsafe = t % 30 > 26;
      const batteryPct = Math.max(0, 78 - Math.round(t / 2));
      const { mv_vacia: emptyMv, mv_llena: fullMv } = config.bateria;

      return {
        connected: !failsafe,
        failsafe,
        latency_ms: failsafe ? null : 6 + 3 * Math.sin(t),
        rssi: failsafe ? null : Math.round(-62 + 16 * Math.sin(t * 0.3)),
        battery_mv: Math.round(emptyMv + ((fullMv - emptyMv) * batteryPct) / 100),
        battery_pct: batteryPct,
        steering: failsafe ? 0 : Math.round(1000 * lastRaw.direccion),
        throttle: failsafe ? 0 : throttle,
        brake: failsafe ? 0 : Math.round(255 * lastRaw.freno),
        buttons: { ...buttons },
        gear,
        input_device: "Volante simulado",
        camera_url: config.camara.url,
        camera_generation: 0,
        camera_rotation: config.camara.rotacion,
        camera_mirror: config.camara.espejo,
        axes_raw: { ...lastRaw },
      };
    },

    async set_keys(keys) {
      console.debug("set_keys", JSON.stringify(keys));
    },

    async toggle_button(name) {
      buttons[name] = !buttons[name];
    },

    async get_config() {
      return structuredClone(config);
    },

    async save_config(data) {
      config = structuredClone(data);
      return { ok: true };
    },

    async start_axis_calibration(axis) {
      ranges[axis] = { min: lastRaw[axis], max: lastRaw[axis] };
    },

    async finish_axis_calibration(axis) {
      const range = ranges[axis];
      delete ranges[axis];
      Object.assign(config.ejes[axis], range);
      return range;
    },

    async capture_next_button(timeoutS) {
      await delay(Math.min(1500, timeoutS * 1000));
      return Math.floor(Math.random() * 12);
    },

    async test_connection() {
      await delay(300);
      return secondsRunning() % 30 > 26 ? null : 6;
    },

    async toggle_fullscreen() {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await document.documentElement.requestFullscreen();
      }
    },
  };
}
