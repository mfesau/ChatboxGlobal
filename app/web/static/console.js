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
    app: document.getElementById("app"),
    logout: document.getElementById("logout-button"),
    langSelect: document.getElementById("lang-select"),
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
    labelFilter: document.getElementById("label-filter"),
    savedViews: document.getElementById("saved-views"),
    saveViewButton: document.getElementById("save-view-button"),
    saveViewForm: document.getElementById("save-view-form"),
    saveViewName: document.getElementById("save-view-name"),
    saveViewShared: document.getElementById("save-view-shared"),
    saveViewSharedField: document.getElementById("save-view-shared-field"),
    list: document.getElementById("conversation-list"),
    thread: document.getElementById("console-thread"),
    title: document.getElementById("thread-title"),
    subtitle: document.getElementById("thread-subtitle"),
    threadActions: document.getElementById("thread-actions"),
    claim: document.getElementById("claim-button"),
    release: document.getElementById("release-button"),
    stateButtons: [...document.querySelectorAll(".state-switch button")],
    aside: document.getElementById("aside"),
    asideToggle: document.getElementById("aside-toggle"),
    transferTarget: document.getElementById("transfer-target"),
    transferDepartment: document.getElementById("transfer-department"),
    transferNote: document.getElementById("transfer-note"),
    transfer: document.getElementById("transfer-button"),
    conversationLabelToggles: document.getElementById("conversation-label-toggles"),
    notesList: document.getElementById("notes-list"),
    noteForm: document.getElementById("note-form"),
    noteBody: document.getElementById("note-body"),
    assignments: document.getElementById("assignment-list"),
    form: document.getElementById("console-form"),
    input: document.getElementById("console-input"),
    send: document.getElementById("console-send"),
    attachButton: document.getElementById("console-attach-button"),
    attachInput: document.getElementById("console-attach-input"),
    cannedSuggestions: document.getElementById("canned-suggestions"),
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
    startButton: document.getElementById("start-button"),
    startPanel: document.getElementById("start-panel"),
    startClose: document.getElementById("start-close"),
    startForm: document.getElementById("start-form"),
    startTo: document.getElementById("start-to"),
    startTemplate: document.getElementById("start-template"),
    startPreview: document.getElementById("start-preview"),
    startVariables: document.getElementById("start-variables"),
    startSend: document.getElementById("start-send"),
    startError: document.getElementById("start-error"),
    brandStyle: document.getElementById("brand-style"),
    brandColor: document.getElementById("brand-color"),
    brandHex: document.getElementById("brand-hex"),
    brandPresets: document.getElementById("brand-presets"),
    brandPreview: document.getElementById("brand-preview"),
    brandSave: document.getElementById("brand-save"),
    brandReset: document.getElementById("brand-reset"),
    createUserForm: document.getElementById("create-user-form"),
    newUserEmail: document.getElementById("new-user-email"),
    newUserName: document.getElementById("new-user-name"),
    newUserRole: document.getElementById("new-user-role"),
    newUserDepartment: document.getElementById("new-user-department"),
    newUserPasswordField: document.getElementById("new-user-password-field"),
    newUserPassword: document.getElementById("new-user-password"),
    newUserPasswordHint: document.getElementById("new-user-password-hint"),
    adminError: document.getElementById("admin-error"),
    adminUsers: document.querySelector("#admin-users-table tbody"),
    createDepartmentForm: document.getElementById("create-department-form"),
    newDepartmentName: document.getElementById("new-department-name"),
    departmentList: document.getElementById("department-list"),
    hoursDepartment: document.getElementById("hours-department"),
    hoursEditor: document.getElementById("hours-editor"),
    hoursTimezone: document.getElementById("hours-timezone"),
    hoursMessage: document.getElementById("hours-message"),
    hoursDays: document.getElementById("hours-days"),
    hoursSlaTarget: document.getElementById("hours-sla-target"),
    hoursDefaultsHint: document.getElementById("hours-defaults-hint"),
    hoursSave: document.getElementById("hours-save"),
    macroPanel: document.getElementById("macro-panel"),
    macroButtons: document.getElementById("macro-buttons"),
    createMacroForm: document.getElementById("create-macro-form"),
    newMacroName: document.getElementById("new-macro-name"),
    macroStepAction: document.getElementById("macro-step-action"),
    macroStepTarget: document.getElementById("macro-step-target"),
    macroStepTargetField: document.getElementById("macro-step-target-field"),
    macroStepTargetLabel: document.getElementById("macro-step-target-label"),
    macroStepBody: document.getElementById("macro-step-body"),
    macroStepBodyField: document.getElementById("macro-step-body-field"),
    macroStepAdd: document.getElementById("macro-step-add"),
    macroSteps: document.getElementById("macro-steps"),
    macroList: document.getElementById("macro-list"),
    createLabelForm: document.getElementById("create-label-form"),
    newLabelName: document.getElementById("new-label-name"),
    newLabelColor: document.getElementById("new-label-color"),
    labelList: document.getElementById("label-list"),
    createCannedResponseForm: document.getElementById("create-canned-response-form"),
    newCannedShortcode: document.getElementById("new-canned-shortcode"),
    newCannedTitle: document.getElementById("new-canned-title"),
    newCannedBody: document.getElementById("new-canned-body"),
    cannedResponseList: document.getElementById("canned-response-list"),
    createChannelAccountForm: document.getElementById("create-channel-account-form"),
    newAccountChannel: document.getElementById("new-account-channel"),
    newAccountExternalId: document.getElementById("new-account-external-id"),
    newAccountName: document.getElementById("new-account-name"),
    newAccountDepartment: document.getElementById("new-account-department"),
    newAccountToken: document.getElementById("new-account-token"),
    newAccountVerifyToken: document.getElementById("new-account-verify-token"),
    newAccountAppSecret: document.getElementById("new-account-app-secret"),
    channelAccountsTable: document.querySelector("#channel-accounts-table tbody"),
    contactsButton: document.getElementById("contacts-button"),
    contactsPanel: document.getElementById("contacts-panel"),
    contactsClose: document.getElementById("contacts-close"),
    contactsSearch: document.getElementById("contacts-search"),
    contactsTable: document.querySelector("#contacts-table tbody"),
    hotelAdminDepartment: document.getElementById("hotel-admin-department"),
    hotelAdminModuleField: document.getElementById("hotel-admin-module-field"),
    hotelAdminModuleEnabled: document.getElementById("hotel-admin-module-enabled"),
    hotelAdminSetup: document.getElementById("hotel-admin-setup"),
    createRoomTypeForm: document.getElementById("create-room-type-form"),
    newRoomTypeName: document.getElementById("new-room-type-name"),
    newRoomTypeCapacity: document.getElementById("new-room-type-capacity"),
    newRoomTypeDescription: document.getElementById("new-room-type-description"),
    roomTypesTable: document.querySelector("#room-types-table tbody"),
    createRoomForm: document.getElementById("create-room-form"),
    newRoomTypeSelect: document.getElementById("new-room-type-select"),
    newRoomCode: document.getElementById("new-room-code"),
    roomsTable: document.querySelector("#rooms-table tbody"),
    createRatePlanForm: document.getElementById("create-rate-plan-form"),
    newRatePlanTypeSelect: document.getElementById("new-rate-plan-type-select"),
    newRatePlanName: document.getElementById("new-rate-plan-name"),
    newRatePlanStarts: document.getElementById("new-rate-plan-starts"),
    newRatePlanEnds: document.getElementById("new-rate-plan-ends"),
    newRatePlanPrice: document.getElementById("new-rate-plan-price"),
    newRatePlanCurrency: document.getElementById("new-rate-plan-currency"),
    ratePlansTable: document.querySelector("#rate-plans-table tbody"),
    ratePlanFormTitle: document.getElementById("rate-plan-form-title"),
    ratePlanSubmit: document.getElementById("rate-plan-submit"),
    ratePlanCancelEdit: document.getElementById("rate-plan-cancel-edit"),
    hotelButton: document.getElementById("hotel-button"),
    hotelPanel: document.getElementById("hotel-panel"),
    hotelClose: document.getElementById("hotel-close"),
    hotelDepartment: document.getElementById("hotel-department"),
    hotelError: document.getElementById("hotel-error"),
    hotelBody: document.getElementById("hotel-body"),
    hotelReportArrivals: document.getElementById("hotel-report-arrivals"),
    hotelReportDepartures: document.getElementById("hotel-report-departures"),
    hotelReportOccupancy: document.getElementById("hotel-report-occupancy"),
    hotelReportPending: document.getElementById("hotel-report-pending"),
    hotelReportRevenue: document.getElementById("hotel-report-revenue"),
    hotelAvailabilityForm: document.getElementById("hotel-availability-form"),
    hotelCheckIn: document.getElementById("hotel-check-in"),
    hotelCheckOut: document.getElementById("hotel-check-out"),
    hotelAvailabilityList: document.getElementById("hotel-availability-list"),
    hotelReservationForm: document.getElementById("hotel-reservation-form"),
    hotelReservationFormTitle: document.getElementById("hotel-reservation-form-title"),
    hotelReservationSubmit: document.getElementById("hotel-reservation-submit"),
    hotelReservationCancelEdit: document.getElementById("hotel-reservation-cancel-edit"),
    hotelContactSearch: document.getElementById("hotel-contact-search"),
    hotelContactResults: document.getElementById("hotel-contact-results"),
    hotelContactLinked: document.getElementById("hotel-contact-linked"),
    hotelReservationRoom: document.getElementById("hotel-reservation-room"),
    hotelGuestName: document.getElementById("hotel-guest-name"),
    hotelGuestPhone: document.getElementById("hotel-guest-phone"),
    hotelGuestEmail: document.getElementById("hotel-guest-email"),
    hotelGuestCount: document.getElementById("hotel-guest-count"),
    hotelReservationNotes: document.getElementById("hotel-reservation-notes"),
    hotelStatusFilter: document.getElementById("hotel-status-filter"),
    hotelReservationsTable: document.querySelector("#hotel-reservations-table tbody"),
    contactProfile: document.getElementById("contact-profile"),
    profileTitle: document.getElementById("contact-profile-title"),
    profileName: document.getElementById("profile-name"),
    profilePhone: document.getElementById("profile-phone"),
    profileEmail: document.getElementById("profile-email"),
    profileSave: document.getElementById("profile-save"),
    profileError: document.getElementById("profile-error"),
    profileComments: document.getElementById("profile-comments"),
    profileCommentForm: document.getElementById("profile-comment-form"),
    profileCommentBody: document.getElementById("profile-comment-body"),
    profileConversations: document.getElementById("profile-conversations"),
  };

  const state = {
    me: null,
    scope: "unassigned",
    conversations: [],
    selected: null,
    agents: [],
    departments: [],
    labels: [],
    macros: [],
    macroDraft: [],
    savedViews: [],
    cannedResponses: [],
    cannedMatches: [],
    cannedHighlight: -1,
    socket: null,
    pendingAttachment: null,
    contact: null,
    contactProfileId: null,
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

  function extractDetail(body, status, fallback) {
    try {
      return JSON.parse(body).detail || fallback || `Error ${status}`;
    } catch {
      return fallback || `Error ${status}`;
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
    // "/" es la entrada única de la aplicación: sin sesión de agente válida,
    // la consola no tiene login propio, redirige allá.
    if (state.socket) {
      state.socket.close();
      state.socket = null;
    }
    window.location.href = "/";
  }

  function showApp() {
    dom.app.hidden = false;
  }

  async function loadIdentity() {
    try {
      const me = await api("/api/auth/me");
      state.me = me;
      const label = me.agent ? me.agent.display_name || me.agent.email : "Acceso de servicio";
      dom.who.textContent = label;
      dom.whoRole.textContent = i18n.t(me.is_supervisor ? "role.supervisor" : "role.agent");
      dom.tabAll.hidden = !me.is_supervisor;
      dom.supervisorButton.hidden = !me.is_supervisor;
      dom.contactsButton.hidden = !me.is_supervisor;
      dom.adminButton.hidden = me.role !== "admin";
      // Escribir primero es trabajo de agente, no de administracion: lo ve
      // todo el mundo. Si la instalacion no tiene WhatsApp, el boton se
      // retira al primer intento (ver openStartPanel).
      dom.startButton.hidden = false;
      // Igual que "Nueva conversación": operar el hotel es trabajo de
      // cualquier agente con acceso al departamento, no solo de
      // administración. El servidor decide el acceso al elegir el
      // departamento (ver dom.hotelDepartment).
      dom.hotelButton.hidden = false;
      showApp();
      return true;
    } catch {
      showGate();
      return false;
    }
  }

  dom.logout.addEventListener("click", async () => {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch {
      // Aunque falle el cierre en el servidor, igual se vuelve al acceso.
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
    // El estado viaja siempre, incluso vacío: omitirlo hacía que el servidor
    // aplicara su valor por omisión —solo las abiertas— y «Todas» terminaba
    // mostrando lo mismo que «Pendientes», ocultando las ya resueltas.
    params.set("status", dom.statusFilter.value);
    if (dom.channelFilter.value) {
      params.set("channel", dom.channelFilter.value);
    }
    if (dom.departmentFilter.value) {
      params.set("department", dom.departmentFilter.value);
    }
    if (dom.labelFilter.value) {
      params.set("label", dom.labelFilter.value);
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
          ? i18n.t("thread.listEmptyQueue")
          : i18n.t("thread.listEmptyFilters");
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
      name.textContent = conversation.contact_name || i18n.t("thread.noContact");
      top.appendChild(name);

      if (!conversation.assignee_id) {
        top.appendChild(badge(i18n.t("badge.queued"), "console__badge--pool"));
      } else if (conversation.assignee_id === state.me?.agent?.id) {
        top.appendChild(badge(i18n.t("badge.mine"), "console__badge--human"));
      }
      if (conversation.unread_count > 0) {
        top.appendChild(badge(String(conversation.unread_count), "console__badge--unread"));
      }
      // Solo se avisa de lo que exige atención: una conversación respondida a
      // tiempo no necesita distintivo que compita con el resto.
      if (conversation.sla_status === "breached") {
        const vencida = badge(i18n.t("badge.breached"), "console__badge--breached");
        vencida.title = i18n.t("badge.breachedTitle");
        top.appendChild(vencida);
      } else if (conversation.sla_status === "pending" && conversation.sla_due_at) {
        const espera = badge(slaCountdown(conversation.sla_due_at), "console__badge--sla");
        espera.title = i18n.t("badge.slaTitle", { time: formatTime(conversation.sla_due_at) });
        top.appendChild(espera);
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

      if (conversation.labels?.length > 0) {
        const labelRow = document.createElement("div");
        labelRow.className = "console__item-labels";
        conversation.labels.forEach((label) => {
          const chip = badge(label.name);
          chip.style.backgroundColor = label.color;
          chip.style.color = "#fff";
          labelRow.appendChild(chip);
        });
        button.appendChild(labelRow);
      }

      button.addEventListener("click", () => selectConversation(conversation));
      item.appendChild(button);
      dom.list.appendChild(item);
    });
  }

  /** Cuánto queda hasta el objetivo, en la unidad que se entienda de un vistazo. */
  function slaCountdown(dueAt) {
    const minutes = Math.round((new Date(dueAt) - Date.now()) / 60000);
    if (minutes <= 0) {
      return i18n.t("sla.dueNow");
    }
    if (minutes < 60) {
      return i18n.t("sla.minutes", { n: minutes });
    }
    const hours = Math.round(minutes / 60);
    return hours < 24
      ? i18n.t("sla.hours", { n: hours })
      : i18n.t("sla.days", { n: Math.round(hours / 24) });
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
    dom.title.textContent = conversation.contact_name || i18n.t("thread.noContact");
    dom.threadActions.hidden = false;
    // En pantalla ancha el panel es una columna y se muestra siempre. En
    // pantalla estrecha es un cajón que taparía el hilo, de modo que se abre
    // solo cuando el agente lo pide con el botón «Equipo».
    dom.aside.hidden = !isWideLayout();
    dom.input.disabled = false;
    dom.attachButton.disabled = false;
    hideCannedSuggestions();
    renderOwnership();
    renderConversationState();
    renderConversations();
    renderConversationLabelToggles();
    await Promise.all([loadMessages(), loadNotes(), loadAssignments(), loadContact()]);
  }

  function renderConversationLabelToggles() {
    // Si el panel no está en la vista —se quitó una vez y podría volver a
    // quitarse—, no se hace nada en lugar de fallar.
    if (!dom.conversationLabelToggles) {
      return;
    }
    const conversation = state.selected;
    dom.conversationLabelToggles.textContent = "";
    if (!conversation) {
      return;
    }
    if (state.labels.length === 0) {
      const empty = document.createElement("p");
      empty.className = "console__muted";
      empty.textContent = i18n.t("aside.labelsEmpty");
      dom.conversationLabelToggles.appendChild(empty);
      return;
    }
    const activeIds = new Set((conversation.labels || []).map((label) => label.id));
    state.labels.forEach((label) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = `label-toggle${activeIds.has(label.id) ? " is-active" : ""}`;
      chip.style.backgroundColor = label.color;
      chip.textContent = label.name;
      chip.addEventListener("click", () => toggleConversationLabel(label.id));
      dom.conversationLabelToggles.appendChild(chip);
    });
  }

  async function toggleConversationLabel(labelId) {
    const conversation = state.selected;
    if (!conversation) {
      return;
    }
    const current = new Set((conversation.labels || []).map((label) => label.id));
    if (current.has(labelId)) {
      current.delete(labelId);
    } else {
      current.add(labelId);
    }
    try {
      const updated = await api(`/api/conversations/${conversation.id}/labels`, {
        method: "PUT",
        body: JSON.stringify({ label_ids: [...current] }),
      });
      conversation.labels = updated;
      renderConversationLabelToggles();
      renderConversations();
    } catch (error) {
      setStatus(error.message);
    }
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
          const nodo = renderAttachment(attachment);
          if (nodo) {
            bubble.appendChild(nodo);
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
      setStatus(i18n.t("thread.messageCount", { n: messages.length }));
      updateSend();
    } catch (error) {
      handleThreadError(error, i18n.t("thread.loadFailed", { error: error.message }));
    }
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
        empty.textContent = i18n.t("aside.notesEmpty");
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
      case "transfer_department":
        return `Derivada a la cola del departamento ${entry.to_department || "sin nombre"}`;
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

  /* ------------------------------------------- estado de la conversación */

  /* Marcar el estado ya no vacía el panel: al pasar a «en proceso» quien la
     atiende sigue en ella, y al resolverla conviene ver el hilo un momento
     más. Es la lista la que se recarga para reflejar el cambio. */
  dom.stateButtons.forEach((button) =>
    button.addEventListener("click", async () => {
      if (!state.selected) {
        return;
      }
      const target = button.dataset.state;
      dom.stateButtons.forEach((other) => {
        other.disabled = true;
      });
      try {
        await api(`/api/conversations/${state.selected.id}/state`, {
          method: "POST",
          body: JSON.stringify({ state: target }),
        });
        state.selected.work_state = target;
        renderConversationState();
        setStatus(i18n.t(`state.set.${target}`));
        await loadConversations();
      } catch (error) {
        setStatus(error.message);
      } finally {
        dom.stateButtons.forEach((other) => {
          other.disabled = false;
        });
      }
    }),
  );

  function renderConversationState() {
    const current = state.selected?.work_state || "pending";
    dom.stateButtons.forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.state === current));
    });
  }

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
      setStatus(i18n.t("aside.noteSaved"));
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
      hideCannedSuggestions();
      clearAttachment();
      setStatus(i18n.t("composer.queued"));
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
      dom.attachButton.title = i18n.t("composer.attachReady", {
        name: attachment.filename || "",
      });
      updateSend();
    } catch (error) {
      clearAttachment();
      dom.attachButton.title = i18n.t("composer.attachFailed", { error: error.message });
    } finally {
      dom.attachButton.disabled = false;
    }
  });

  dom.input.addEventListener("input", () => {
    dom.input.style.height = "auto";
    dom.input.style.height = `${dom.input.scrollHeight}px`;
    updateSend();
    updateCannedSuggestions();
  });

  dom.input.addEventListener("keydown", (event) => {
    if (state.cannedMatches.length > 0) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const delta = event.key === "ArrowDown" ? 1 : -1;
        state.cannedHighlight =
          (state.cannedHighlight + delta + state.cannedMatches.length) %
          state.cannedMatches.length;
        renderCannedSuggestions();
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        applyCannedResponse(state.cannedMatches[Math.max(state.cannedHighlight, 0)]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        hideCannedSuggestions();
        return;
      }
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      dom.form.requestSubmit();
    }
  });

  dom.input.addEventListener("blur", hideCannedSuggestions);

  /* ----------------------------------------------- respuestas guardadas: «/» */

  function updateCannedSuggestions() {
    const cursor = dom.input.selectionStart ?? dom.input.value.length;
    const before = dom.input.value.slice(0, cursor);
    const match = before.match(/(?:^|\s)\/(\S*)$/);
    if (!match) {
      hideCannedSuggestions();
      return;
    }
    const query = match[1].toLowerCase();
    state.cannedMatches = state.cannedResponses.filter(
      (canned) =>
        canned.shortcode.toLowerCase().includes(query)
        || canned.title.toLowerCase().includes(query),
    );
    state.cannedHighlight = state.cannedMatches.length > 0 ? 0 : -1;
    renderCannedSuggestions();
  }

  function renderCannedSuggestions() {
    dom.cannedSuggestions.textContent = "";
    if (state.cannedMatches.length === 0) {
      dom.cannedSuggestions.hidden = true;
      return;
    }
    state.cannedMatches.forEach((canned, index) => {
      const item = document.createElement("li");
      item.className = index === state.cannedHighlight ? "is-active" : "";
      const shortcode = document.createElement("strong");
      shortcode.textContent = `/${canned.shortcode}`;
      item.appendChild(shortcode);
      item.appendChild(document.createTextNode(` — ${canned.title}`));
      // `mousedown` (con `preventDefault`) para elegir antes de que el
      // `blur` del textarea, que llegaría con un `click` normal, cierre
      // la lista sin dar tiempo a aplicar la respuesta.
      item.addEventListener("mousedown", (event) => {
        event.preventDefault();
        applyCannedResponse(canned);
      });
      dom.cannedSuggestions.appendChild(item);
    });
    dom.cannedSuggestions.hidden = false;
  }

  function hideCannedSuggestions() {
    state.cannedMatches = [];
    state.cannedHighlight = -1;
    dom.cannedSuggestions.hidden = true;
    dom.cannedSuggestions.textContent = "";
  }

  function applyCannedResponse(canned) {
    const cursor = dom.input.selectionStart ?? dom.input.value.length;
    const before = dom.input.value.slice(0, cursor);
    const after = dom.input.value.slice(cursor);
    const start = before.search(/\/\S*$/);
    dom.input.value = `${before.slice(0, start)}${canned.body}${after}`;
    const caret = start + canned.body.length;
    dom.input.focus();
    dom.input.setSelectionRange(caret, caret);
    dom.input.style.height = "auto";
    dom.input.style.height = `${dom.input.scrollHeight}px`;
    hideCannedSuggestions();
    updateSend();
  }

  /* -------------------------------------------------------------- pestañas */

  dom.tabs.forEach((tab) =>
    tab.addEventListener("click", async () => {
      state.scope = tab.dataset.scope;
      dom.tabs.forEach((other) =>
        other.setAttribute("aria-selected", String(other === tab)),
      );
      renderSavedViews();
      await loadConversations();
    }),
  );

  /* Lo elegido se repite en el rótulo emergente: el desplegable es estrecho y
     un nombre largo de departamento o etiqueta se corta con puntos. */
  function reflectFilterTitle(select) {
    select.title = select.options[select.selectedIndex]?.textContent || "";
  }

  const FILTERS = [
    dom.statusFilter,
    dom.channelFilter,
    dom.departmentFilter,
    dom.labelFilter,
  ];

  FILTERS.forEach((element) => {
    reflectFilterTitle(element);
    element.addEventListener("change", async () => {
      reflectFilterTitle(element);
      renderSavedViews();
      await loadConversations();
    });
  });

  document.addEventListener("languagechange", () => FILTERS.forEach(reflectFilterTitle));

  /* -------------------------------------------------- vistas guardadas */

  /* Viven en la base y no en el navegador: así la misma persona las encuentra
     desde cualquier equipo, y supervisión puede dejar vistas para todos. */
  function viewsKey() {
    return `chatbox.views.${state.me?.agent?.id || "anon"}`;
  }

  async function loadSavedViews() {
    try {
      state.savedViews = await api("/api/saved-views");
    } catch {
      state.savedViews = [];
    }
    await migrateBrowserViews();
    renderSavedViews();
  }

  /** Sube las vistas que quedaron en el navegador de la versión anterior. */
  async function migrateBrowserViews() {
    let stored = [];
    try {
      const raw = localStorage.getItem(viewsKey());
      stored = raw ? JSON.parse(raw) : [];
    } catch {
      return;
    }
    if (stored.length === 0) {
      return;
    }
    for (const view of stored) {
      try {
        await api("/api/saved-views", {
          method: "POST",
          body: JSON.stringify({ name: view.name, filters: cleanFilters(view.filters) }),
        });
      } catch {
        // Repetida o ya inválida: se descarta sin cortar el resto.
      }
    }
    try {
      localStorage.removeItem(viewsKey());
    } catch {
      // Nada que hacer: se subieron igual y no se volverán a duplicar.
    }
    try {
      state.savedViews = await api("/api/saved-views");
    } catch {
      // Se queda con lo que ya tenía; el próximo ingreso lo reintenta.
    }
  }

  /** El servidor rechaza un canal o un identificador vacíos: se quitan. */
  function cleanFilters(filters) {
    const clean = {};
    Object.entries(filters || {}).forEach(([key, value]) => {
      if (value) {
        clean[key] = value;
      }
    });
    return clean;
  }

  function currentFilters() {
    return {
      scope: state.scope,
      status: dom.statusFilter.value,
      channel: dom.channelFilter.value,
      department: dom.departmentFilter.value,
      label: dom.labelFilter.value,
    };
  }

  function sameFilters(a, b) {
    return ["scope", "status", "channel", "department", "label"].every(
      (key) => (a[key] || "") === (b[key] || ""),
    );
  }

  function renderSavedViews() {
    dom.savedViews.textContent = "";
    const active = currentFilters();
    state.savedViews.forEach((view) => {
      const chip = document.createElement("span");
      chip.className = "console__view";
      if (sameFilters(view.filters, active)) {
        chip.classList.add("is-active");
      }
      if (view.shared) {
        chip.classList.add("console__view--shared");
      }

      const apply = document.createElement("button");
      apply.type = "button";
      apply.className = "console__view-apply";
      apply.textContent = view.name;
      apply.title = view.shared
        ? i18n.t("views.applyShared", { name: view.name })
        : i18n.t("views.apply", { name: view.name });
      apply.addEventListener("click", () => applySavedView(view));
      chip.appendChild(apply);

      // Una vista del equipo solo la retira supervisión: para el resto no
      // se muestra un botón que el servidor va a rechazar igual.
      if (!view.shared || state.me?.is_supervisor) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "console__view-remove";
        remove.textContent = "×";
        remove.title = i18n.t("views.remove", { name: view.name });
        remove.setAttribute("aria-label", `Quitar la vista ${view.name}`);
        remove.addEventListener("click", () => removeSavedView(view));
        chip.appendChild(remove);
      }

      dom.savedViews.appendChild(chip);
    });
  }

  async function removeSavedView(view) {
    try {
      await api(`/api/saved-views/${view.id}`, { method: "DELETE" });
      state.savedViews = state.savedViews.filter((row) => row.id !== view.id);
      renderSavedViews();
    } catch (error) {
      setStatus(error.message);
    }
  }

  async function applySavedView(view) {
    const { scope, status, channel, department, label } = view.filters;
    state.scope = scope || "unassigned";
    dom.tabs.forEach((tab) =>
      tab.setAttribute("aria-selected", String(tab.dataset.scope === state.scope)),
    );
    // Si el departamento o la etiqueta se borraron desde que se guardó la
    // vista, el <select> no tiene esa opción y queda en "todos": se degrada
    // a una vista más amplia en vez de romperse.
    dom.statusFilter.value = status || "";
    dom.channelFilter.value = channel || "";
    dom.departmentFilter.value = department || "";
    dom.labelFilter.value = label || "";
    renderSavedViews();
    await loadConversations();
  }

  /* El nombre se pide con un campo en la misma barra, no con un diálogo del
     navegador: el resto de la consola tampoco usa ventanas emergentes. */
  function openSaveView() {
    dom.saveViewButton.hidden = true;
    dom.saveViewForm.hidden = false;
    dom.saveViewName.value = "";
    // Compartir con el equipo solo se ofrece a quien puede hacerlo.
    dom.saveViewSharedField.hidden = !state.me?.is_supervisor;
    dom.saveViewShared.checked = false;
    // Sin nombre escrito se usa el del marcador: guardar es un solo Enter.
    dom.saveViewName.placeholder = i18n.t("views.default", {
      n: state.savedViews.length + 1,
    });
    // En el cuadro siguiente, no dentro del propio clic: al ocultar el botón
    // que lo recibió, el navegador devuelve el foco al documento al terminar
    // de procesarlo y se llevaría por delante el que acabamos de poner.
    requestAnimationFrame(() => dom.saveViewName.focus());
  }

  function closeSaveView() {
    dom.saveViewForm.hidden = true;
    dom.saveViewButton.hidden = false;
  }

  dom.saveViewButton.addEventListener("click", openSaveView);

  dom.saveViewForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = (dom.saveViewName.value.trim() || dom.saveViewName.placeholder).slice(0, 40);
    const shared = dom.saveViewShared.checked;
    closeSaveView();
    try {
      const view = await api("/api/saved-views", {
        method: "POST",
        body: JSON.stringify({ name, filters: cleanFilters(currentFilters()), shared }),
      });
      state.savedViews.push(view);
      renderSavedViews();
      setStatus(i18n.t("views.saved", { name }));
    } catch (error) {
      setStatus(error.message);
    }
  });

  dom.saveViewName.addEventListener("keydown", (event) => {
    // El envío se pide a mano: un formulario sin botón de envío no siempre
    // reacciona al Enter, y este solo tiene el campo del nombre.
    if (event.key === "Enter") {
      event.preventDefault();
      dom.saveViewForm.requestSubmit();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      closeSaveView();
    }
  });

  // Salir del campo solo lo cierra si está vacío. Con texto a medio escribir
  // se deja abierto: perderlo por un clic fuera sería peor que dejar el campo
  // a la vista, y para descartarlo ya está Escape.
  dom.saveViewName.addEventListener("blur", () => {
    if (!dom.saveViewName.value.trim()) {
      closeSaveView();
    }
  });

  /* ----------------------------------------------------------- compañeros */

  function buildDepartmentSelect(agent, { multiple = false } = {}) {
    const select = document.createElement("select");
    if (multiple) {
      select.multiple = true;
      select.size = Math.min(4, Math.max(2, state.departments.length || 2));
    } else {
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "Sin departamento";
      select.appendChild(empty);
    }
    state.departments.forEach((department) => {
      const option = document.createElement("option");
      option.value = department.id;
      option.textContent = department.name;
      if (multiple) {
        option.selected = agent.extra_department_ids.includes(department.id);
      } else if (department.id === agent.department_id) {
        option.selected = true;
      }
      select.appendChild(option);
    });
    return select;
  }

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

    const primaryCell = document.createElement("td");
    const primarySelect = buildDepartmentSelect(agent);
    primarySelect.setAttribute("aria-label", `Departamento principal de ${agent.email}`);
    primaryCell.appendChild(primarySelect);
    row.appendChild(primaryCell);

    const extraCell = document.createElement("td");
    const extraSelect = buildDepartmentSelect(agent, { multiple: true });
    extraSelect.setAttribute("aria-label", `Departamentos adicionales de ${agent.email}`);
    extraCell.appendChild(extraSelect);
    row.appendChild(extraCell);

    const passwordCell = document.createElement("td");
    const passwordInput = document.createElement("input");
    passwordInput.type = "password";
    passwordInput.minLength = 8;
    passwordInput.autocomplete = "new-password";
    passwordInput.placeholder = "Sin cambios";
    passwordInput.setAttribute("aria-label", `Nueva contraseña de ${agent.email}`);
    passwordCell.appendChild(passwordInput);
    row.appendChild(passwordCell);

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
      const password = passwordInput.value;
      if (password && password.length < 8) {
        showAdminError("La nueva contraseña debe tener al menos 8 caracteres.");
        return;
      }
      showAdminError("");
      saveButton.disabled = true;
      try {
        await api(`/api/agents/${agent.id}`, {
          method: "PATCH",
          body: JSON.stringify({ display_name: displayName }),
        });
        await api(`/api/agents/${agent.id}/departments`, {
          method: "PUT",
          body: JSON.stringify({
            department_id: primarySelect.value || null,
            extra_department_ids: Array.from(extraSelect.selectedOptions).map(
              (option) => option.value,
            ),
          }),
        });
        if (password) {
          await api(`/api/agents/${agent.id}/password`, {
            method: "POST",
            body: JSON.stringify({ password }),
          });
        }
        await loadAgents();
        setStatus("Usuario actualizado.");
      } catch (error) {
        showAdminError(error.message);
      } finally {
        saveButton.disabled = false;
      }
    });
    actionsCell.appendChild(saveButton);

    const toggleButton = document.createElement("button");
    toggleButton.type = "button";
    toggleButton.className = "ghost-button";
    toggleButton.textContent = i18n.t(agent.is_active ? "admin.deactivate" : "admin.reactivate");
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

    /* Borrar de verdad, frente a desactivar. Se pide confirmación en el propio
       botón —primer clic pregunta, segundo borra— en vez de abrir un diálogo
       del navegador: el aviso queda donde está la fila que se va a borrar, y
       no hay forma de confundirse de persona. La pregunta se retira sola a los
       cinco segundos, para que un botón no se quede armado esperando. */
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "ghost-button ghost-button--danger";
    let armed = false;
    let armedTimer = null;

    const disarm = () => {
      armed = false;
      deleteButton.textContent = i18n.t("admin.delete");
      deleteButton.classList.remove("is-armed");
    };
    disarm();

    deleteButton.addEventListener("click", async () => {
      showAdminError("");
      if (!armed) {
        armed = true;
        deleteButton.textContent = i18n.t("admin.deleteConfirm");
        deleteButton.classList.add("is-armed");
        clearTimeout(armedTimer);
        armedTimer = setTimeout(disarm, 5000);
        return;
      }
      clearTimeout(armedTimer);
      deleteButton.disabled = true;
      try {
        await api(`/api/agents/${agent.id}/permanently`, { method: "DELETE" });
        setStatus(i18n.t("admin.deleted"));
        await loadAgents();
      } catch (error) {
        deleteButton.disabled = false;
        disarm();
        showAdminError(error.message);
      }
    });
    actionsCell.appendChild(deleteButton);

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
    dom.adminUsers.textContent = "";
    state.agents.forEach((agent) => dom.adminUsers.appendChild(buildAdminUserRow(agent)));
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

    fillWithPlaceholder(dom.departmentFilter, i18n.t("filter.department.all"));
    fillWithPlaceholder(dom.transferDepartment, i18n.t("aside.choose"));
    fillWithPlaceholder(dom.newUserDepartment, i18n.t("admin.noDepartment"));
    fillWithPlaceholder(dom.newAccountDepartment, i18n.t("admin.noDepartment"));
    fillWithPlaceholder(dom.hotelAdminDepartment, "— Elegir —");
    fillWithPlaceholder(dom.hotelDepartment, "— Elegir —");

    // El horario conserva el departamento elegido si sigue existiendo, para
    // no perder la selección al recargar la lista tras guardar. La opción de
    // «toda la empresa» no sale de la lista, así que se repone a mano.
    const chosen = dom.hoursDepartment.value;
    fillWithPlaceholder(dom.hoursDepartment, i18n.t("aside.choose"));
    const defaults = document.createElement("option");
    defaults.value = "__defaults__";
    defaults.textContent = i18n.t("hours.defaults");
    dom.hoursDepartment.insertBefore(defaults, dom.hoursDepartment.options[1] || null);
    dom.hoursDepartment.value = chosen;
    dom.hoursEditor.hidden = !dom.hoursDepartment.value;

    dom.departmentList.textContent = "";
    if (state.departments.length === 0) {
      const empty = document.createElement("li");
      empty.className = "console__muted";
      empty.textContent = i18n.t("admin.departmentsEmpty");
      dom.departmentList.appendChild(empty);
    } else {
      state.departments.forEach((department) => {
        const item = document.createElement("li");
        item.textContent = department.name;
        dom.departmentList.appendChild(item);
      });
    }
  }

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
      await loadAgents();
      await loadChannelAccounts();
      setStatus(i18n.t("admin.departmentCreated"));
    } catch (error) {
      showAdminError(error.message);
    }
  });

  /* ------------------------------------------------------------------ macros */

  //: La etiqueta se resuelve al pintar, no aquí: si se guardara traducida,
  //: cambiar de idioma dejaría los pasos ya dibujados en el idioma anterior.
  const MACRO_ACTIONS = {
    label: { clave: "macro.short.label", campo: "label_id", origen: "labels" },
    reply: {
      clave: "macro.short.reply",
      campo: "canned_response_id",
      origen: "cannedResponses",
    },
    note: { clave: "macro.short.note", campo: "body", origen: null },
    transfer_department: {
      clave: "macro.short.transfer",
      campo: "department_id",
      origen: "departments",
    },
    close: { clave: "macro.short.close", campo: null, origen: null },
  };

  /** Qué campo pedir según la acción: cada una necesita lo suyo, o nada. */
  function refreshMacroStepFields() {
    const action = dom.macroStepAction.value;
    const spec = MACRO_ACTIONS[action];
    dom.macroStepBodyField.hidden = action !== "note";
    dom.macroStepTargetField.hidden = !spec.origen;
    if (!spec.origen) {
      return;
    }
    dom.macroStepTargetLabel.textContent = i18n.t(`macro.target.${
      { label: "label", reply: "reply", transfer_department: "transfer" }[action]
    }`);
    dom.macroStepTarget.textContent = "";
    (state[spec.origen] || []).forEach((row) => {
      const option = document.createElement("option");
      option.value = row.id;
      option.textContent = row.name || `/${row.shortcode} — ${row.title}`;
      dom.macroStepTarget.appendChild(option);
    });
  }

  function renderMacroDraft() {
    dom.macroSteps.textContent = "";
    state.macroDraft.forEach((step, index) => {
      const item = document.createElement("li");
      item.textContent = describeMacroStep(step);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "panel__list-remove";
      remove.textContent = "×";
      remove.setAttribute("aria-label", i18n.t("macro.removeStep"));
      remove.addEventListener("click", () => {
        state.macroDraft.splice(index, 1);
        renderMacroDraft();
      });
      item.appendChild(remove);
      dom.macroSteps.appendChild(item);
    });
  }

  function describeMacroStep(step) {
    const spec = MACRO_ACTIONS[step.action];
    if (step.action === "note") {
      return `${i18n.t(spec.clave)}: «${step.body}»`;
    }
    if (!spec.origen) {
      return i18n.t(spec.clave);
    }
    const row = (state[spec.origen] || []).find((r) => r.id === step[spec.campo]);
    return `${i18n.t(spec.clave)}: ${row?.name || row?.title || "—"}`;
  }

  dom.macroStepAction.addEventListener("change", refreshMacroStepFields);

  dom.macroStepAdd.addEventListener("click", () => {
    showAdminError("");
    const action = dom.macroStepAction.value;
    const spec = MACRO_ACTIONS[action];
    const step = { action };
    if (action === "note") {
      const body = dom.macroStepBody.value.trim();
      if (!body) {
        showAdminError(i18n.t("macro.noteNeedsText"));
        return;
      }
      step.body = body;
      dom.macroStepBody.value = "";
    } else if (spec.origen) {
      if (!dom.macroStepTarget.value) {
        showAdminError(
          i18n.t("macro.createFirst", {
            what: dom.macroStepTargetLabel.textContent.toLowerCase(),
          }),
        );
        return;
      }
      step[spec.campo] = dom.macroStepTarget.value;
    }
    state.macroDraft.push(step);
    renderMacroDraft();
  });

  dom.createMacroForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");
    const name = dom.newMacroName.value.trim();
    if (!name || state.macroDraft.length === 0) {
      showAdminError(i18n.t("macro.needsNameAndStep"));
      return;
    }
    try {
      await api("/api/macros", {
        method: "POST",
        body: JSON.stringify({ name, steps: state.macroDraft }),
      });
      dom.newMacroName.value = "";
      state.macroDraft = [];
      renderMacroDraft();
      await loadMacros();
      setStatus(i18n.t("macro.created", { name }));
    } catch (error) {
      showAdminError(error.message);
    }
  });

  async function loadMacros() {
    try {
      state.macros = await api("/api/macros");
    } catch {
      state.macros = [];
    }

    dom.macroList.textContent = "";
    dom.macroList.classList.add("panel__list--removable");
    if (state.macros.length === 0) {
      const empty = document.createElement("li");
      empty.className = "console__muted";
      empty.textContent = i18n.t("macro.empty");
      dom.macroList.appendChild(empty);
    } else {
      state.macros.forEach((macro) => {
        const item = document.createElement("li");
        const label = document.createElement("span");
        label.textContent = i18n.t("macro.stepCount", {
          name: macro.name,
          n: macro.steps.length,
        });
        label.title = macro.steps.map(describeMacroStep).join(" → ");
        item.appendChild(label);
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "panel__list-remove";
        remove.textContent = i18n.t("admin.remove");
        remove.addEventListener("click", async () => {
          try {
            await api(`/api/macros/${macro.id}`, { method: "DELETE" });
            await loadMacros();
          } catch (error) {
            showAdminError(error.message);
          }
        });
        item.appendChild(remove);
        dom.macroList.appendChild(item);
      });
    }
    renderMacroButtons();
  }

  function renderMacroButtons() {
    dom.macroButtons.textContent = "";
    dom.macroPanel.hidden = state.macros.length === 0;
    state.macros.forEach((macro) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "macro-run";
      button.textContent = macro.name;
      button.title = macro.steps.map(describeMacroStep).join(" → ");
      button.addEventListener("click", () => runMacro(macro, button));
      dom.macroButtons.appendChild(button);
    });
  }

  async function runMacro(macro, button) {
    if (!state.selected) {
      return;
    }
    button.disabled = true;
    try {
      const result = await api(
        `/api/conversations/${state.selected.id}/macros/${macro.id}`,
        { method: "POST" },
      );
      setStatus(i18n.t("macro.applied", {
        name: macro.name,
        n: result.applied.length,
      }));
      // La macro pudo etiquetar, derivar o cerrar: se recarga todo lo que
      // pudo haber cambiado en vez de adivinar qué tocó.
      await loadConversations();
      await Promise.all([loadMessages(), loadNotes(), loadAssignments()]);
    } catch (error) {
      setStatus(error.message);
    } finally {
      button.disabled = false;
    }
  }

  /* ------------------------------------------------- horario de atención */

  //: Día ISO (1 = lunes … 7 = domingo), el mismo formato que guarda el
  //: servidor. El rótulo se traduce al pintar la tabla.
  const WEEKDAYS = ["1", "2", "3", "4", "5", "6", "7"];

  function buildHoursRows() {
    dom.hoursDays.textContent = "";
    WEEKDAYS.forEach((day) => {
      const label = i18n.t(`day.${day}`);
      const row = document.createElement("tr");
      row.dataset.day = day;
      row.dataset.open = "false";

      const dayCell = document.createElement("td");
      dayCell.className = "hours-table__day";
      const dayLabel = document.createElement("label");
      const toggle = document.createElement("input");
      toggle.type = "checkbox";
      toggle.dataset.role = "open";
      toggle.setAttribute("aria-label", `Atiende el ${label}`);
      toggle.addEventListener("change", () => {
        row.dataset.open = String(toggle.checked);
      });
      dayLabel.appendChild(toggle);
      dayLabel.appendChild(document.createTextNode(label));
      dayCell.appendChild(dayLabel);
      row.appendChild(dayCell);

      // Dos franjas: la segunda queda vacía salvo turno partido.
      ["opens", "closes", "opens2", "closes2"].forEach((role) => {
        const cell = document.createElement("td");
        const field = document.createElement("input");
        field.type = "time";
        field.dataset.role = role;
        field.setAttribute("aria-label", `${role} del ${label}`);
        cell.appendChild(field);
        row.appendChild(cell);
      });

      dom.hoursDays.appendChild(row);
    });
  }

  function fillHoursForm(department) {
    dom.hoursTimezone.value = department?.timezone || "";
    dom.hoursMessage.value = department?.out_of_hours_message || "";
    dom.hoursSlaTarget.value = department?.first_response_target_minutes ?? "";
    const schedule = department?.business_hours || {};
    dom.hoursDays.querySelectorAll("tr").forEach((row) => {
      const spans = schedule[row.dataset.day] || [];
      const value = (index, part) => spans[index]?.[part] || "";
      row.querySelector('[data-role="open"]').checked = spans.length > 0;
      row.dataset.open = String(spans.length > 0);
      row.querySelector('[data-role="opens"]').value = value(0, 0);
      row.querySelector('[data-role="closes"]').value = value(0, 1);
      row.querySelector('[data-role="opens2"]').value = value(1, 0);
      row.querySelector('[data-role="closes2"]').value = value(1, 1);
    });
  }

  function collectHours() {
    const schedule = {};
    dom.hoursDays.querySelectorAll("tr").forEach((row) => {
      if (!row.querySelector('[data-role="open"]').checked) {
        // Día sin marcar: se omite, que es como se representa "cerrado".
        return;
      }
      const spans = [];
      [
        ["opens", "closes"],
        ["opens2", "closes2"],
      ].forEach(([from, to]) => {
        const opens = row.querySelector(`[data-role="${from}"]`).value;
        const closes = row.querySelector(`[data-role="${to}"]`).value;
        // Una franja a medio completar se descarta: el servidor la
        // rechazaría y no hay forma de adivinar la hora que falta.
        if (opens && closes) {
          spans.push([opens, closes]);
        }
      });
      if (spans.length > 0) {
        schedule[row.dataset.day] = spans;
      }
    });
    return schedule;
  }

  //: Valor de la opción que edita lo que rige por omisión en todo el inquilino.
  const SERVICE_DEFAULTS = "__defaults__";

  dom.hoursDepartment.addEventListener("change", async () => {
    const chosen = dom.hoursDepartment.value;
    dom.hoursDefaultsHint.hidden = chosen !== SERVICE_DEFAULTS;
    if (chosen === SERVICE_DEFAULTS) {
      dom.hoursEditor.hidden = false;
      try {
        fillHoursForm(await api("/api/admin/service-defaults"));
      } catch (error) {
        showAdminError(error.message);
      }
      return;
    }
    const department = state.departments.find((row) => row.id === chosen);
    dom.hoursEditor.hidden = !department;
    if (department) {
      fillHoursForm(department);
    }
  });

  dom.hoursSave.addEventListener("click", async () => {
    showAdminError("");
    const chosen = dom.hoursDepartment.value;
    if (!chosen) {
      return;
    }
    const isDefaults = chosen === SERVICE_DEFAULTS;
    const url = isDefaults
      ? "/api/admin/service-defaults"
      : `/api/departments/${chosen}/business-hours`;
    dom.hoursSave.disabled = true;
    try {
      const saved = await api(url, {
        method: "PUT",
        body: JSON.stringify({
          business_hours: collectHours(),
          timezone: dom.hoursTimezone.value.trim() || null,
          out_of_hours_message: dom.hoursMessage.value.trim() || null,
          first_response_target_minutes: Number(dom.hoursSlaTarget.value) || null,
        }),
      });
      if (isDefaults) {
        setStatus(i18n.t("hours.savedDefaults"));
        return;
      }
      // La copia en memoria se actualiza para que reabrir el panel no
      // muestre lo anterior.
      const index = state.departments.findIndex((row) => row.id === saved.id);
      if (index >= 0) {
        state.departments[index] = saved;
      }
      setStatus(i18n.t("hours.saved", { name: saved.name }));
    } catch (error) {
      showAdminError(error.message);
    } finally {
      dom.hoursSave.disabled = false;
    }
  });

  /* -------------------------------------------------------------- etiquetas */

  async function loadLabels() {
    try {
      state.labels = await api("/api/labels");
    } catch {
      state.labels = [];
    }

    dom.labelFilter.textContent = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = i18n.t("filter.label.all");
    dom.labelFilter.appendChild(placeholder);
    state.labels.forEach((label) => {
      const option = document.createElement("option");
      option.value = label.id;
      option.textContent = label.name;
      dom.labelFilter.appendChild(option);
    });

    dom.labelList.textContent = "";
    dom.labelList.classList.add("panel__list--removable");
    if (state.labels.length === 0) {
      const empty = document.createElement("li");
      empty.className = "console__muted";
      empty.textContent = i18n.t("admin.labelsEmpty");
      dom.labelList.appendChild(empty);
    } else {
      state.labels.forEach((label) => {
        const item = document.createElement("li");
        const swatch = document.createElement("span");
        swatch.textContent = label.name;
        swatch.style.color = label.color;
        item.appendChild(swatch);
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "panel__list-remove";
        remove.textContent = "Eliminar";
        remove.addEventListener("click", async () => {
          try {
            await api(`/api/labels/${label.id}`, { method: "DELETE" });
            await loadLabels();
            await loadConversations();
          } catch (error) {
            showAdminError(error.message);
          }
        });
        item.appendChild(remove);
        dom.labelList.appendChild(item);
      });
    }

    if (state.selected) {
      renderConversationLabelToggles();
    }
  }

  dom.createLabelForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");
    const name = dom.newLabelName.value.trim();
    if (!name) {
      return;
    }
    try {
      await api("/api/labels", {
        method: "POST",
        body: JSON.stringify({ name, color: dom.newLabelColor.value }),
      });
      dom.newLabelName.value = "";
      await loadLabels();
      setStatus(i18n.t("admin.labelCreated"));
    } catch (error) {
      showAdminError(error.message);
    }
  });

  /* ------------------------------------------------------- respuestas guardadas */

  async function loadCannedResponses() {
    try {
      state.cannedResponses = await api("/api/canned-responses");
    } catch {
      state.cannedResponses = [];
    }

    dom.cannedResponseList.textContent = "";
    dom.cannedResponseList.classList.add("panel__list--removable");
    if (state.cannedResponses.length === 0) {
      const empty = document.createElement("li");
      empty.className = "console__muted";
      empty.textContent = i18n.t("admin.cannedEmpty");
      dom.cannedResponseList.appendChild(empty);
    } else {
      state.cannedResponses.forEach((canned) => {
        const item = document.createElement("li");
        const label = document.createElement("span");
        label.textContent = `/${canned.shortcode} — ${canned.title}`;
        item.appendChild(label);
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "panel__list-remove";
        remove.textContent = "Eliminar";
        remove.addEventListener("click", async () => {
          try {
            await api(`/api/canned-responses/${canned.id}`, { method: "DELETE" });
            await loadCannedResponses();
          } catch (error) {
            showAdminError(error.message);
          }
        });
        item.appendChild(remove);
        dom.cannedResponseList.appendChild(item);
      });
    }
  }

  dom.createCannedResponseForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");
    const shortcode = dom.newCannedShortcode.value.trim();
    const title = dom.newCannedTitle.value.trim();
    const body = dom.newCannedBody.value.trim();
    if (!shortcode || !title || !body) {
      return;
    }
    try {
      await api("/api/canned-responses", {
        method: "POST",
        body: JSON.stringify({ shortcode, title, body }),
      });
      dom.newCannedShortcode.value = "";
      dom.newCannedTitle.value = "";
      dom.newCannedBody.value = "";
      await loadCannedResponses();
      setStatus(i18n.t("admin.cannedCreated"));
    } catch (error) {
      showAdminError(error.message);
    }
  });

  /* ------------------------------------------------------------ cuentas de canal */

  const CHANNEL_ACCOUNT_LABELS = { whatsapp: "WhatsApp", facebook: "Facebook", msbot: "Teams" };

  function buildChannelAccountRow(account) {
    const row = document.createElement("tr");

    [
      CHANNEL_ACCOUNT_LABELS[account.channel] || account.channel,
      account.external_id,
    ].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    });

    const nameCell = document.createElement("td");
    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.maxLength = 160;
    nameInput.value = account.display_name || "";
    nameInput.setAttribute("aria-label", `Nombre de ${account.external_id}`);
    nameCell.appendChild(nameInput);
    row.appendChild(nameCell);

    const departmentCell = document.createElement("td");
    const departmentSelect = document.createElement("select");
    const noneOption = document.createElement("option");
    noneOption.value = "";
    noneOption.textContent = "Sin departamento";
    departmentSelect.appendChild(noneOption);
    state.departments.forEach((department) => {
      const option = document.createElement("option");
      option.value = department.id;
      option.textContent = department.name;
      option.selected = department.id === account.department_id;
      departmentSelect.appendChild(option);
    });
    departmentSelect.setAttribute("aria-label", `Departamento de ${account.external_id}`);
    departmentCell.appendChild(departmentSelect);
    row.appendChild(departmentCell);

    const tokenCell = document.createElement("td");
    const tokenInput = document.createElement("input");
    tokenInput.type = "password";
    tokenInput.maxLength = 4096;
    tokenInput.autocomplete = "off";
    tokenInput.placeholder = account.has_own_credentials ? "Reemplazar" : "Sin token propio";
    tokenInput.setAttribute("aria-label", `Nuevo token de ${account.external_id}`);
    tokenCell.appendChild(tokenInput);
    row.appendChild(tokenCell);

    const statusCell = document.createElement("td");
    statusCell.textContent = account.is_active ? "Activa" : "Inactiva";
    row.appendChild(statusCell);

    const actionsCell = document.createElement("td");
    const saveButton = document.createElement("button");
    saveButton.type = "button";
    saveButton.className = "ghost-button";
    saveButton.textContent = "Guardar";
    saveButton.addEventListener("click", async () => {
      showAdminError("");
      const displayName = nameInput.value.trim();
      const body = {
        display_name: displayName || null,
        department_id: departmentSelect.value || null,
      };
      if (tokenInput.value) {
        body.access_token = tokenInput.value;
      }
      saveButton.disabled = true;
      try {
        await api(`/api/channel-accounts/${account.id}`, {
          method: "PATCH",
          body: JSON.stringify(body),
        });
        await loadChannelAccounts();
        setStatus("Cuenta de canal actualizada.");
      } catch (error) {
        showAdminError(error.message);
      } finally {
        saveButton.disabled = false;
      }
    });
    actionsCell.appendChild(saveButton);

    const toggleButton = document.createElement("button");
    toggleButton.type = "button";
    toggleButton.className = "ghost-button";
    toggleButton.textContent = account.is_active ? "Desactivar" : "Reactivar";
    toggleButton.addEventListener("click", async () => {
      showAdminError("");
      try {
        await api(`/api/channel-accounts/${account.id}`, {
          method: "PATCH",
          body: JSON.stringify({ is_active: !account.is_active }),
        });
        await loadChannelAccounts();
        setStatus(
          i18n.t(account.is_active ? "accounts.deactivated" : "accounts.reactivated"),
        );
      } catch (error) {
        showAdminError(error.message);
      }
    });
    actionsCell.appendChild(toggleButton);

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "ghost-button ghost-button--danger";
    deleteButton.textContent = i18n.t("accounts.delete");
    deleteButton.addEventListener("click", () => removeChannelAccount(account, deleteButton));
    actionsCell.appendChild(deleteButton);

    row.appendChild(actionsCell);
    return row;
  }

  /* El servidor responde 409 con cuántas conversaciones hay detrás; recién
     entonces se pregunta, con ese número delante, y se reintenta confirmando.
     Así el aviso dice qué se pierde, en vez de un «¿está seguro?» a ciegas. */
  async function removeChannelAccount(account, button) {
    showAdminError("");
    button.disabled = true;
    try {
      await api(`/api/channel-accounts/${account.id}`, { method: "DELETE" });
      await loadChannelAccounts();
      setStatus(i18n.t("accounts.deleted", { name: account.external_id }));
      return;
    } catch (error) {
      // Sin conversaciones detrás, el borrado ya ocurrió o falló de verdad.
      if (!/\d/.test(error.message) || !window.confirm(error.message)) {
        if (!/\d/.test(error.message)) {
          showAdminError(error.message);
        }
        return;
      }
    } finally {
      button.disabled = false;
    }

    try {
      await api(`/api/channel-accounts/${account.id}?confirm=true`, { method: "DELETE" });
      await loadChannelAccounts();
      setStatus(i18n.t("accounts.deleted", { name: account.external_id }));
    } catch (error) {
      showAdminError(error.message);
    }
  }

  async function loadChannelAccounts() {
    let accounts = [];
    try {
      accounts = await api("/api/channel-accounts");
    } catch {
      accounts = [];
    }
    dom.channelAccountsTable.textContent = "";
    accounts.forEach((account) => dom.channelAccountsTable.appendChild(buildChannelAccountRow(account)));
  }

  /* Teams no usa ni token de verificación ni clave de app: su autenticación es
     un JWT firmado por Microsoft. Enseñar los campos invitaría a rellenarlos
     con algo, y ese algo no serviría para nada. */
  function reflectAccountChannel() {
    const esMeta = dom.newAccountChannel.value !== "msbot";
    document.querySelectorAll("[data-meta-only]").forEach((campo) => {
      campo.hidden = !esMeta;
    });
  }

  dom.newAccountChannel.addEventListener("change", reflectAccountChannel);
  reflectAccountChannel();

  dom.createChannelAccountForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");
    try {
      await api("/api/channel-accounts", {
        method: "POST",
        body: JSON.stringify({
          channel: dom.newAccountChannel.value,
          external_id: dom.newAccountExternalId.value.trim(),
          display_name: dom.newAccountName.value.trim() || null,
          department_id: dom.newAccountDepartment.value || null,
          access_token: dom.newAccountToken.value || null,
          verify_token: dom.newAccountVerifyToken.value || null,
          app_secret: dom.newAccountAppSecret.value || null,
        }),
      });
      dom.createChannelAccountForm.reset();
      await loadChannelAccounts();
      setStatus("Cuenta de canal conectada.");
    } catch (error) {
      showAdminError(error.message);
    }
  });

  /* --------------------------------------------- módulo Hotel: administración */

  function formatMoney(cents, currency) {
    return cents == null ? "—" : `${(cents / 100).toFixed(2)} ${currency}`;
  }

  let hotelAdminRoomTypes = [];
  // Nulo = el formulario de tarifas crea una nueva; con id, corrige esta.
  let editingRatePlanId = null;

  function fillRoomTypeSelect(select) {
    select.textContent = "";
    hotelAdminRoomTypes.forEach((roomType) => {
      const option = document.createElement("option");
      option.value = roomType.id;
      option.textContent = roomType.is_active ? roomType.name : `${roomType.name} (retirada)`;
      select.appendChild(option);
    });
  }

  function buildRoomTypeRow(roomType) {
    const row = document.createElement("tr");
    [roomType.name, roomType.capacity, roomType.is_active ? "Activa" : "Retirada"].forEach(
      (value) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      },
    );
    const actionsCell = document.createElement("td");
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "ghost-button";
    toggle.textContent = roomType.is_active ? "Retirar" : "Reactivar";
    toggle.addEventListener("click", async () => {
      showAdminError("");
      try {
        await api(
          `/api/departments/${dom.hotelAdminDepartment.value}/hotel/room-types/${roomType.id}`,
          { method: "PATCH", body: JSON.stringify({ is_active: !roomType.is_active }) },
        );
        await loadHotelRoomTypes();
      } catch (error) {
        showAdminError(error.message);
      }
    });
    actionsCell.appendChild(toggle);
    row.appendChild(actionsCell);
    return row;
  }

  async function loadHotelRoomTypes() {
    const departmentId = dom.hotelAdminDepartment.value;
    hotelAdminRoomTypes = await api(`/api/departments/${departmentId}/hotel/room-types`);
    dom.roomTypesTable.textContent = "";
    hotelAdminRoomTypes.forEach((roomType) =>
      dom.roomTypesTable.appendChild(buildRoomTypeRow(roomType)),
    );
    fillRoomTypeSelect(dom.newRoomTypeSelect);
    fillRoomTypeSelect(dom.newRatePlanTypeSelect);
  }

  function buildRoomRow(room) {
    const row = document.createElement("tr");
    [room.code, room.room_type_name].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    });

    const statusCell = document.createElement("td");
    const statusSelect = document.createElement("select");
    [
      ["available", "Disponible"],
      ["maintenance", "Mantenimiento"],
      ["out_of_service", "Fuera de servicio"],
    ].forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      option.selected = value === room.status;
      statusSelect.appendChild(option);
    });
    statusCell.appendChild(statusSelect);
    row.appendChild(statusCell);

    const actionsCell = document.createElement("td");
    const save = document.createElement("button");
    save.type = "button";
    save.className = "ghost-button";
    save.textContent = "Guardar";
    save.addEventListener("click", async () => {
      showAdminError("");
      try {
        await api(`/api/departments/${dom.hotelAdminDepartment.value}/hotel/rooms/${room.id}`, {
          method: "PATCH",
          body: JSON.stringify({ status: statusSelect.value }),
        });
        await loadHotelRooms();
        setStatus("Habitación actualizada.");
      } catch (error) {
        showAdminError(error.message);
      }
    });
    actionsCell.appendChild(save);
    row.appendChild(actionsCell);
    return row;
  }

  async function loadHotelRooms() {
    const departmentId = dom.hotelAdminDepartment.value;
    const rooms = await api(`/api/departments/${departmentId}/hotel/rooms`);
    dom.roomsTable.textContent = "";
    rooms.forEach((room) => dom.roomsTable.appendChild(buildRoomRow(room)));
  }

  function buildRatePlanRow(ratePlan) {
    const roomType = hotelAdminRoomTypes.find((rt) => rt.id === ratePlan.room_type_id);
    const row = document.createElement("tr");
    const validity =
      ratePlan.starts_on || ratePlan.ends_on
        ? `${ratePlan.starts_on || "…"} → ${ratePlan.ends_on || "…"}`
        : "Todo el año";
    [
      roomType ? roomType.name : "—",
      ratePlan.name,
      validity,
      formatMoney(ratePlan.nightly_price_cents, ratePlan.currency),
    ].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    });
    const actionsCell = document.createElement("td");
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "ghost-button";
    edit.textContent = "Editar";
    edit.addEventListener("click", () => startEditingRatePlan(ratePlan));
    actionsCell.appendChild(edit);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "ghost-button ghost-button--danger";
    remove.textContent = "Borrar";
    remove.addEventListener("click", async () => {
      showAdminError("");
      try {
        await api(
          `/api/departments/${dom.hotelAdminDepartment.value}/hotel/rate-plans/${ratePlan.id}`,
          { method: "DELETE" },
        );
        if (editingRatePlanId === ratePlan.id) {
          cancelEditingRatePlan();
        }
        await loadHotelRatePlans();
      } catch (error) {
        showAdminError(error.message);
      }
    });
    actionsCell.appendChild(remove);
    row.appendChild(actionsCell);
    return row;
  }

  function startEditingRatePlan(ratePlan) {
    editingRatePlanId = ratePlan.id;
    dom.newRatePlanTypeSelect.value = ratePlan.room_type_id;
    // La categoría no se puede cambiar al editar: una tarifa pertenece a una
    // sola, igual que una habitación. Queda a la vista, no en blanco.
    dom.newRatePlanTypeSelect.disabled = true;
    dom.newRatePlanName.value = ratePlan.name;
    dom.newRatePlanStarts.value = ratePlan.starts_on || "";
    dom.newRatePlanEnds.value = ratePlan.ends_on || "";
    dom.newRatePlanPrice.value = (ratePlan.nightly_price_cents / 100).toFixed(2);
    dom.newRatePlanCurrency.value = ratePlan.currency;
    dom.ratePlanFormTitle.textContent = "Editar tarifa";
    dom.ratePlanSubmit.textContent = "Guardar cambios";
    dom.ratePlanCancelEdit.hidden = false;
  }

  function cancelEditingRatePlan() {
    editingRatePlanId = null;
    dom.createRatePlanForm.reset();
    dom.newRatePlanTypeSelect.disabled = false;
    dom.newRatePlanCurrency.value = "USD";
    dom.ratePlanFormTitle.textContent = "Tarifas";
    dom.ratePlanSubmit.textContent = "Crear tarifa";
    dom.ratePlanCancelEdit.hidden = true;
  }

  dom.ratePlanCancelEdit.addEventListener("click", cancelEditingRatePlan);

  async function loadHotelRatePlans() {
    const departmentId = dom.hotelAdminDepartment.value;
    const ratePlans = await api(`/api/departments/${departmentId}/hotel/rate-plans`);
    dom.ratePlansTable.textContent = "";
    ratePlans.forEach((ratePlan) => dom.ratePlansTable.appendChild(buildRatePlanRow(ratePlan)));
  }

  async function loadHotelAdminSetup() {
    await loadHotelRoomTypes();
    await loadHotelRooms();
    await loadHotelRatePlans();
  }

  dom.hotelAdminDepartment.addEventListener("change", async () => {
    showAdminError("");
    dom.hotelAdminModuleField.hidden = true;
    dom.hotelAdminSetup.hidden = true;
    cancelEditingRatePlan();
    const departmentId = dom.hotelAdminDepartment.value;
    if (!departmentId) {
      return;
    }
    try {
      const module = await api(`/api/departments/${departmentId}/hotel/module`);
      dom.hotelAdminModuleEnabled.checked = module.enabled;
      dom.hotelAdminModuleField.hidden = false;
      dom.hotelAdminSetup.hidden = !module.enabled;
      if (module.enabled) {
        await loadHotelAdminSetup();
      }
    } catch (error) {
      showAdminError(error.message);
    }
  });

  dom.hotelAdminModuleEnabled.addEventListener("change", async () => {
    showAdminError("");
    const enabled = dom.hotelAdminModuleEnabled.checked;
    try {
      await api(`/api/departments/${dom.hotelAdminDepartment.value}/hotel/module`, {
        method: "PUT",
        body: JSON.stringify({ enabled }),
      });
      dom.hotelAdminSetup.hidden = !enabled;
      if (enabled) {
        await loadHotelAdminSetup();
      }
      setStatus(enabled ? "Módulo de hotel activado." : "Módulo de hotel desactivado.");
    } catch (error) {
      dom.hotelAdminModuleEnabled.checked = !enabled;
      showAdminError(error.message);
    }
  });

  dom.createRoomTypeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");
    try {
      await api(`/api/departments/${dom.hotelAdminDepartment.value}/hotel/room-types`, {
        method: "POST",
        body: JSON.stringify({
          name: dom.newRoomTypeName.value.trim(),
          capacity: Number(dom.newRoomTypeCapacity.value) || 1,
          description: dom.newRoomTypeDescription.value.trim() || null,
        }),
      });
      dom.createRoomTypeForm.reset();
      dom.newRoomTypeCapacity.value = 2;
      await loadHotelRoomTypes();
      setStatus("Categoría creada.");
    } catch (error) {
      showAdminError(error.message);
    }
  });

  dom.createRoomForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");
    try {
      await api(`/api/departments/${dom.hotelAdminDepartment.value}/hotel/rooms`, {
        method: "POST",
        body: JSON.stringify({
          room_type_id: dom.newRoomTypeSelect.value,
          code: dom.newRoomCode.value.trim(),
        }),
      });
      dom.newRoomCode.value = "";
      await loadHotelRooms();
      setStatus("Habitación creada.");
    } catch (error) {
      showAdminError(error.message);
    }
  });

  dom.createRatePlanForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");
    const price = Number(dom.newRatePlanPrice.value);
    if (!price || price <= 0) {
      showAdminError("El precio debe ser mayor que cero.");
      return;
    }
    const body = {
      name: dom.newRatePlanName.value.trim(),
      starts_on: dom.newRatePlanStarts.value || null,
      ends_on: dom.newRatePlanEnds.value || null,
      nightly_price_cents: Math.round(price * 100),
      currency: (dom.newRatePlanCurrency.value || "USD").toUpperCase(),
    };
    try {
      if (editingRatePlanId) {
        await api(
          `/api/departments/${dom.hotelAdminDepartment.value}/hotel/rate-plans/${editingRatePlanId}`,
          { method: "PATCH", body: JSON.stringify(body) },
        );
        cancelEditingRatePlan();
        await loadHotelRatePlans();
        setStatus("Tarifa actualizada.");
      } else {
        await api(`/api/departments/${dom.hotelAdminDepartment.value}/hotel/rate-plans`, {
          method: "POST",
          body: JSON.stringify({ ...body, room_type_id: dom.newRatePlanTypeSelect.value }),
        });
        dom.createRatePlanForm.reset();
        dom.newRatePlanCurrency.value = "USD";
        await loadHotelRatePlans();
        setStatus("Tarifa creada.");
      }
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

  /* ------------------------------------------------ directorio de contactos */

  let contactsSearchTimer = null;

  function renderContactsTable(rows) {
    dom.contactsTable.textContent = "";
    if (rows.length === 0) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 6;
      td.className = "console__muted";
      td.textContent = "Sin resultados.";
      tr.appendChild(td);
      dom.contactsTable.appendChild(tr);
      return;
    }
    rows.forEach((contact) => {
      const tr = document.createElement("tr");
      [
        contact.display_name || "—",
        contact.primary_phone || "—",
        contact.primary_email || "—",
        contact.conversation_count,
        formatTime(contact.last_message_at) || "—",
      ].forEach((value) => {
        const td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      });
      const actions = document.createElement("td");
      const view = document.createElement("button");
      view.type = "button";
      view.className = "ghost-button";
      view.textContent = "Ver";
      view.addEventListener("click", () => openContactProfile(contact.id));
      actions.appendChild(view);
      tr.appendChild(actions);
      dom.contactsTable.appendChild(tr);
    });
  }

  async function loadContacts(search) {
    try {
      const query = search ? `?search=${encodeURIComponent(search)}` : "";
      const rows = await api(`/api/contacts${query}`);
      renderContactsTable(rows);
    } catch (error) {
      setStatus(error.message);
    }
  }

  function showProfileError(message) {
    dom.profileError.textContent = message;
    dom.profileError.hidden = !message;
  }

  async function openContactProfile(contactId) {
    showProfileError("");
    try {
      const contact = await api(`/api/contacts/${contactId}`);
      state.contactProfileId = contact.id;
      dom.contactProfile.hidden = false;
      dom.profileTitle.textContent = contact.display_name || "Contacto sin nombre";
      dom.profileName.value = contact.display_name || "";
      dom.profilePhone.value = contact.primary_phone || "";
      dom.profileEmail.value = contact.primary_email || "";

      dom.profileComments.textContent = "";
      if (contact.comments.length === 0) {
        const empty = document.createElement("li");
        empty.className = "console__muted";
        empty.textContent = "Todavía no hay comentarios.";
        dom.profileComments.appendChild(empty);
      } else {
        contact.comments.forEach((comment) => {
          const item = document.createElement("li");
          item.textContent = comment.body;
          const meta = document.createElement("span");
          meta.className = "panel__meta";
          meta.textContent = `${comment.agent || "sistema"} · ${formatTime(comment.created_at)}`;
          item.appendChild(meta);
          dom.profileComments.appendChild(item);
        });
      }

      dom.profileConversations.textContent = "";
      if (contact.conversations.length === 0) {
        const empty = document.createElement("li");
        empty.className = "console__muted";
        empty.textContent = "Sin conversaciones.";
        dom.profileConversations.appendChild(empty);
      } else {
        contact.conversations.forEach((conversation) => {
          const item = document.createElement("li");
          const link = document.createElement("button");
          link.type = "button";
          link.className = "ghost-button";
          const channel = CHANNEL_LABELS[conversation.channel] || conversation.channel;
          link.textContent =
            `${channel} · ${conversation.status} · ` +
            (formatTime(conversation.last_message_at) || "sin mensajes");
          link.addEventListener("click", async () => {
            dom.contactsPanel.hidden = true;
            await selectConversation(conversation);
          });
          item.appendChild(link);
          dom.profileConversations.appendChild(item);
        });
      }
    } catch (error) {
      dom.contactProfile.hidden = true;
      showProfileError(error.message);
    }
  }

  dom.contactsButton.addEventListener("click", async () => {
    dom.contactProfile.hidden = true;
    dom.contactsSearch.value = "";
    dom.contactsPanel.hidden = false;
    await loadContacts("");
  });

  dom.contactsClose.addEventListener("click", () => {
    dom.contactsPanel.hidden = true;
  });

  dom.contactsSearch.addEventListener("input", () => {
    clearTimeout(contactsSearchTimer);
    contactsSearchTimer = setTimeout(() => {
      loadContacts(dom.contactsSearch.value.trim());
    }, 300);
  });

  dom.profileSave.addEventListener("click", async () => {
    if (!state.contactProfileId) {
      return;
    }
    showProfileError("");
    dom.profileSave.disabled = true;
    try {
      await api(`/api/contacts/${state.contactProfileId}`, {
        method: "PATCH",
        body: JSON.stringify({
          display_name: dom.profileName.value.trim() || null,
          primary_phone: dom.profilePhone.value.trim() || null,
          primary_email: dom.profileEmail.value.trim() || null,
        }),
      });
      await openContactProfile(state.contactProfileId);
      await loadContacts(dom.contactsSearch.value.trim());
    } catch (error) {
      showProfileError(error.message);
    } finally {
      dom.profileSave.disabled = false;
    }
  });

  dom.profileCommentForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = dom.profileCommentBody.value.trim();
    if (!body || !state.contactProfileId) {
      return;
    }
    try {
      await api(`/api/contacts/${state.contactProfileId}/comments`, {
        method: "POST",
        body: JSON.stringify({ body }),
      });
      dom.profileCommentBody.value = "";
      await openContactProfile(state.contactProfileId);
    } catch (error) {
      showProfileError(error.message);
    }
  });

  /* ------------------------------------- pestañas de administración */

  /* El panel reunía en una sola columna usuarios, canales, etiquetas, macros
     y horarios: había que desplazarse mucho para encontrar cada cosa. Ahora
     cada asunto vive en su pestaña y solo se muestra una a la vez. */
  const adminTabs = [...document.querySelectorAll(".admin-tabs button")];
  const adminPanels = [...document.querySelectorAll(".admin-tab-panel")];

  function showAdminTab(name) {
    adminTabs.forEach((tab) =>
      tab.setAttribute("aria-selected", String(tab.dataset.adminTab === name)),
    );
    adminPanels.forEach((panel) => {
      panel.hidden = panel.id !== `admin-tab-${name}`;
    });
    // Cambiar de sección deja atrás el aviso de la anterior.
    showAdminError("");
  }

  adminTabs.forEach((tab) =>
    tab.addEventListener("click", () => showAdminTab(tab.dataset.adminTab)),
  );

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
    await loadBranding();
    await loadChannelAccounts();
    await loadLabels();
    await loadCannedResponses();
    await loadMacros();
    refreshMacroStepFields();
    // El departamento del hotel no se conserva de una apertura a otra: sin
    // esto, reabrir el panel dejaría a la vista la configuración de la
    // última vez, con el desplegable ya vacío por loadDepartments().
    dom.hotelAdminModuleField.hidden = true;
    dom.hotelAdminSetup.hidden = true;
    // Se abre siempre por usuarios: es lo que más se administra, y volver a
    // la sección donde alguien estuvo la vez anterior desorienta más de lo
    // que ahorra.
    showAdminTab("users");
    dom.adminPanel.hidden = false;
  });

  dom.adminClose.addEventListener("click", () => {
    dom.adminPanel.hidden = true;
  });

  /* ------------------------------------- nueva conversacion saliente */

  /* WhatsApp no deja escribir texto libre a quien no ha escrito en las ultimas
     24 horas: fuera de esa ventana solo admite plantillas aprobadas. Por eso
     este dialogo no tiene campo de texto, sino una plantilla y sus huecos. */

  let startTemplates = [];

  function showStartError(message) {
    dom.startError.textContent = message;
    dom.startError.hidden = !message;
  }

  function selectedTemplate() {
    return startTemplates.find(
      (t) => `${t.name}|${t.language}` === dom.startTemplate.value,
    );
  }

  /* Enseña el texto tal como lo recibira la persona, con los huecos ya
     rellenos. El nombre tecnico de una plantilla no dice nada de lo que
     contiene, y enviarla a ciegas es como firmar sin leer. */
  function renderStartPreview() {
    const plantilla = selectedTemplate();
    if (!plantilla) {
      dom.startPreview.textContent = "";
      return;
    }
    const valores = [...dom.startVariables.querySelectorAll("input")].map((i) => i.value);
    let texto = plantilla.body || "";
    valores.forEach((valor, indice) => {
      texto = texto.split(`{{${indice + 1}}}`).join(valor || `{{${indice + 1}}}`);
    });
    dom.startPreview.textContent = texto;
  }

  function renderStartVariables() {
    const plantilla = selectedTemplate();
    dom.startVariables.textContent = "";
    for (let n = 1; n <= (plantilla ? plantilla.variables : 0); n += 1) {
      const label = document.createElement("label");
      label.className = "panel__field";
      const span = document.createElement("span");
      span.textContent = i18n.t("start.variable", { n });
      const input = document.createElement("input");
      input.type = "text";
      input.maxLength = 200;
      input.required = true;
      input.addEventListener("input", renderStartPreview);
      label.append(span, input);
      dom.startVariables.appendChild(label);
    }
    renderStartPreview();
  }

  function fillStartTemplates() {
    dom.startTemplate.textContent = "";
    startTemplates.forEach((plantilla) => {
      const option = document.createElement("option");
      option.value = `${plantilla.name}|${plantilla.language}`;
      option.textContent = `${plantilla.name} (${plantilla.language})`;
      dom.startTemplate.appendChild(option);
    });
    renderStartVariables();
  }

  async function openStartPanel() {
    showStartError("");
    dom.startPanel.hidden = false;
    dom.startSend.disabled = true;
    try {
      startTemplates = await api("/api/whatsapp/templates");
      if (startTemplates.length === 0) {
        showStartError(i18n.t("start.noTemplates"));
        return;
      }
      fillStartTemplates();
      dom.startSend.disabled = false;
    } catch (error) {
      // 503 = la instalacion no tiene WhatsApp configurado. El boton se
      // retira para el resto de la sesion: repetir el intento daria siempre
      // lo mismo, y dejarlo a la vista promete algo que no existe.
      if (error.status === 503) {
        dom.startButton.hidden = true;
      }
      showStartError(error.message);
    }
  }

  dom.startButton.addEventListener("click", openStartPanel);
  dom.startClose.addEventListener("click", () => {
    dom.startPanel.hidden = true;
  });
  dom.startTemplate.addEventListener("change", renderStartVariables);

  dom.startForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showStartError("");
    const plantilla = selectedTemplate();
    if (!plantilla) {
      return;
    }
    dom.startSend.disabled = true;
    try {
      const data = await api("/api/conversations/start", {
        method: "POST",
        body: JSON.stringify({
          to: dom.startTo.value.trim(),
          template: plantilla.name,
          language: plantilla.language,
          variables: [...dom.startVariables.querySelectorAll("input")].map((i) => i.value.trim()),
        }),
      });
      dom.startPanel.hidden = true;
      dom.startForm.reset();
      dom.startVariables.textContent = "";
      dom.startPreview.textContent = "";
      await loadConversations();
      // La conversacion nueva entra en la cola comun. Si el agente esta
      // mirando otra bandeja no aparecera en su lista, y entonces se avisa en
      // vez de abrir algo que no esta a la vista.
      const abierta = state.conversations.find((c) => c.id === data.conversation_id);
      if (abierta) {
        await selectConversation(abierta);
      }
      setStatus(i18n.t(abierta ? "start.sent" : "start.sentHidden"));
    } catch (error) {
      showStartError(error.message);
    } finally {
      dom.startSend.disabled = false;
    }
  });

  /* --------------------------------------------------- módulo Hotel: reservas */

  const HOTEL_STATUS_LABELS = {
    pending: "Pendiente",
    confirmed: "Confirmada",
    checked_in: "Con check-in",
    checked_out: "Con check-out",
    cancelled: "Cancelada",
    no_show: "No show",
  };

  // Reflejo, solo para no ofrecer botones que el servidor rechazaría; la
  // regla de verdad vive en HOTEL_RESERVATION_TRANSITIONS, en console.py.
  const HOTEL_STATUS_TRANSITIONS = {
    pending: [
      ["confirmed", "Confirmar"],
      ["cancelled", "Cancelar"],
    ],
    confirmed: [
      ["checked_in", "Check-in"],
      ["cancelled", "Cancelar"],
      ["no_show", "No show"],
    ],
    checked_in: [["checked_out", "Check-out"]],
    checked_out: [],
    cancelled: [],
    no_show: [],
  };

  // Nulo = el formulario de abajo crea una reserva; con id, corrige esta.
  let editingReservationId = null;
  // Contacto elegido en el buscador, para vincularlo a la reserva además de
  // copiarle nombre/teléfono/correo. Se limpia junto con el formulario.
  let hotelSelectedContactId = null;
  let hotelContactSearchTimer = null;

  function showHotelError(message) {
    dom.hotelError.textContent = message;
    dom.hotelError.hidden = !message;
  }

  async function loadHotelAvailability() {
    dom.hotelAvailabilityList.textContent = "";
    dom.hotelReservationRoom.textContent = "";
    if (!dom.hotelCheckIn.value || !dom.hotelCheckOut.value) {
      return;
    }
    const departmentId = dom.hotelDepartment.value;
    const query = new URLSearchParams({
      check_in: dom.hotelCheckIn.value,
      check_out: dom.hotelCheckOut.value,
    });
    // Al editar, la habitación que la propia reserva ya ocupa no debe
    // excluirse por chocar contra sí misma en sus propias fechas.
    if (editingReservationId) {
      query.set("exclude_reservation_id", editingReservationId);
    }
    try {
      const rooms = await api(`/api/departments/${departmentId}/hotel/availability?${query}`);
      if (rooms.length === 0) {
        const empty = document.createElement("li");
        empty.className = "console__muted";
        empty.textContent = "Sin habitaciones libres para esas fechas.";
        dom.hotelAvailabilityList.appendChild(empty);
        return;
      }
      rooms.forEach((room) => {
        const item = document.createElement("li");
        item.textContent = `${room.room_type_name} — ${room.code}`;
        dom.hotelAvailabilityList.appendChild(item);

        const option = document.createElement("option");
        option.value = room.id;
        option.textContent = `${room.room_type_name} — ${room.code}`;
        dom.hotelReservationRoom.appendChild(option);
      });
    } catch (error) {
      showHotelError(error.message);
    }
  }

  function formatHotelRevenue(revenue) {
    if (!revenue || revenue.length === 0) {
      return "sin reservas con precio";
    }
    return revenue.map((row) => formatMoney(row.total_cents, row.currency)).join(" + ");
  }

  async function loadHotelReport() {
    const departmentId = dom.hotelDepartment.value;
    try {
      const report = await api(`/api/departments/${departmentId}/hotel/report`);
      dom.hotelReportArrivals.textContent = report.arrivals_today;
      dom.hotelReportDepartures.textContent = report.departures_today;
      dom.hotelReportOccupancy.textContent = `${report.occupied_rooms} / ${report.total_rooms}`;
      dom.hotelReportPending.textContent = report.pending_count;
      dom.hotelReportRevenue.textContent = formatHotelRevenue(report.revenue_next_30_days);
    } catch {
      // El resumen es un agregado, no algo bloqueante: si falla, el resto
      // del panel —disponibilidad, reservas— sigue funcionando igual.
      dom.hotelReportArrivals.textContent = "—";
      dom.hotelReportDepartures.textContent = "—";
      dom.hotelReportOccupancy.textContent = "—";
      dom.hotelReportPending.textContent = "—";
      dom.hotelReportRevenue.textContent = "—";
    }
  }

  function setHotelContactLink(contact) {
    hotelSelectedContactId = contact ? contact.id : null;
    if (!contact) {
      dom.hotelContactLinked.hidden = true;
      dom.hotelContactLinked.textContent = "";
      return;
    }
    dom.hotelGuestName.value = contact.display_name || dom.hotelGuestName.value;
    dom.hotelGuestPhone.value = contact.primary_phone || dom.hotelGuestPhone.value;
    dom.hotelGuestEmail.value = contact.primary_email || dom.hotelGuestEmail.value;
    dom.hotelContactLinked.hidden = false;
    dom.hotelContactLinked.textContent =
      `Vinculado a ${contact.display_name || contact.primary_email || contact.primary_phone}.`;
    dom.hotelContactResults.hidden = true;
    dom.hotelContactResults.textContent = "";
    dom.hotelContactSearch.value = "";
  }

  async function searchHotelContacts(term) {
    dom.hotelContactResults.textContent = "";
    if (!term) {
      dom.hotelContactResults.hidden = true;
      return;
    }
    try {
      const matches = await api(
        `/api/departments/${dom.hotelDepartment.value}/hotel/contacts?q=${encodeURIComponent(term)}`,
      );
      if (matches.length === 0) {
        const empty = document.createElement("li");
        empty.className = "console__muted";
        empty.textContent = "Sin coincidencias.";
        dom.hotelContactResults.appendChild(empty);
      } else {
        matches.forEach((contact) => {
          const item = document.createElement("li");
          const button = document.createElement("button");
          button.type = "button";
          button.className = "ghost-button";
          const label = [contact.display_name, contact.primary_phone, contact.primary_email]
            .filter(Boolean)
            .join(" · ");
          button.textContent = label || "(sin datos)";
          button.addEventListener("click", () => setHotelContactLink(contact));
          item.appendChild(button);
          dom.hotelContactResults.appendChild(item);
        });
      }
      dom.hotelContactResults.hidden = false;
    } catch (error) {
      showHotelError(error.message);
    }
  }

  function buildHotelReservationRow(reservation) {
    const row = document.createElement("tr");
    [
      `${reservation.room_type_name} — ${reservation.room_code}`,
      reservation.guest_name,
      reservation.check_in,
      reservation.check_out,
      formatMoney(reservation.nightly_price_cents, reservation.currency),
      HOTEL_STATUS_LABELS[reservation.status] || reservation.status,
    ].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    });

    const actionsCell = document.createElement("td");

    // Solo tiene sentido corregir fechas u habitación mientras la estadía
    // todavía no arrancó; una vez con check-in o cerrada, ya pasó.
    if (reservation.status === "pending" || reservation.status === "confirmed") {
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "ghost-button";
      edit.textContent = "Editar";
      edit.addEventListener("click", () => startEditingReservation(reservation));
      actionsCell.appendChild(edit);
    }

    (HOTEL_STATUS_TRANSITIONS[reservation.status] || []).forEach(([nextStatus, label]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ghost-button";
      button.textContent = label;
      button.addEventListener("click", async () => {
        showHotelError("");
        try {
          await api(
            `/api/departments/${dom.hotelDepartment.value}/hotel/reservations/${reservation.id}/status`,
            { method: "PUT", body: JSON.stringify({ status: nextStatus }) },
          );
          if (editingReservationId === reservation.id) {
            cancelEditingReservation();
          }
          await loadHotelReservations();
          await loadHotelAvailability();
          await loadHotelReport();
          setStatus(`Reserva: ${HOTEL_STATUS_LABELS[nextStatus] || nextStatus}.`);
        } catch (error) {
          showHotelError(error.message);
        }
      });
      actionsCell.appendChild(button);
    });

    // El teléfono viene del huésped o del contacto que escribió por el
    // canal; sin él no hay a quién mandarle la plantilla de confirmación.
    if (reservation.guest_phone) {
      const confirmButton = document.createElement("button");
      confirmButton.type = "button";
      confirmButton.className = "ghost-button";
      confirmButton.textContent = "Confirmar por WhatsApp";
      confirmButton.addEventListener("click", () => {
        dom.hotelPanel.hidden = true;
        dom.startTo.value = reservation.guest_phone;
        openStartPanel();
      });
      actionsCell.appendChild(confirmButton);
    }

    row.appendChild(actionsCell);
    return row;
  }

  async function startEditingReservation(reservation) {
    editingReservationId = reservation.id;
    dom.hotelCheckIn.value = reservation.check_in;
    dom.hotelCheckOut.value = reservation.check_out;
    dom.hotelGuestName.value = reservation.guest_name;
    dom.hotelGuestPhone.value = reservation.guest_phone || "";
    dom.hotelGuestEmail.value = reservation.guest_email || "";
    dom.hotelGuestCount.value = reservation.guests;
    dom.hotelReservationNotes.value = reservation.notes || "";
    // Los datos del huésped ya vienen de la reserva; a diferencia de
    // setHotelContactLink (pensada para el buscador), aquí no hay que
    // completar nombre/teléfono/correo con los del contacto.
    hotelSelectedContactId = reservation.contact_id || null;
    dom.hotelContactLinked.hidden = !reservation.contact_id;
    dom.hotelContactLinked.textContent = reservation.contact_id
      ? "Vinculado al contacto de esta reserva."
      : "";
    dom.hotelReservationFormTitle.textContent = "Editar reserva";
    dom.hotelReservationSubmit.textContent = "Guardar cambios";
    dom.hotelReservationCancelEdit.hidden = false;
    await loadHotelAvailability();
    // Sin esto, el navegador selecciona la primera opción de la lista —que
    // solo coincide con la habitación actual por casualidad del orden
    // alfabético—, no la que esta reserva de verdad tiene asignada.
    dom.hotelReservationRoom.value = reservation.room_id;
    dom.hotelReservationForm.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function cancelEditingReservation() {
    editingReservationId = null;
    dom.hotelReservationForm.reset();
    dom.hotelGuestCount.value = 1;
    setHotelContactLink(null);
    dom.hotelReservationFormTitle.textContent = "Nueva reserva";
    dom.hotelReservationSubmit.textContent = "Crear reserva";
    dom.hotelReservationCancelEdit.hidden = true;
    loadHotelAvailability();
  }

  dom.hotelReservationCancelEdit.addEventListener("click", cancelEditingReservation);

  // Devuelve si pudo cargar, en vez de dejarlo solo en el aviso de error: el
  // selector de departamento lo usa para decidir si mostrar el resto del
  // panel, y una excepción tragada aquí se lo ocultaría.
  async function loadHotelReservations() {
    const departmentId = dom.hotelDepartment.value;
    const query = dom.hotelStatusFilter.value
      ? `?status=${encodeURIComponent(dom.hotelStatusFilter.value)}`
      : "";
    try {
      const reservations = await api(`/api/departments/${departmentId}/hotel/reservations${query}`);
      dom.hotelReservationsTable.textContent = "";
      reservations.forEach((reservation) =>
        dom.hotelReservationsTable.appendChild(buildHotelReservationRow(reservation)),
      );
      return true;
    } catch (error) {
      showHotelError(error.message);
      return false;
    }
  }

  dom.hotelButton.addEventListener("click", () => {
    showHotelError("");
    dom.hotelBody.hidden = true;
    dom.hotelDepartment.value = "";
    dom.hotelAvailabilityList.textContent = "";
    cancelEditingReservation();
    dom.hotelPanel.hidden = false;
  });

  dom.hotelClose.addEventListener("click", () => {
    dom.hotelPanel.hidden = true;
  });

  dom.hotelDepartment.addEventListener("change", async () => {
    showHotelError("");
    dom.hotelBody.hidden = true;
    cancelEditingReservation();
    const departmentId = dom.hotelDepartment.value;
    if (!departmentId) {
      return;
    }
    // 404 = sin acceso a ese departamento; 409 = el módulo no está activo
    // ahí. En cualquiera de los dos casos el resto del panel queda oculto:
    // loadHotelReservations ya dejó el aviso puesto.
    const ok = await loadHotelReservations();
    if (!ok) {
      return;
    }
    dom.hotelBody.hidden = false;
    await loadHotelReport();
    await loadHotelAvailability();
  });

  dom.hotelStatusFilter.addEventListener("change", loadHotelReservations);
  dom.hotelAvailabilityForm.addEventListener("submit", (event) => {
    event.preventDefault();
    loadHotelAvailability();
  });

  dom.hotelContactSearch.addEventListener("input", () => {
    clearTimeout(hotelContactSearchTimer);
    const term = dom.hotelContactSearch.value.trim();
    hotelContactSearchTimer = setTimeout(() => searchHotelContacts(term), 300);
  });

  dom.hotelReservationForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showHotelError("");
    if (!dom.hotelReservationRoom.value) {
      showHotelError("Busque disponibilidad y elija una habitación primero.");
      return;
    }
    const body = {
      room_id: dom.hotelReservationRoom.value,
      guest_name: dom.hotelGuestName.value.trim(),
      guest_phone: dom.hotelGuestPhone.value.trim() || null,
      guest_email: dom.hotelGuestEmail.value.trim() || null,
      contact_id: hotelSelectedContactId,
      check_in: dom.hotelCheckIn.value,
      check_out: dom.hotelCheckOut.value,
      guests: Number(dom.hotelGuestCount.value) || 1,
      notes: dom.hotelReservationNotes.value.trim() || null,
    };
    try {
      if (editingReservationId) {
        await api(
          `/api/departments/${dom.hotelDepartment.value}/hotel/reservations/${editingReservationId}`,
          { method: "PATCH", body: JSON.stringify(body) },
        );
        cancelEditingReservation();
        await loadHotelReservations();
        await loadHotelAvailability();
        await loadHotelReport();
        setStatus("Reserva actualizada.");
      } else {
        await api(`/api/departments/${dom.hotelDepartment.value}/hotel/reservations`, {
          method: "POST",
          body: JSON.stringify(body),
        });
        dom.hotelReservationForm.reset();
        dom.hotelGuestCount.value = 1;
        setHotelContactLink(null);
        await loadHotelReservations();
        await loadHotelAvailability();
        await loadHotelReport();
        setStatus("Reserva creada.");
      }
    } catch (error) {
      showHotelError(error.message);
    }
  });

  /* ------------------------------------- apariencia: color de la marca */

  /* Ocho colores de arranque. No son una paleta cerrada —al lado hay un
     selector libre y un campo hexadecimal— sino un atajo: la mayoria de las
     marcas cae cerca de alguno, y elegir de una lista corta es mas rapido y
     da mejor resultado que buscar a pulso dentro de un cuadrado de color. */
  const BRAND_PRESETS = [
    "#2f5bd7", "#0b7285", "#1f9254", "#b7791f",
    "#c0392b", "#d6336c", "#7048e8", "#495057",
  ];

  //: Color guardado en el servidor. `null` = ninguno, y rige la paleta de
  //: partida de la hoja de estilos.
  let brandSaved = null;

  /* «Volver al color de partida» solo tiene sentido si hay algo que deshacer.
     Deshabilitado dice, sin gastar una linea de texto, que la consola ya esta
     con los colores originales. */
  function reflectBrandSaved() {
    dom.brandReset.disabled = brandSaved === null;
  }

  function renderBrandPresets() {
    dom.brandPresets.textContent = "";
    BRAND_PRESETS.forEach((color) => {
      const button = document.createElement("button");
      button.type = "button";
      button.style.background = color;
      button.dataset.color = color;
      button.title = color;
      button.setAttribute("aria-label", color);
      button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", () => setBrandChoice(color));
      dom.brandPresets.appendChild(button);
    });
  }

  function markBrandPreset(color) {
    [...dom.brandPresets.children].forEach((button) =>
      button.setAttribute("aria-pressed", String(button.dataset.color === color)),
    );
  }

  /* Pinta la muestra con los colores que devolvio el servidor. El calculo de
     contraste no se repite aqui a proposito: vive entero en
     app/core/branding.py, y una segunda copia en JavaScript acabaria
     desviandose de la primera sin que nadie lo notara. */
  function paintBrandPreview(palette) {
    ["light", "dark"].forEach((tema) => {
      const card = dom.brandPreview.querySelector(`[data-brand-preview="${tema}"]`);
      const colores = palette ? palette[tema] : null;
      const bubble = card.querySelector(".brand-preview__bubble");
      const button = card.querySelector(".brand-preview__button");
      const link = card.querySelector(".brand-preview__link");
      if (!colores) {
        [bubble, button, link].forEach((el) => el.removeAttribute("style"));
        return;
      }
      bubble.style.background = colores.outbound;
      bubble.style.color = colores.outbound_text;
      button.style.background = colores.accent;
      button.style.color = colores.accent_contrast;
      link.style.color = colores.accent_ink;
    });
  }

  /* Aplica el color a la consola entera, sustituyendo el bloque que el
     servidor incrusto en la cabecera. Se reemplaza ese mismo bloque y no se
     añade otro nuevo: repintar tiene que poder deshacerse, y con un `<style>`
     por cada cambio quedaria el ultimo encima para siempre. */
  function applyBrandCss(css) {
    dom.brandStyle.textContent = css || "";
  }

  let brandPreviewTimer = null;

  /* Deja el color elegido en los tres sitios que lo muestran y pide la
     muestra. La peticion se retrasa un poco porque arrastrar el selector de
     color dispara un evento por cada pixel recorrido. */
  function setBrandChoice(color, { preview = true } = {}) {
    dom.brandColor.value = color;
    dom.brandHex.value = color;
    markBrandPreset(color);
    if (!preview) {
      return;
    }
    clearTimeout(brandPreviewTimer);
    brandPreviewTimer = setTimeout(async () => {
      try {
        const data = await api(`/api/admin/branding/preview?accent=${encodeURIComponent(color)}`);
        paintBrandPreview(data.palette);
      } catch (error) {
        showAdminError(error.message);
      }
    }, 150);
  }

  async function loadBranding() {
    try {
      const data = await api("/api/admin/branding");
      brandSaved = data.accent;
      reflectBrandSaved();
      // Sin color propio no llega paleta que pintar, y la muestra saldria
      // vacia; se pide entonces la del color que aparece elegido.
      setBrandChoice(data.accent || BRAND_PRESETS[0], { preview: !data.accent });
      if (data.accent) {
        paintBrandPreview(data.palette);
      }
    } catch (error) {
      showAdminError(error.message);
    }
  }

  dom.brandColor.addEventListener("input", () => setBrandChoice(dom.brandColor.value));

  dom.brandHex.addEventListener("change", () => {
    const escrito = dom.brandHex.value.trim();
    if (/^#[0-9a-fA-F]{6}$/.test(escrito)) {
      setBrandChoice(escrito.toLowerCase());
    } else {
      // Se devuelve al ultimo valor bueno en vez de dejar el campo en rojo:
      // aqui no hay nada que corregir, solo un color que no se entendio.
      setBrandChoice(dom.brandColor.value, { preview: false });
      showAdminError(i18n.t("brand.badHex"));
    }
  });

  dom.brandSave.addEventListener("click", async () => {
    showAdminError("");
    try {
      const data = await api("/api/admin/branding", {
        method: "PUT",
        body: JSON.stringify({ accent: dom.brandColor.value }),
      });
      brandSaved = data.accent;
      reflectBrandSaved();
      applyBrandCss(data.css);
      paintBrandPreview(data.palette);
      setStatus(i18n.t("brand.saved"));
    } catch (error) {
      showAdminError(error.message);
    }
  });

  dom.brandReset.addEventListener("click", async () => {
    showAdminError("");
    try {
      const data = await api("/api/admin/branding", {
        method: "PUT",
        body: JSON.stringify({ accent: null }),
      });
      brandSaved = null;
      reflectBrandSaved();
      applyBrandCss(data.css);
      setBrandChoice(BRAND_PRESETS[0]);
      setStatus(i18n.t("brand.resetDone"));
    } catch (error) {
      showAdminError(error.message);
    }
  });

  renderBrandPresets();
  reflectBrandSaved();

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

  function updateNewUserPasswordVisibility() {
    // Agente: contraseña elegida a mano. Supervisor y administrador: el
    // servidor la genera y la envía por correo de invitación (ver
    // create_agent en app/api/console.py); pedirla aquí sería redundante.
    const needsPassword = dom.newUserRole.value === "agent";
    dom.newUserPasswordField.hidden = !needsPassword;
    dom.newUserPassword.required = needsPassword;
    dom.newUserPasswordHint.hidden = needsPassword;
  }

  dom.newUserRole.addEventListener("change", updateNewUserPasswordVisibility);
  updateNewUserPasswordVisibility();

  dom.createUserForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");
    try {
      const role = dom.newUserRole.value;
      const data = await api("/api/agents", {
        method: "POST",
        body: JSON.stringify({
          email: dom.newUserEmail.value.trim(),
          display_name: dom.newUserName.value.trim() || null,
          role,
          department_id: dom.newUserDepartment.value || null,
          password: role === "agent" ? dom.newUserPassword.value : null,
        }),
      });
      dom.createUserForm.reset();
      updateNewUserPasswordVisibility();
      await loadAgents();
      if (data.temporary_password) {
        setStatus(
          data.invitation_email_sent
            ? `Usuario creado. Correo de invitación enviado a ${data.email}.`
            : `Usuario creado. No se pudo enviar el correo de invitación: ` +
                `contraseña temporal ${data.temporary_password} (comuníquela usted mismo).`
        );
      } else {
        setStatus("Usuario creado.");
      }
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

      if (payload.type === "hotel_reservation_created") {
        setStatus(
          `Reserva de hotel pendiente: ${payload.room} (${payload.check_in} → ${payload.check_out}).`,
        );
        // Si el panel de reservas está abierto en ese mismo departamento, se
        // refresca solo: sin esto, quien lo tenga abierto vería una reserva
        // de menos hasta cerrar y volver a entrar.
        if (!dom.hotelPanel.hidden && dom.hotelDepartment.value === payload.department_id) {
          await loadHotelReservations();
          await loadHotelReport();
        }
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

  /* Al cambiar de idioma, lo marcado en el HTML lo traduce `i18n` solo; aquí
     se rehace lo que dibuja esta hoja, que no lleva marcas. */
  document.addEventListener("languagechange", () => {
    buildHoursRows();
    refreshMacroStepFields();
    renderMacroDraft();
    renderMacroButtons();
    renderSavedViews();
    renderConversations();
    renderConversationLabelToggles();
    if (state.me) {
      dom.whoRole.textContent = i18n.t(
        state.me.is_supervisor ? "role.supervisor" : "role.agent",
      );
    }
    // Los desplegables se rellenan con el marcador ya traducido.
    loadDepartments();
    loadLabels();
  });

  async function start() {
    if (!(await loadIdentity())) {
      return;
    }
    i18n.mountSelector(dom.langSelect);
    theme.mountToggle(document.getElementById("theme-toggle"));
    buildHoursRows();
    await loadDepartments();
    await loadLabels();
    await loadCannedResponses();
    await loadMacros();
    await loadAgents();
    // Después de los departamentos y etiquetas: las vistas guardadas se
    // resaltan comparándose con los <select>, que ya deben estar poblados.
    await loadSavedViews();
    await loadConversations();
    // Administrar es lo excepcional; atender, lo de todos los días. El panel
    // ya no se abre solo —tapaba la bandeja al entrar— y espera detrás de su
    // botón. Sus datos sí se precargan, para que abrirlo sea inmediato.
    if (state.me.role === "admin") {
      await loadChannelAccounts();
    }
    connectInbox();
  }

  start();
})();
