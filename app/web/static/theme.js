/**
 * Tema claro u oscuro.
 *
 * Tres opciones: seguir al sistema (lo de siempre), claro o oscuro. Lo que se
 * elige a mano manda sobre lo que dice el sistema, y se recuerda en el propio
 * navegador —es una preferencia de quien mira la pantalla, no un dato del
 * negocio—.
 *
 * El tema se aplica al cargar esta hoja y no al terminar de leer el documento:
 * esperar dejaría ver un destello claro antes de pintar el oscuro. Por eso el
 * `<script>` va en la cabecera y sin `defer`.
 */
(() => {
  "use strict";

  const STORAGE_KEY = "chatbox.theme";
  //: `auto` no escribe nada en el documento: sin atributo, manda la consulta
  //: `prefers-color-scheme` de la hoja de estilos.
  const MODES = ["auto", "light", "dark"];
  const FALLBACK = "auto";

  function readStored() {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return MODES.includes(stored) ? stored : FALLBACK;
    } catch {
      // Navegación privada o almacenamiento bloqueado: se sigue al sistema.
      return FALLBACK;
    }
  }

  let current = readStored();

  function apply() {
    const root = document.documentElement;
    if (current === FALLBACK) {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", current);
    }
  }

  // Antes del primer pintado, para que no se vea el cambio.
  apply();

  function setTheme(mode) {
    if (!MODES.includes(mode)) {
      return;
    }
    current = mode;
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // Se pierde al cerrar, pero la pantalla en curso ya quedó como se pidió.
    }
    apply();
  }

  //: Un símbolo por modo: el botón muestra el que está puesto.
  const ICONS = { auto: "🌓", light: "☀️", dark: "🌙" };

  /**
   * Convierte un botón en el conmutador del tema.
   *
   * Un botón y no un desplegable: cambiar de tema es un gesto que se hace al
   * vuelo —entra el sol, se hace de noche— y no merece abrir una lista. Cada
   * pulsación pasa al siguiente modo, y el rótulo emergente dice cuál es,
   * porque un símbolo suelto no basta para saberlo.
   */
  function mountToggle(button) {
    if (!button) {
      return;
    }
    const render = () => {
      button.textContent = ICONS[current];
      const name = window.i18n ? i18n.t(`theme.${current}`) : current;
      const label = window.i18n ? `${i18n.t("theme.label")}: ${name}` : name;
      button.title = label;
      button.setAttribute("aria-label", label);
    };

    button.addEventListener("click", () => {
      setTheme(MODES[(MODES.indexOf(current) + 1) % MODES.length]);
      render();
    });
    document.addEventListener("languagechange", render);
    render();
  }

  /** Rellena un `<select>` con las tres opciones y lo deja escuchando. */
  function mountSelector(select) {
    if (!select) {
      return;
    }
    const render = () => {
      const chosen = select.value || current;
      select.textContent = "";
      MODES.forEach((mode) => {
        const option = document.createElement("option");
        option.value = mode;
        // Los rótulos salen del diccionario: el tema también se lee en los
        // tres idiomas.
        option.textContent = window.i18n ? i18n.t(`theme.${mode}`) : mode;
        select.appendChild(option);
      });
      select.value = chosen;
    };

    render();
    select.addEventListener("change", () => setTheme(select.value));
    // Al cambiar de idioma se reescriben los rótulos, conservando la elección.
    document.addEventListener("languagechange", render);
  }

  window.theme = { setTheme, mountSelector, mountToggle, current: () => current, MODES };
})();
