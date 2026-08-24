/**
 * Consola del equipo.
 *
 * Tres bandejas: la cola común por la que entra todo, la cartera propia de cada
 * agente y —solo para supervisión— la totalidad de las conversaciones. Derivar a
 * un compañero no mueve el hilo: cambia el responsable y el historial permanece.
 */
(() => {
  "use strict";

  const dom = {
    gate: document.getElementById("gate"),
    app: document.getElementById("app"),
    loginForm: document.getElementById("login-form"),
    loginEmail: document.getElementById("login-email"),
    loginPassword: document.getElementById("login-password"),
    loginError: document.getElementById("login-error"),
    loginButton: document.getElementById("login-button"),
    logout: document.getElementById("logout-button"),
    who: document.getElementById("who"),
    whoRole: document.getElementById("who-role"),
    tabs: document.querySelectorAll(".console__tabs button"),
    tabAll: document.getElementById("tab-all"),
    counts: {
      unassigned: document.getElementById("count-unassigned"),
      mine: document.getElementById("count-mine"),
      all: document.getElementById("count-all"),
    },
    statusFilter: document.getElementById("status-filter"),
    channelFilter: document.getElementById("channel-filter"),
    departmentFilter: document.getElementById("department-filter"),
    list: document.getElementById("conversation-list"),
    thread: document.getElementById("console-thread"),
    title: document.getElementById("thread-title"),
    subtitle: document.getElementById("thread-subtitle"),
    threadActions: document.getElementById("thread-actions"),
    claim: document.getElementById("claim-button"),
    release: document.getElementById("release-button"),
    close: document.getElementById("close-button"),
    aside: document.getElementById("aside"),
    asideToggle: document.getElementById("aside-toggle"),
    transferTarget: document.getElementById("transfer-target"),
    transferDepartment: document.getElementById("transfer-department"),
    transferNote: document.getElementById("transfer-note"),
    transfer: document.getElementById("transfer-button"),
    notesList: document.getElementById("notes-list"),
    noteForm: document.getElementById("note-form"),
    noteBody: document.getElementById("note-body"),
    assignments: document.getElementById("assignment-list"),
    form: document.getElementById("console-form"),
    input: document.getElementById("console-input"),
    send: document.getElementById("console-send"),
    attachButton: document.getElementById("console-attach-button"),
    attachInput: document.getElementById("console-attach-input"),
    status: document.getElementById("console-status"),
    contactName: document.getElementById("contact-name"),
    contactPhone: document.getElementById("contact-phone"),
    contactEmail: document.getElementById("contact-email"),
    contactSave: document.getElementById("contact-save"),
    contactError: document.getElementById("contact-error"),
    contactComments: document.getElementById("contact-comments"),
    contactCommentForm: document.getElementById("contact-comment-form"),
    contactCommentBody: document.getElementById("contact-comment-body"),
    supervisorButton: document.getElementById("supervisor-button"),
    supervisorPanel: document.getElementById("supervisor-panel"),
    supervisorClose: document.getElementById("supervisor-close"),
    workload: document.querySelector("#workload-table tbody"),
    transferActivity: document.getElementById("transfer-activity"),
    adminButton: document.getElementById("admin-button"),
    adminPanel: document.getElementById("admin-panel"),
    adminClose: document.getElementById("admin-close"),
    autoReplyForm: document.getElementById("auto-reply-form"),
    autoReplyText: document.getElementById("auto-reply-text"),
    createUserForm: document.getElementById("create-user-form"),
    newUserEmail: document.getElementById("new-user-email"),
    newUserName: document.getElementById("new-user-name"),
    newUserRole: document.getElementById("new-user-role"),
    newUserDepartment: document.getElementById("new-user-department"),
    newUserPassword: document.getElementById("new-user-password"),
    resetPasswordForm: document.getElementById("reset-password-form"),
    passwordUser: document.getElementById("password-user"),
    resetPassword: document.getElementById("reset-password"),
    adminError: document.getElementById("admin-error"),
    adminUsers: document.querySelector("#admin-users-table tbody"),
    createDepartmentForm: document.getElementById("create-department-form"),
    newDepartmentName: document.getElementById("new-department-name"),
    departmentList: document.getElementById("department-list"),
    agentDepartmentsForm: document.getElementById("agent-departments-form"),
    departmentsUser: document.getElementById("departments-user"),
    departmentsPrimary: document.getElementById("departments-primary"),
    departmentsExtra: document.getElementById("departments-extra"),
  };

  const state = {
    me: null,
    scope: "unassigned",
    conversations: [],
    selected: null,
    agents: [],
    departments: [],
    socket: null,
    pendingAttachment: null,
    contact: null,
  };

  const CHANNEL_LABELS = {
    whatsapp: "WhatsApp",
    msbot: "Microsoft",
    web: "Web",
    internal: "Interno",
  };

  /* ------------------------------------------------------------------- API */

  async function api(path, options = {}) {
    const response = await fetch(path, {
      // La sesión viaja en una cookie `HttpOnly`; hace falta enviarla siempre.
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (response.status === 401) {
      showGate();
      throw new Error("Sesión no válida");
    }
    if (!response.ok) {
      const detail = await response.text();
      const error = new Error(extractDetail(detail, response.status));
      error.status = response.status;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }

  function extractDetail(body, status) {
    try {
      return JSON.parse(body).detail || `Error ${status}`;
    } catch {
      return `Error ${status}`;
    }
  }

  function formatTime(iso) {
    if (!iso) {
      return "";
    }
    const date = new Date(iso);
    return Number.isNaN(date.getTime())
      ? ""
      : date.toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
  }

  function setStatus(text) {
    dom.status.textContent = text;
  }

  function isWideLayout() {
    // El mismo umbral que emplea la hoja de estilos para el cambio de columna.
    return window.matchMedia("(min-width: 1181px)").matches;
  }

  /* ---------------------------------------------------------------- acceso */

  function showGate() {
    dom.gate.hidden = false;
    dom.app.hidden = true;
    if (state.socket) {
      state.socket.close();
      state.socket = null;
    }
  }

  function showApp() {
    dom.gate.hidden = true;
    dom.app.hidden = false;
  }

  async function loadIdentity() {
    try {
      const me = await api("/api/auth/me");
      state.me = me;
      const label = me.agent ? me.agent.display_name || me.agent.email : "Acceso de servicio";
      dom.who.textContent = label;
      dom.whoRole.textContent = me.is_supervisor ? "Supervisión" : "Agente";
      dom.tabAll.hidden = !me.is_supervisor;
      dom.supervisorButton.hidden = !me.is_supervisor;
      dom.adminButton.hidden = me.role !== "admin";
      showApp();
      return true;
    } catch {
      showGate();
      return false;
    }
  }

  dom.loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    dom.loginError.hidden = true;
    dom.loginButton.disabled = true;
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({
          email: dom.loginEmail.value.trim(),
          password: dom.loginPassword.value,
        }),
      });
      dom.loginPassword.value = "";
      await start();
    } catch (error) {
      dom.loginError.textContent = error.message;
      dom.loginError.hidden = false;
    } finally {
      dom.loginButton.disabled = false;
    }
  });

  dom.logout.addEventListener("click", async () => {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch {
      // Aunque falle el cierre en el servidor, la consola vuelve al acceso.
    }
    state.me = null;
    showGate();
  });

  /* -------------------------------------------------------------- bandeja */

  async function loadCounts() {
    try {
      const summary = await api("/api/inbox/summary");
      dom.counts.unassigned.textContent = summary.unassigned ?? 0;
      dom.counts.mine.textContent = summary.mine ?? 0;
      dom.counts.all.textContent = summary.all ?? 0;
    } catch (error) {
      setStatus(`No se pudieron leer los contadores — ${error.message}`);
    }
  }

  //: Petición de bandeja en curso, para no repetirla. Una sola acción provoca
  //: tanto la recarga local como el aviso por WebSocket; sin esta unificación se
  //: dispararían tres consultas idénticas por cada derivación.
  let cargaEnCurso = null;

  async function loadConversations() {
    if (cargaEnCurso) {
      return cargaEnCurso;
    }
    cargaEnCurso = fetchConversations().finally(() => {
      cargaEnCurso = null;
    });
    return cargaEnCurso;
  }

  async function fetchConversations() {
    const params = new URLSearchParams({ scope: state.scope });
    if (dom.statusFilter.value) {
      params.set("status", dom.statusFilter.value);
    }
    if (dom.channelFilter.value) {
      params.set("channel", dom.channelFilter.value);
    }
    if (dom.departmentFilter.value) {
      params.set("department", dom.departmentFilter.value);
    }
    try {
      state.conversations = await api(`/api/conversations?${params}`);
      renderConversations();
      await loadCounts();
    } catch (error) {
      setStatus(`No se pudo cargar la bandeja — ${error.message}`);
    }
  }

  function renderConversations() {
    dom.list.textContent = "";
    if (state.conversations.length === 0) {
      const empty = document.createElement("li");
      empty.className = "console__muted";
      empty.style.padding = "16px";
      empty.textContent =
        state.scope === "unassigned"
          ? "La cola común está vacía. Buen trabajo."
          : "No hay conversaciones con estos filtros.";
      dom.list.appendChild(empty);
      return;
    }

    state.conversations.forEach((conversation) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "console__item";
      button.setAttribute(
        "aria-current",
        String(state.selected && conversation.id === state.selected.id),
      );

      const top = document.createElement("div");
      top.className = "console__item-top";
      const name = document.createElement("span");
      name.className = "console__item-name";
      name.textContent = conversation.contact_name || "Contacto sin nombre";
      top.appendChild(name);

      if (!conversation.assignee_id) {
        top.appendChild(badge("En cola", "console__badge--pool"));
      } else if (conversation.assignee_id === state.me?.agent?.id) {
        top.appendChild(badge("Mía", "console__badge--human"));
      }
      if (conversation.unread_count > 0) {
        top.appendChild(badge(String(conversation.unread_count), "console__badge--unread"));
      }
      button.appendChild(top);

      const meta = document.createElement("p");
      meta.className = "console__muted";
      const channel = CHANNEL_LABELS[conversation.channel] || conversation.channel;
      const owner = conversation.assignee_name ? ` · ${conversation.assignee_name}` : "";
      const department = conversation.department_name ? ` · ${conversation.department_name}` : "";
      meta.textContent =
        `${channel}${department}${owner} · ${formatTime(conversation.last_message_at)}`;
      button.appendChild(meta);

      button.addEventListener("click", () => selectConversation(conversation));
      item.appendChild(button);
      dom.list.appendChild(item);
    });
  }

  function badge(text, extraClass) {
    const element = document.createElement("span");
    element.className = `console__badge ${extraClass || ""}`.trim();
    element.textContent = text;
    return element;
  }

  /* ----------------------------------------------------------------- hilo */

  async function selectConversation(conversation) {
    state.selected = conversation;
    dom.title.textContent = conversation.contact_name || "Contacto sin nombre";
    dom.threadActions.hidden = false;
    // En pantalla ancha el panel es una columna y se muestra siempre. En
    // pantalla estrecha es un cajón que taparía el hilo, de modo que se abre
    // solo cuando el agente lo pide con el botón «Equipo».
    dom.aside.hidden = !isWideLayout();
    dom.input.disabled = false;
    dom.attachButton.disabled = false;
    renderOwnership();
    renderConversations();
    await Promise.all([loadMessages(), loadNotes(), loadAssignments(), loadContact()]);
  }

  function renderOwnership() {
    const conversation = state.selected;
    if (!conversation) {
      return;
    }
    const channel = CHANNEL_LABELS[conversation.channel] || conversation.channel;
    const mine = conversation.assignee_id === state.me?.agent?.id;
    // El responsable se determina por identificador: un nombre ausente no
    // significa que la conversación esté en la cola común.
    let owner = "sin asignar (cola común)";
    if (mine) {
      owner = "usted";
    } else if (conversation.assignee_id) {
      owner = conversation.assignee_name || "otro compañero";
    }
    const department = conversation.department_name
      ? ` · departamento: ${conversation.department_name}`
      : "";
    dom.subtitle.textContent =
      `${channel} · responsable: ${owner}${department} · atención: ${conversation.control}`;
    dom.claim.hidden = mine;
    dom.release.hidden = !conversation.assignee_id;
    dom.transfer.disabled = state.agents.length === 0;
  }

  async function loadMessages() {
    if (!state.selected) {
      return;
    }
    try {
      const messages = await api(`/api/conversations/${state.selected.id}/messages`);
      dom.thread.textContent = "";
      messages.forEach((message) => {
        // El cliente queda a la izquierda; el equipo, a la derecha.
        const side = message.direction === "inbound" ? "outbound" : "inbound";
        const bubble = document.createElement("div");
        bubble.className = `bubble bubble--${side}`;
        bubble.textContent = message.text
          || (message.attachments?.length ? "" : `[${message.content_type}]`);
        (message.attachments || []).forEach((attachment) => {
          if (attachment.content_type === "image" && attachment.url) {
            const img = document.createElement("img");
            img.src = attachment.url;
            img.alt = attachment.filename || "Imagen adjunta";
            bubble.appendChild(img);
          }
        });
        const meta = document.createElement("span");
        meta.className = "bubble__meta";
        meta.textContent = `${authorLabel(message)} · ${message.status} · ${formatTime(
          message.created_at,
        )}`;
        bubble.appendChild(meta);
        dom.thread.appendChild(bubble);
      });
      dom.thread.scrollTop = dom.thread.scrollHeight;
      setStatus(`${messages.length} mensajes en el historial`);
      updateSend();
    } catch (error) {
      handleThreadError(error, `No se pudo cargar el hilo — ${error.message}`);
    }
  }

  function authorLabel(message) {
    switch (message.author_type) {
      case "contact":
        return "cliente";
      case "bot":
        return "asistente";
      case "agent":
        return "equipo";
      default:
        return message.author_type;
    }
  }

  async function loadNotes() {
    try {
      const notes = await api(`/api/conversations/${state.selected.id}/notes`);
      dom.notesList.textContent = "";
      if (notes.length === 0) {
        const empty = document.createElement("li");
        empty.className = "console__muted";
        empty.textContent = "Todavía no hay notas.";
        dom.notesList.appendChild(empty);
        return;
      }
      notes.forEach((note) => {
        const item = document.createElement("li");
        item.textContent = note.body;
        const meta = document.createElement("span");
        meta.className = "panel__meta";
        meta.textContent = `${note.agent || "sistema"} · ${formatTime(note.created_at)}`;
        item.appendChild(meta);
        dom.notesList.appendChild(item);
      });
    } catch (error) {
      handleThreadError(error, `No se pudieron leer las notas — ${error.message}`);
    }
  }

  async function loadAssignments() {
    try {
      const entries = await api(`/api/conversations/${state.selected.id}/assignments`);
      dom.assignments.textContent = "";
      if (entries.length === 0) {
        const empty = document.createElement("li");
        empty.className = "console__muted";
        empty.textContent = "Sin derivaciones: sigue en la cola común.";
        dom.assignments.appendChild(empty);
        return;
      }
      entries.forEach((entry) => {
        const item = document.createElement("li");
        item.textContent = describeAssignment(entry);
        const meta = document.createElement("span");
        meta.className = "panel__meta";
        meta.textContent = `${entry.by_agent || "sistema"} · ${formatTime(entry.created_at)}`;
        item.appendChild(meta);
        if (entry.note) {
          const note = document.createElement("span");
          note.className = "panel__meta";
          note.textContent = `«${entry.note}»`;
          item.appendChild(note);
        }
        dom.assignments.appendChild(item);
      });
    } catch (error) {
      handleThreadError(error, `No se pudo leer el historial — ${error.message}`);
    }
  }

  async function loadContact() {
    const canEdit = Boolean(state.me?.is_supervisor);
    dom.contactError.hidden = true;
    dom.contactSave.hidden = !canEdit;
    dom.contactCommentForm.hidden = !canEdit;

    try {
      const contact = await api(`/api/conversations/${state.selected.id}/contact`);
      state.contact = contact;
      dom.contactName.value = contact.display_name || "";
      dom.contactPhone.value = contact.primary_phone || "";
      dom.contactEmail.value = contact.primary_email || "";
      dom.contactName.disabled = !canEdit;
      dom.contactPhone.disabled = !canEdit;
      dom.contactEmail.disabled = !canEdit;

      dom.contactComments.textContent = "";
      if (contact.comments.length === 0) {
        const empty = document.createElement("li");
        empty.className = "console__muted";
        empty.textContent = "Todavía no hay comentarios.";
        dom.contactComments.appendChild(empty);
      } else {
        contact.comments.forEach((comment) => {
          const item = document.createElement("li");
          item.textContent = comment.body;
          const meta = document.createElement("span");
          meta.className = "panel__meta";
          meta.textContent = `${comment.agent || "sistema"} · ${formatTime(comment.created_at)}`;
          item.appendChild(meta);
          dom.contactComments.appendChild(item);
        });
      }
    } catch (error) {
      // Poco común: un hilo interno sin contacto asociado. No es un fallo del
      // resto del panel, así que no se toca la selección de la conversación.
      state.contact = null;
      dom.contactName.value = "";
      dom.contactPhone.value = "";
      dom.contactEmail.value = "";
      dom.contactName.disabled = true;
      dom.contactPhone.disabled = true;
      dom.contactEmail.disabled = true;
      dom.contactSave.hidden = true;
      dom.contactCommentForm.hidden = true;
      dom.contactComments.textContent = "";
      if (error.status !== 404) {
        dom.contactError.textContent = `No se pudo cargar el contacto — ${error.message}`;
        dom.contactError.hidden = false;
      }
    }
  }

  dom.contactSave.addEventListener("click", async () => {
    if (!state.selected) {
      return;
    }
    dom.contactError.hidden = true;
    dom.contactSave.disabled = true;
    try {
      await api(`/api/conversations/${state.selected.id}/contact`, {
        method: "PATCH",
        body: JSON.stringify({
          display_name: dom.contactName.value.trim() || null,
          primary_phone: dom.contactPhone.value.trim() || null,
          primary_email: dom.contactEmail.value.trim() || null,
        }),
      });
      await loadContact();
      renderConversations();
    } catch (error) {
      dom.contactError.textContent = error.message;
      dom.contactError.hidden = false;
    } finally {
      dom.contactSave.disabled = false;
    }
  });

  dom.contactCommentForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = dom.contactCommentBody.value.trim();
    if (!body || !state.selected) {
      return;
    }
    try {
      await api(`/api/conversations/${state.selected.id}/contact/comments`, {
        method: "POST",
        body: JSON.stringify({ body }),
      });
      dom.contactCommentBody.value = "";
      await loadContact();
    } catch (error) {
      dom.contactError.textContent = error.message;
      dom.contactError.hidden = false;
    }
  });

  function describeAssignment(entry) {
    switch (entry.action) {
      case "claim":
        return `${entry.to_agent} tomó la conversación`;
      case "transfer":
        return `Derivada de ${entry.from_agent || "la cola"} a ${entry.to_agent}`;
      case "release":
        return `${entry.from_agent || "El equipo"} la devolvió a la cola`;
      case "close":
        return "Conversación cerrada";
      case "reopen":
        return "Conversación reabierta";
      default:
        return entry.action;
    }
  }

  /* ------------------------------------------------------------- acciones */

  function clearSelection(message) {
    state.selected = null;
    dom.threadActions.hidden = true;
    dom.aside.hidden = true;
    dom.thread.textContent = "";
    dom.title.textContent = "Seleccione una conversación";
    dom.subtitle.textContent = "";
    dom.input.disabled = true;
    dom.attachButton.disabled = true;
    clearAttachment();
    updateSend();
    setStatus(message);
  }

  function handleThreadError(error, fallback) {
    // Un 404 sobre el hilo abierto no es un fallo: alguien lo derivó o lo tomó
    // mientras estaba a la vista. Se informa con naturalidad y se limpia.
    if (error.status === 404) {
      clearSelection("La conversación ya la atiende otro compañero.");
      return;
    }
    setStatus(fallback);
  }

  function stillVisibleToMe(assigneeId) {
    // Un agente conserva acceso a lo propio y a lo que queda en la cola común;
    // supervisión lo conserva siempre. Se decide con la respuesta del servidor,
    // sin una petición extra que además fallaría con 404.
    if (state.me?.is_supervisor) {
      return true;
    }
    return !assigneeId || assigneeId === state.me?.agent?.id;
  }

  async function act(path, body, successMessage) {
    if (!state.selected) {
      return;
    }
    try {
      const result = await api(`/api/conversations/${state.selected.id}/${path}`, {
        method: "POST",
        body: JSON.stringify(body || {}),
      });
      const assigneeId = result.assignee_id ?? null;

      if (!stillVisibleToMe(assigneeId)) {
        // Tras derivar, el hilo pasa a la cartera del compañero. No es un error:
        // se informa y se limpia el panel en lugar de pedir datos ya vedados.
        await loadConversations();
        clearSelection(
          `${successMessage} La atiende ahora ${result.assignee_name || "un compañero"}.`,
        );
        return;
      }

      Object.assign(state.selected, {
        assignee_id: assigneeId,
        assignee_name: result.assignee_name ?? null,
        control: result.control ?? state.selected.control,
      });
      renderOwnership();
      await Promise.all([loadConversations(), loadAssignments(), loadNotes()]);
      setStatus(successMessage);
    } catch (error) {
      setStatus(error.message);
    }
  }

  dom.claim.addEventListener("click", () =>
    act("claim", {}, "La conversación es suya; el asistente queda en silencio."),
  );

  dom.release.addEventListener("click", () =>
    act("release", {}, "Devuelta a la cola común; el asistente vuelve a atenderla."),
  );

  dom.close.addEventListener("click", async () => {
    await act("close", {}, "Conversación cerrada.");
    clearSelection("Conversación cerrada. El historial queda guardado.");
    await loadConversations();
  });

  dom.transfer.addEventListener("click", async () => {
    const target = dom.transferTarget.value;
    const department = dom.transferDepartment.value;
    if (!target && !department) {
      setStatus("Elija un compañero o un departamento antes de derivar.");
      return;
    }
    if (target && department) {
      setStatus("Elija solo uno: compañero o departamento, no ambos.");
      return;
    }
    const note = dom.transferNote.value.trim();
    const body = target
      ? { to_agent_id: target, note: note || null }
      : { to_department_id: department, note: note || null };
    const successMessage = target
      ? "Derivada con todo el historial."
      : "Derivada a la cola del departamento.";
    dom.transfer.disabled = true;
    await act("transfer", body, successMessage);
    dom.transferNote.value = "";
    dom.transferTarget.value = "";
    dom.transferDepartment.value = "";
    dom.transfer.disabled = false;
  });

  dom.noteForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = dom.noteBody.value.trim();
    if (!body || !state.selected) {
      return;
    }
    try {
      await api(`/api/conversations/${state.selected.id}/notes`, {
        method: "POST",
        body: JSON.stringify({ body }),
      });
      dom.noteBody.value = "";
      await loadNotes();
      setStatus("Nota interna guardada.");
    } catch (error) {
      setStatus(error.message);
    }
  });

  dom.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = dom.input.value.trim();
    const attachment = state.pendingAttachment;
    if (!text && !attachment) {
      return;
    }
    if (!state.selected) {
      return;
    }
    dom.send.disabled = true;
    try {
      await api(`/api/conversations/${state.selected.id}/reply`, {
        method: "POST",
        body: JSON.stringify({ text, attachments: attachment ? [attachment] : [] }),
      });
      dom.input.value = "";
      dom.input.style.height = "auto";
      clearAttachment();
      setStatus("Respuesta encolada para envío.");
      await loadMessages();
    } catch (error) {
      setStatus(error.message);
    } finally {
      updateSend();
    }
  });

  function updateSend() {
    const hasText = dom.input.value.trim().length > 0;
    dom.send.disabled = !state.selected || (!hasText && !state.pendingAttachment);
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
      updateSend();
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
      const attachment = await api("/api/uploads", { method: "POST", headers: {}, body: form });
      state.pendingAttachment = attachment;
      dom.attachButton.textContent = "✅";
      dom.attachButton.title = `Imagen lista: ${attachment.filename || "adjunto"}`;
      updateSend();
    } catch (error) {
      clearAttachment();
      dom.attachButton.title = `No se pudo subir la imagen — ${error.message}`;
    } finally {
      dom.attachButton.disabled = false;
    }
  });

  dom.input.addEventListener("input", () => {
    dom.input.style.height = "auto";
    dom.input.style.height = `${dom.input.scrollHeight}px`;
    updateSend();
  });

  dom.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      dom.form.requestSubmit();
    }
  });

  /* -------------------------------------------------------------- pestañas */

  dom.tabs.forEach((tab) =>
    tab.addEventListener("click", async () => {
      state.scope = tab.dataset.scope;
      dom.tabs.forEach((other) =>
        other.setAttribute("aria-selected", String(other === tab)),
      );
      await loadConversations();
    }),
  );

  [dom.statusFilter, dom.channelFilter, dom.departmentFilter].forEach((element) =>
    element.addEventListener("change", loadConversations),
  );

  /* ----------------------------------------------------------- compañeros */

  function buildAdminUserRow(agent) {
    const row = document.createElement("tr");

    const nameCell = document.createElement("td");
    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.maxLength = 160;
    nameInput.value = agent.display_name || "";
    nameInput.setAttribute("aria-label", `Nombre de ${agent.email}`);
    nameCell.appendChild(nameInput);
    row.appendChild(nameCell);

    [agent.email, agent.role, agent.is_active ? "Activo" : "Inactivo"].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    });

    const actionsCell = document.createElement("td");
    const saveButton = document.createElement("button");
    saveButton.type = "button";
    saveButton.className = "ghost-button";
    saveButton.textContent = "Guardar";
    saveButton.addEventListener("click", async () => {
      const displayName = nameInput.value.trim();
      if (!displayName) {
        showAdminError("El nombre no puede quedar vacío.");
        return;
      }
      try {
        await api(`/api/agents/${agent.id}`, {
          method: "PATCH",
          body: JSON.stringify({ display_name: displayName }),
        });
        await loadAgents();
        setStatus("Nombre actualizado.");
      } catch (error) {
        showAdminError(error.message);
      }
    });
    actionsCell.appendChild(saveButton);

    const toggleButton = document.createElement("button");
    toggleButton.type = "button";
    toggleButton.className = "ghost-button";
    toggleButton.textContent = agent.is_active ? "Desactivar" : "Reactivar";
    toggleButton.addEventListener("click", async () => {
      showAdminError("");
      try {
        if (agent.is_active) {
          await api(`/api/agents/${agent.id}`, { method: "DELETE" });
          setStatus("Usuario desactivado.");
        } else {
          await api(`/api/agents/${agent.id}`, {
            method: "PATCH",
            body: JSON.stringify({ is_active: true }),
          });
          setStatus("Usuario reactivado.");
        }
        await loadAgents();
      } catch (error) {
        showAdminError(error.message);
      }
    });
    actionsCell.appendChild(toggleButton);

    row.appendChild(actionsCell);
    return row;
  }

  async function loadAgents() {
    try {
      state.agents = await api("/api/agents");
    } catch {
      state.agents = [];
    }
    dom.transferTarget.textContent = "";
    const mineId = state.me?.agent?.id;
    state.agents
      .filter((agent) => agent.id !== mineId && agent.is_active)
      .forEach((agent) => {
        const option = document.createElement("option");
        option.value = agent.id;
        const presence = agent.presence === "available" ? "•" : "◦";
        option.textContent = `${presence} ${agent.display_name || agent.email}`;
        dom.transferTarget.appendChild(option);
      });
    if (dom.transferTarget.options.length === 0) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No hay compañeros disponibles";
      dom.transferTarget.appendChild(option);
    }
    dom.passwordUser.textContent = "";
    state.agents.forEach((agent) => {
      const option = document.createElement("option");
      option.value = agent.id;
      option.textContent = `${agent.display_name || agent.email} (${agent.role})`;
      dom.passwordUser.appendChild(option);
    });
    dom.adminUsers.textContent = "";
    state.agents.forEach((agent) => dom.adminUsers.appendChild(buildAdminUserRow(agent)));

    dom.departmentsUser.textContent = "";
    state.agents.forEach((agent) => {
      const option = document.createElement("option");
      option.value = agent.id;
      option.textContent = `${agent.display_name || agent.email} (${agent.role})`;
      dom.departmentsUser.appendChild(option);
    });
    if (dom.departmentsUser.options.length > 0) {
      selectAgentDepartments(state.agents[0]);
    }
  }

  /* ------------------------------------------------------------ departamentos */

  async function loadDepartments() {
    try {
      state.departments = await api("/api/departments");
    } catch {
      state.departments = [];
    }

    const fillWithPlaceholder = (select, placeholder) => {
      select.textContent = "";
      const option = document.createElement("option");
      option.value = "";
      option.textContent = placeholder;
      select.appendChild(option);
      state.departments.forEach((department) => {
        const departmentOption = document.createElement("option");
        departmentOption.value = department.id;
        departmentOption.textContent = department.name;
        select.appendChild(departmentOption);
      });
    };

    fillWithPlaceholder(dom.departmentFilter, "Todos los departamentos");
    fillWithPlaceholder(dom.transferDepartment, "— Elegir —");
    fillWithPlaceholder(dom.newUserDepartment, "Sin departamento");
    fillWithPlaceholder(dom.departmentsPrimary, "Sin departamento");

    dom.departmentsExtra.textContent = "";
    state.departments.forEach((department) => {
      const option = document.createElement("option");
      option.value = department.id;
      option.textContent = department.name;
      dom.departmentsExtra.appendChild(option);
    });

    dom.departmentList.textContent = "";
    if (state.departments.length === 0) {
      const empty = document.createElement("li");
      empty.className = "console__muted";
      empty.textContent = "Todavía no hay departamentos.";
      dom.departmentList.appendChild(empty);
    } else {
      state.departments.forEach((department) => {
        const item = document.createElement("li");
        item.textContent = department.name;
        dom.departmentList.appendChild(item);
      });
    }
  }

  function selectAgentDepartments(agent) {
    dom.departmentsPrimary.value = agent.department_id || "";
    Array.from(dom.departmentsExtra.options).forEach((option) => {
      option.selected = agent.extra_department_ids.includes(option.value);
    });
  }

  dom.departmentsUser.addEventListener("change", () => {
    const agent = state.agents.find((candidate) => candidate.id === dom.departmentsUser.value);
    if (agent) {
      selectAgentDepartments(agent);
    }
  });

  dom.createDepartmentForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");
    const name = dom.newDepartmentName.value.trim();
    if (!name) {
      return;
    }
    try {
      await api("/api/departments", { method: "POST", body: JSON.stringify({ name }) });
      dom.newDepartmentName.value = "";
      await loadDepartments();
      setStatus("Departamento creado.");
    } catch (error) {
      showAdminError(error.message);
    }
  });

  dom.agentDepartmentsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");
    const agentId = dom.departmentsUser.value;
    if (!agentId) {
      return;
    }
    const extraIds = Array.from(dom.departmentsExtra.selectedOptions).map((option) => option.value);
    try {
      await api(`/api/agents/${agentId}/departments`, {
        method: "PUT",
        body: JSON.stringify({
          department_id: dom.departmentsPrimary.value || null,
          extra_department_ids: extraIds,
        }),
      });
      await loadAgents();
      // `loadAgents()` reconstruye el <select> y por defecto vuelve a la
      // primera persona; se restituye la que se acababa de editar.
      dom.departmentsUser.value = agentId;
      const agent = state.agents.find((candidate) => candidate.id === agentId);
      if (agent) {
        selectAgentDepartments(agent);
      }
      setStatus("Departamentos actualizados.");
    } catch (error) {
      showAdminError(error.message);
    }
  });

  /* -------------------------------------------------------------- supervisión */

  dom.supervisorButton.addEventListener("click", async () => {
    try {
      const overview = await api("/api/supervisor/overview");
      dom.workload.textContent = "";
      overview.workload.forEach((row) => {
        const tr = document.createElement("tr");
        [
          row.agent,
          row.role || "—",
          row.presence || "—",
          row.open_conversations,
          row.unread,
        ].forEach((value) => {
          const td = document.createElement("td");
          td.textContent = value;
          tr.appendChild(td);
        });
        dom.workload.appendChild(tr);
      });

      dom.transferActivity.textContent = "";
      overview.recent_transfers.forEach((entry) => {
        const item = document.createElement("li");
        item.textContent = describeAssignment({
          action: entry.action,
          from_agent: entry.from,
          to_agent: entry.to,
        });
        const meta = document.createElement("span");
        meta.className = "panel__meta";
        meta.textContent = `${entry.by || "sistema"} · ${formatTime(entry.at)}`;
        item.appendChild(meta);
        dom.transferActivity.appendChild(item);
      });
      dom.supervisorPanel.hidden = false;
    } catch (error) {
      setStatus(error.message);
    }
  });

  dom.supervisorClose.addEventListener("click", () => {
    dom.supervisorPanel.hidden = true;
  });

  function showAdminError(message) {
    dom.adminError.textContent = message;
    dom.adminError.hidden = !message;
  }

  dom.adminButton.addEventListener("click", async () => {
    showAdminError("");
    // Los departamentos deben estar ya en el <select> antes de que loadAgents()
    // marque los adicionales de la persona elegida.
    await loadDepartments();
    await loadAgents();
    await loadAutoReply();
    dom.adminPanel.hidden = false;
  });

  dom.adminClose.addEventListener("click", () => {
    dom.adminPanel.hidden = true;
  });

  async function loadAutoReply() {
    try {
      const data = await api("/api/admin/settings");
      dom.autoReplyText.value = data.fallback_message;
    } catch (error) {
      showAdminError(error.message);
    }
  }

  dom.autoReplyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");
    try {
      await api("/api/admin/settings", {
        method: "PUT",
        body: JSON.stringify({ fallback_message: dom.autoReplyText.value.trim() }),
      });
      setStatus("Respuesta automática actualizada.");
    } catch (error) {
      showAdminError(error.message);
    }
  });

  dom.createUserForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");
    try {
      await api("/api/agents", {
        method: "POST",
        body: JSON.stringify({
          email: dom.newUserEmail.value.trim(),
          display_name: dom.newUserName.value.trim() || null,
          role: dom.newUserRole.value,
          department_id: dom.newUserDepartment.value || null,
          password: dom.newUserPassword.value,
        }),
      });
      dom.createUserForm.reset();
      await loadAgents();
      setStatus("Usuario creado.");
    } catch (error) {
      showAdminError(error.message);
    }
  });

  dom.resetPasswordForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");
    try {
      await api(`/api/agents/${dom.passwordUser.value}/password`, {
        method: "POST",
        body: JSON.stringify({ password: dom.resetPassword.value }),
      });
      dom.resetPasswordForm.reset();
      setStatus("Contraseña actualizada.");
    } catch (error) {
      showAdminError(error.message);
    }
  });

  dom.asideToggle.addEventListener("click", () => {
    dom.aside.hidden = !dom.aside.hidden;
  });

  /* ------------------------------------------------------------ tiempo real */

  function connectInbox() {
    // Cierra cualquier conexión anterior antes de abrir otra: sin esto, dos
    // sockets vivos a la vez duplican cada aviso que llega por la cola.
    if (state.socket) {
      const stale = state.socket;
      state.socket = null;
      stale.close();
    }
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${window.location.host}/ws/inbox`);
    state.socket = socket;

    socket.addEventListener("message", async (event) => {
      if (state.socket !== socket) return;
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      if (payload.type === "ready") {
        return;
      }

      const esMio = payload.assignee_id === state.me?.agent?.id;
      if (payload.type === "assignment" && esMio) {
        setStatus(`${payload.by} le derivó una conversación.`);
      }

      // Si el hilo abierto acaba de pasar a otra persona, se cierra en el acto:
      // pedir su contenido ahora solo obtendría un 404.
      const afectaAlAbierto =
        state.selected && payload.conversation_id === state.selected.id;
      if (
        afectaAlAbierto &&
        payload.type === "assignment" &&
        !stillVisibleToMe(payload.assignee_id)
      ) {
        clearSelection(
          `${payload.by} pasó la conversación a ${payload.assignee_name || "otro compañero"}.`,
        );
        await loadConversations();
        return;
      }

      await loadConversations();
      if (afectaAlAbierto && state.selected) {
        await Promise.all([loadMessages(), loadAssignments(), loadNotes(), loadContact()]);
      }
    });

    // Latido: mantiene la conexión viva y detecta cortes intermedios.
    const heartbeat = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send("ping");
      }
    }, 25000);

    socket.addEventListener("close", () => {
      clearInterval(heartbeat);
      if (state.socket !== socket) return;
      if (state.me) {
        setTimeout(connectInbox, 3000);
      }
    });
  }

  /* ---------------------------------------------------------------- arranque */

  async function start() {
    if (!(await loadIdentity())) {
      return;
    }
    await loadDepartments();
    await loadAgents();
    await loadConversations();
    // Una sola consola para todos: a quien entra como administrador se le
    // abre el panel de administración de una vez, sin URL aparte que elegir.
    if (state.me.role === "admin") {
      dom.adminPanel.hidden = false;
    }
    connectInbox();
  }

  start();
})();
