// Captura de teclado para manejar.

function hasModifier(event) {
  return event.ctrlKey || event.altKey || event.metaKey;
}

export function createKeyboard(api) {
  const pressed = new Set();
  let enabled = false;
  let sending = false;
  let queued = null;

  // pywebview atiende cada llamada en su propio hilo y podrían llegar en desorden. Si un
  // set_keys([]) adelantara al anterior, el carrito quedaría acelerando. Por eso va una
  // sola llamada a la vez y, mientras tanto, solo se conserva la lista más reciente.
  async function send(keys) {
    queued = keys;
    if (sending) return;

    sending = true;
    while (queued !== null) {
      const next = queued;
      queued = null;
      try {
        await api.set_keys(next);
      } catch (error) {
        console.error("Falló set_keys", error);
      }
    }
    sending = false;
  }

  function release() {
    pressed.clear();
    send([]);
  }

  function onKeyDown(event) {
    if (event.code === "F11") {
      event.preventDefault();
      if (!event.repeat) api.toggle_fullscreen();
      return;
    }

    if (!enabled) return;

    // Evita que la barra espaciadora o las flechas activen botones o desplacen la página.
    // Tab y los atajos del sistema siguen funcionando.
    if (!hasModifier(event) && event.code !== "Tab") event.preventDefault();

    if (event.repeat || pressed.has(event.code)) return;
    pressed.add(event.code);
    send([...pressed]);
  }

  function onKeyUp(event) {
    if (!enabled) return;
    if (pressed.delete(event.code)) send([...pressed]);
  }

  function onVisibilityChange() {
    if (document.hidden) release();
  }

  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("keyup", onKeyUp);
  window.addEventListener("blur", release);
  document.addEventListener("visibilitychange", onVisibilityChange);

  return {
    // Solo la vista de manejo envía teclas. Cada cambio de vista suelta todo.
    setEnabled(value) {
      enabled = value;
      release();
    },
  };
}
