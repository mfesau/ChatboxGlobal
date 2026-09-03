/**
 * Cliente del chatbox web.
 *
 * Responsabilidades: exigir registro o login antes de nada, mantener el
 * WebSocket con reconexión, componer el hilo, ampliar el redactor conforme se
 * escribe y reflejar la transmisión por fragmentos que produce la capa de IA.
 */
(() => {
  "use strict";

  const WIDTHS = ["comfortable", "wide", "full"];
  const MAX_RECONNECT_DELAY_MS = 15000;
  const CHAR_LIMIT = 4000;

  const dom = {
    langSelect: document.getElementById("lang-select"),
    gate: document.getElementById("gate"),
    shell: document.getElementById("shell"),
    identityName: document.getElementById("identity-name"),
    thread: document.getElementById("thread"),
    emptyState: document.getElementById("empty-state"),
    suggestions: document.getElementById("suggestions"),
    quickReplies: document.getElementById("quick-replies"),
    typing: document.getElementById("typing"),
    form: document.getElementById("composer-form"),
    input: document.getElementById("composer-input"),
    sendButton: document.getElementById("send-button"),
    attachButton: document.getElementById("attach-button"),
    attachInput: document.getElementById("attach-input"),
    charCount: document.getElementById("char-count"),
    statusDot: document.getElementById("status-dot"),
    statusText: document.getElementById("status-text"),
    widthToggle: document.getElementById("width-toggle"),
    resetButton: document.getElementById("reset-button"),
    logoutButton: document.getElementById("logout-button"),
    loginForm: document.getElementById("login-form"),
    loginEmail: document.getElementById("login-email"),
    loginPassword: document.getElementById("login-password"),
    loginError: document.getElementById("login-error"),
    loginButton: document.getElementById("login-button"),
    ssoSeparator: document.getElementById("sso-separator"),
    ssoButton: document.getElementById("sso-button"),
    ssoNote: document.getElementById("sso-note"),
    googleSsoButton: document.getElementById("google-sso-button"),
    showRegister: document.getElementById("show-register"),
    registerForm: document.getElementById("register-form"),
    registerName: document.getElementById("register-name"),
    registerEmail: document.getElementById("register-email"),
    registerPassword: document.getElementById("register-password"),
    registerError: document.getElementById("register-error"),
    registerButton: document.getElementById("register-button"),
    showLogin: document.getElementById("show-login"),
  };

  // El visitante llega sin nada configurado: la ventana se abre en su idioma
  // en vez de en el nuestro. El selector queda igualmente por si prefiere otro.
  i18n.useBrowserLanguage();
  i18n.mountSelector(document.getElementById("lang-select"));
  theme.mountToggle(document.getElementById("theme-toggle"));
  document.addEventListener("languagechange", () => updateHeaderIdentity());

  const state = {
    socket: null,
    reconnectAttempt: 0,
    streamingBubble: null,
    typingTimer: null,
    clientName: null,
    pendingAttachment: null,
  };

  /* --------------------------------------------------------------------- API */

  async function api(path, options = {}) {
    const response = await fetch(path, {
      // La sesión viaja en una cookie `HttpOnly`; hace falta enviarla siempre.
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (response.status === 401) {
      showGate();
      const detail = await response.text();
      throw new Error(extractDetail(detail, response.status, "Sesión no válida"));
    }
    if (!response.ok) {
      const detail = await response.text();
      const error = new Error(extractDetail(detail, response.status));
      error.status = response.status;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }

  /* Un adjunto se muestra según lo que sea. Antes solo se contemplaba la
     imagen, de modo que un vídeo o un audio dejaban la burbuja vacía y no había
     ni forma de descargarlos. El documento no se puede previsualizar, así que
     al menos se ofrece como enlace con su nombre. */
  function renderAttachment(attachment) {
    if (!attachment.url) {
      return null;
    }
    if (attachment.content_type === "image") {
      const img = document.createElement("img");
      img.src = attachment.url;
      img.alt = attachment.filename || "Imagen adjunta";
      return img;
    }
    if (attachment.content_type === "video" || attachment.content_type === "audio") {
      const media = document.createElement(
        attachment.content_type === "video" ? "video" : "audio",
      );
      media.src = attachment.url;
      media.controls = true;
      // Sin `preload` el navegador se descarga cada adjunto del hilo al
      // abrirlo; con los metadatos basta para pintar la barra de reproducción.
      media.preload = "metadata";
      media.className = "bubble__media";
      return media;
    }
    const link = document.createElement("a");
    link.href = attachment.url;
    link.textContent = attachment.filename || "Archivo adjunto";
    link.className = "bubble__file";
    link.target = "_blank";
    link.rel = "noopener";
    return link;
  }

  function extractDetail(body, status, fallback) {
    try {
      return JSON.parse(body).detail || fallback || `Error ${status}`;
    } catch {
      return fallback || `Error ${status}`;
    }
  }

  /* ------------------------------------------------------------------ acceso */

  function showGate() {
    dom.gate.hidden = false;
    dom.shell.hidden = true;
    if (state.socket) {
      state.socket.close();
      state.socket = null;
    }
  }

  function showApp() {
    dom.gate.hidden = true;
    dom.shell.hidden = false;
  }

  function updateHeaderIdentity({ control, assigneeName } = {}) {
    if (control === "human" && assigneeName) {
      dom.identityName.textContent = assigneeName;
      return;
    }
    dom.identityName.textContent = state.clientName
      ? i18n.t("chat.customerNamed", { name: state.clientName })
      : i18n.t("chat.client");
  }

  async function hasAgentSession() {
    // "/" es la entrada única: quien ya tiene abierta una sesión de equipo
    // (por ejemplo, tras iniciar sesión con SSO) va directo a la consola en
    // vez de ver el chatbox. Con `fetch` liso, no con `api()`, para no
    // disparar `showGate()` ante el 401 esperado de quien no es agente.
    try {
      const response = await fetch("/api/auth/me", { credentials: "same-origin" });
      if (!response.ok) {
        return false;
      }
      const data = await response.json();
      return Boolean(data.agent);
    } catch {
      return false;
    }
  }

  async function checkSso() {
    try {
      const info = await api("/api/auth/sso");
      const hayEquipo = info.available || info.google_available;
      dom.ssoSeparator.hidden = !hayEquipo;
      // La aclaración de que es el acceso del personal salía antes entre
      // paréntesis dentro del propio botón. Google pide no alterar el rótulo
      // de su botón, así que la aclaración vive debajo y vale para los dos.
      dom.ssoNote.hidden = !hayEquipo;
      dom.ssoButton.hidden = !info.available;
      dom.googleSsoButton.hidden = !info.google_available;
    } catch {
      dom.ssoSeparator.hidden = true;
      dom.ssoNote.hidden = true;
      dom.ssoButton.hidden = true;
      dom.googleSsoButton.hidden = true;
    }
  }

  async function loadIdentity() {
    try {
      const data = await api("/api/contact/me");
      state.clientName = data.contact?.display_name || null;
      updateHeaderIdentity();
      showApp();
      connect();
      return true;
    } catch {
      showGate();
      return false;
    }
  }

  dom.showRegister.addEventListener("click", () => {
    dom.loginForm.hidden = true;
    dom.registerForm.hidden = false;
  });

  dom.showLogin.addEventListener("click", () => {
    dom.registerForm.hidden = true;
    dom.loginForm.hidden = false;
  });

  dom.loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    dom.loginError.hidden = true;
    dom.loginButton.disabled = true;
    try {
      // Un solo formulario para todos: el servidor determina si el correo es
      // de un agente o de un cliente, y a dónde corresponde entrar.
      const data = await api("/api/session/login", {
        method: "POST",
        body: JSON.stringify({
          email: dom.loginEmail.value.trim(),
          password: dom.loginPassword.value,
        }),
      });
      dom.loginPassword.value = "";
      if (data.kind === "agent") {
        window.location.href = data.redirect || "/console";
        return;
      }
      state.clientName = data.contact?.display_name || null;
      updateHeaderIdentity();
      showApp();
      connect();
    } catch (error) {
      dom.loginError.textContent = error.message;
      dom.loginError.hidden = false;
    } finally {
      dom.loginButton.disabled = false;
    }
  });

  dom.registerForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    dom.registerError.hidden = true;
    dom.registerButton.disabled = true;
    try {
      const data = await api("/api/contact/register", {
        method: "POST",
        body: JSON.stringify({
          email: dom.registerEmail.value.trim(),
          display_name: dom.registerName.value.trim() || null,
          password: dom.registerPassword.value,
        }),
      });
      dom.registerPassword.value = "";
      state.clientName = data.contact?.display_name || null;
      updateHeaderIdentity();
      showApp();
      connect();
    } catch (error) {
      dom.registerError.textContent = error.message;
      dom.registerError.hidden = false;
    } finally {
      dom.registerButton.disabled = false;
    }
  });

  dom.logoutButton.addEventListener("click", async () => {
    try {
      await api("/api/contact/logout", { method: "POST" });
    } catch {
      // Aunque falle el cierre en el servidor, el chatbox vuelve al acceso.
    }
    showGate();
  });

  /* ---------------------------------------------------------------- interfaz */

  function setStatus(stateName, label) {
    dom.statusDot.dataset.state = stateName;
    dom.statusText.textContent = label;
  }

  function hideEmptyState() {
    if (dom.emptyState && dom.emptyState.parentNode) {
      dom.emptyState.remove();
    }
  }

  function atBottom() {
    const slack = 80;
    return (
      dom.thread.scrollHeight - dom.thread.scrollTop - dom.thread.clientHeight < slack
    );
  }

  function scrollToEnd(force) {
    if (force || atBottom()) {
      dom.thread.scrollTop = dom.thread.scrollHeight;
    }
  }

  function addBubble(direction, text, meta, attachments) {
    hideEmptyState();
    const stick = atBottom();
    const bubble = document.createElement("div");
    bubble.className = `bubble bubble--${direction}`;
    bubble.textContent = text || "";
    (attachments || []).forEach((attachment) => {
      const nodo = renderAttachment(attachment);
      if (nodo) {
        bubble.appendChild(nodo);
      }
    });
    if (meta) {
      const label = document.createElement("span");
      label.className = "bubble__meta";
      label.textContent = meta;
      bubble.appendChild(label);
    }
    dom.thread.appendChild(bubble);
    scrollToEnd(stick);
    return bubble;
  }

  function renderQuickReplies(options) {
    dom.quickReplies.textContent = "";
    if (!options || options.length === 0) {
      dom.quickReplies.hidden = true;
      return;
    }
    options.forEach((option) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = option.title || option.id;
      button.addEventListener("click", () => {
        dom.quickReplies.hidden = true;
        dom.quickReplies.textContent = "";
        sendMessage(option.title || option.id, { id: option.id });
      });
      dom.quickReplies.appendChild(button);
    });
    dom.quickReplies.hidden = false;
  }

  function showTyping(visible) {
    dom.typing.hidden = !visible;
    clearTimeout(state.typingTimer);
    if (visible) {
      // Red de seguridad: el indicador nunca queda encendido de forma indefinida.
      state.typingTimer = setTimeout(() => {
        dom.typing.hidden = true;
      }, 30000);
    }
  }

  /* ------------------------------------------------------------- WebSocket */

  function connect() {
    // Cierra cualquier conexión anterior antes de abrir otra: sin esto, dos
    // sockets vivos a la vez duplican cada mensaje que llega por el hilo.
    if (state.socket) {
      const stale = state.socket;
      state.socket = null;
      stale.close();
    }

    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    // La cookie de sesión viaja sola; la identidad ya no la aporta el cliente.
    const url = `${scheme}://${window.location.host}/ws/chat`;

    setStatus("connecting", i18n.t("chat.connecting"));
    const socket = new WebSocket(url);
    state.socket = socket;

    socket.addEventListener("open", () => {
      if (state.socket !== socket) return;
      state.reconnectAttempt = 0;
      setStatus("online", i18n.t("chat.online"));
      updateSendButton();
    });

    socket.addEventListener("message", (event) => {
      // Una conexión ya reemplazada no debe seguir dibujando lo que reciba.
      if (state.socket !== socket) return;
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (error) {
        console.warn("Mensaje no interpretable", error);
        return;
      }
      handleEvent(payload);
    });

    socket.addEventListener("close", (event) => {
      if (state.socket !== socket) return;
      if (event.code === 4401) {
        showGate();
        return;
      }
      setStatus("offline", i18n.t("chat.offline"));
      updateSendButton();
      scheduleReconnect();
    });

    socket.addEventListener("error", () => socket.close());
  }

  function scheduleReconnect() {
    state.reconnectAttempt += 1;
    // Retroceso exponencial acotado, para no castigar al servidor tras una caída.
    const delay = Math.min(
      MAX_RECONNECT_DELAY_MS,
      500 * 2 ** Math.min(state.reconnectAttempt, 5),
    );
    setTimeout(connect, delay);
  }

  function handleEvent(payload) {
    switch (payload.type) {
      case "ready":
        (payload.history || []).forEach((row) => {
          if (row.text || (row.attachments || []).length > 0) {
            addBubble(
              row.direction === "inbound" ? "inbound" : "outbound",
              row.text,
              null,
              row.attachments,
            );
          }
        });
        scrollToEnd(true);
        updateHeaderIdentity({ control: payload.control, assigneeName: payload.assignee_name });
        break;

      case "control_changed":
        updateHeaderIdentity({ control: payload.control, assigneeName: payload.assignee_name });
        break;

      case "ack":
        showTyping(true);
        break;

      case "typing":
        showTyping(true);
        break;

      case "delta":
        showTyping(false);
        if (!state.streamingBubble) {
          state.streamingBubble = addBubble("outbound", "");
        }
        state.streamingBubble.textContent += payload.text || "";
        scrollToEnd(false);
        break;

      case "message":
        showTyping(false);
        if (payload.direction === "outbound") {
          const body = payload.message || {};
          // La transmisión por fragmentos ya mostró el texto: se consolida la burbuja.
          if (state.streamingBubble) {
            state.streamingBubble.textContent = body.text || state.streamingBubble.textContent;
            state.streamingBubble = null;
          } else if (body.text || (body.attachments || []).length > 0) {
            addBubble("outbound", body.text, null, body.attachments);
          }
          renderQuickReplies(body.quick_replies);
        }
        break;

      default:
        break;
    }
  }

  /* ----------------------------------------------------------------- envío */

  function randomId() {
    // Identificador de mensaje, no de sesión: solo permite conciliar acuses.
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }

  function sendMessage(text, action) {
    const body = (text || "").trim();
    const attachment = state.pendingAttachment;
    if (!body && !action && !attachment) {
      return;
    }
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
      setStatus("offline", i18n.t("chat.offlineNotSent"));
      return;
    }

    addBubble("inbound", body, null, attachment ? [attachment] : null);
    state.socket.send(
      JSON.stringify({
        type: "message",
        text: body,
        action: action || null,
        attachments: attachment ? [attachment] : [],
        client_message_id: randomId(),
        // El idioma en uso, no el del navegador: si el visitante cambió el
        // selector, es en ese idioma en el que espera que le respondan.
        locale: i18n.current(),
      }),
    );
    clearAttachment();
    showTyping(true);
  }

  /* -------------------------------------------------------------- adjuntos */

  function clearAttachment() {
    state.pendingAttachment = null;
    dom.attachButton.textContent = "📎";
    dom.attachButton.title = "Adjuntar una imagen";
  }

  dom.attachButton.addEventListener("click", () => {
    if (state.pendingAttachment) {
      clearAttachment();
      return;
    }
    dom.attachInput.click();
  });

  dom.attachInput.addEventListener("change", async () => {
    const file = dom.attachInput.files[0];
    dom.attachInput.value = "";
    if (!file) {
      return;
    }
    dom.attachButton.disabled = true;
    try {
      const form = new FormData();
      form.append("file", file);
      // Sin `Content-Type` propio: el navegador fija el límite multipart él
      // mismo; forzar `application/json` (el valor por omisión de `api()`)
      // rompería la subida.
      const attachment = await api("/api/contact/uploads", {
        method: "POST",
        headers: {},
        body: form,
      });
      state.pendingAttachment = attachment;
      dom.attachButton.textContent = "✅";
      dom.attachButton.title = `Imagen lista: ${attachment.filename || "adjunto"}`;
      updateSendButton();
    } catch (error) {
      clearAttachment();
      dom.attachButton.title = `No se pudo subir la imagen — ${error.message}`;
    } finally {
      dom.attachButton.disabled = false;
    }
  });

  function autoGrow() {
    // El textarea crece con el contenido; el tope lo fija `max-height` en CSS.
    dom.input.style.height = "auto";
    dom.input.style.height = `${dom.input.scrollHeight}px`;
  }

  function updateSendButton() {
    const hasText = dom.input.value.trim().length > 0;
    const connected = state.socket && state.socket.readyState === WebSocket.OPEN;
    dom.sendButton.disabled = (!hasText && !state.pendingAttachment) || !connected;
    dom.charCount.textContent = `${dom.input.value.length} / ${CHAR_LIMIT}`;
  }

  /* -------------------------------------------------------------- escuchas */

  dom.form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = dom.input.value;
    dom.input.value = "";
    autoGrow();
    updateSendButton();
    sendMessage(text);
  });

  dom.input.addEventListener("input", () => {
    autoGrow();
    updateSendButton();
  });

  dom.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      dom.form.requestSubmit();
    }
  });

  if (dom.suggestions) {
    dom.suggestions.addEventListener("click", (event) => {
      const suggestion = event.target.closest("[data-suggestion]");
      if (suggestion) {
        sendMessage(suggestion.dataset.suggestion);
      }
    });
  }

  dom.widthToggle.addEventListener("click", () => {
    const current = dom.shell.dataset.width || WIDTHS[0];
    const next = WIDTHS[(WIDTHS.indexOf(current) + 1) % WIDTHS.length];
    dom.shell.dataset.width = next;
    localStorage.setItem("chatbox.width", next);
  });

  dom.resetButton.addEventListener("click", () => {
    dom.thread.textContent = "";
    renderQuickReplies([]);
    if (state.socket) {
      state.socket.close();
    }
    connect();
  });

  /* ---------------------------------------------------------------- arranque */

  const storedWidth = localStorage.getItem("chatbox.width");
  if (storedWidth && WIDTHS.includes(storedWidth)) {
    dom.shell.dataset.width = storedWidth;
  }

  autoGrow();
  updateSendButton();

  (async () => {
    if (await hasAgentSession()) {
      window.location.href = "/console";
      return;
    }
    await checkSso();
    await loadIdentity();
  })();
})();
