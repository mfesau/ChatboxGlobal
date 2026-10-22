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
    departmentTabs: document.getElementById("department-tabs"),
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
    passwordChangePanel: document.getElementById("password-change-panel"),
    passwordChangeForm: document.getElementById("password-change-form"),
    passwordCurrent: document.getElementById("password-current"),
    passwordNew: document.getElementById("password-new"),
    passwordChangeError: document.getElementById("password-change-error"),
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
    newAccountCredentials: document.getElementById("new-account-credentials"),
    newAccountHint: document.getElementById("new-account-hint"),
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
    ruleForm: document.getElementById("rule-form"),
    ruleName: document.getElementById("rule-name"),
    rulePriority: document.getElementById("rule-priority"),
    ruleDepartment: document.getElementById("rule-department"),
    ruleKeywords: document.getElementById("rule-keywords"),
    ruleFirstOnly: document.getElementById("rule-first-only"),
    ruleReply: document.getElementById("rule-reply"),
    ruleLabel: document.getElementById("rule-label"),
    ruleRoute: document.getElementById("rule-route"),
    ruleStop: document.getElementById("rule-stop"),
    rulesTable: document.querySelector("#rules-table tbody"),
    csatDepartment: document.getElementById("csat-department"),
    csatSettings: document.getElementById("csat-settings"),
    csatEnabled: document.getElementById("csat-enabled"),
    csatQuestion: document.getElementById("csat-question"),
    csatSave: document.getElementById("csat-save"),
    reportsSince: document.getElementById("reports-since"),
    reportsUntil: document.getElementById("reports-until"),
    reportsDepartment: document.getElementById("reports-department"),
    reportsTiles: document.getElementById("reports-tiles"),
    reportsChart: document.getElementById("reports-chart"),
    reportsChannels: document.querySelector("#reports-channels tbody"),
    reportsAgents: document.querySelector("#reports-agents tbody"),
    knowledgeForm: document.getElementById("knowledge-form"),
    newLabelDepartment: document.getElementById("new-label-department"),
    newCannedDepartment: document.getElementById("new-canned-department"),
    newMacroDepartment: document.getElementById("new-macro-department"),
    systemOverall: document.getElementById("system-overall"),
    systemRefresh: document.getElementById("system-refresh"),
    systemGenerated: document.getElementById("system-generated"),
    systemCards: document.getElementById("system-cards"),
    systemChannels: document.querySelector("#system-channels tbody"),
    assistantForm: document.getElementById("assistant-form"),
    assistantProvider: document.getElementById("assistant-provider"),
    assistantModel: document.getElementById("assistant-model"),
    assistantModelList: document.getElementById("assistant-model-list"),
    assistantModelInfo: document.getElementById("assistant-model-info"),
    assistantEffort: document.getElementById("assistant-effort"),
    assistantSave: document.getElementById("assistant-save"),
    assistantReset: document.getElementById("assistant-reset"),
    assistantDepartments: document.getElementById("assistant-departments"),
    knowledgeTitle: document.getElementById("knowledge-title"),
    knowledgeDepartment: document.getElementById("knowledge-department"),
    knowledgeText: document.getElementById("knowledge-text"),
    knowledgeFile: document.getElementById("knowledge-file"),
    knowledgeTable: document.querySelector("#knowledge-table tbody"),
    auditActor: document.getElementById("audit-actor"),
    auditAction: document.getElementById("audit-action"),
    auditSince: document.getElementById("audit-since"),
    auditTable: document.querySelector("#audit-table tbody"),
    auditMore: document.getElementById("audit-more"),
    profileExport: document.getElementById("profile-export"),
    profileErase: document.getElementById("profile-erase"),
    profilePrivacyError: document.getElementById("profile-privacy-error"),
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

  // Facebook faltaba aquí desde que existe el adaptador: sus hilos salían en
  // la lista con el nombre interno del canal.
  const CHANNEL_LABELS = {
    whatsapp: "WhatsApp",
    facebook: "Facebook",
    msbot: "Microsoft",
    telegram: "Telegram",
    email: "Correo",
    sms: "SMS",
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

  function showPasswordChange() {
    // El servidor responde 403 a todo lo demás mientras la marca siga puesta
    // (ver resolve_principal): sin este diálogo, la consola se vería vacía y
    // sin explicación.
    dom.passwordChangePanel.hidden = false;
    dom.passwordCurrent.focus();
  }

  dom.passwordChangeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    dom.passwordChangeError.hidden = true;
    try {
      await api("/api/auth/password", {
        method: "POST",
        body: JSON.stringify({
          current_password: dom.passwordCurrent.value,
          new_password: dom.passwordNew.value,
        }),
      });
    } catch (error) {
      dom.passwordChangeError.textContent = error.message;
      dom.passwordChangeError.hidden = false;
      return;
    }
    // Recargar es lo más simple y lo más seguro: la consola arranca de nuevo
    // con la sesión ya desbloqueada y sin estado a medio construir.
    window.location.reload();
  });

  async function loadIdentity() {
    try {
      const me = await api("/api/auth/me");
      state.me = me;
      if (me.agent && me.agent.must_change_password) {
        showPasswordChange();
        return false;
      }
      const label = me.agent ? me.agent.display_name || me.agent.email : "Acceso de servicio";
      dom.who.textContent = label;
      dom.whoRole.textContent = i18n.t(me.is_supervisor ? "role.supervisor" : "role.agent");
      dom.tabAll.hidden = !me.is_supervisor;
      dom.supervisorButton.hidden = !me.is_supervisor;
      dom.contactsButton.hidden = !me.is_supervisor;
      dom.adminButton.hidden = me.role !== "admin";
      // Escribir primero queda reservado a administracion: abordar a alguien
      // que no escribio antes gasta una plantilla aprobada y es un contacto
      // que el cliente no pidio. El servidor lo exige igual (ver
      // start_conversation), no solo se oculta el boton. Si la instalacion no
      // tiene WhatsApp, ademas se retira al primer intento (ver
      // openStartPanel).
      dom.startButton.hidden = me.role !== "admin";
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
      // Los contadores siguen a la pestaña abierta: la cola común de un
      // departamento es la suya, no la del inquilino entero.
      const params = new URLSearchParams();
      if (dom.departmentFilter.value) {
        params.set("department", dom.departmentFilter.value);
        params.set("include_unrouted", "true");
      }
      const query = params.toString();
      const summary = await api(`/api/inbox/summary${query ? `?${query}` : ""}`);
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
      // Lo que aún no tiene departamento también se ve: si no, lo que entra
      // sin clasificar solo lo vería administración.
      params.set("department", dom.departmentFilter.value);
      params.set("include_unrouted", "true");
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
    renderMacroButtons();
    await Promise.all([loadMessages(), loadNotes(), loadAssignments(), loadContact()]);
  }

  /** ¿Sirve este recurso en esa conversación? Lo de toda la empresa, siempre;
      lo de un departamento, solo en sus conversaciones. */
  function fitsConversation(row, conversation) {
    return !row.department_id || (conversation && row.department_id === conversation.department_id);
  }

  /** « · Hotel» detrás del nombre, para distinguir en las listas de
      administración lo que es de un departamento. */
  function departmentSuffix(row) {
    const department = state.departments.find((d) => d.id === row.department_id);
    return department ? ` · ${department.name}` : "";
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
    // Las que ya tiene se muestran siempre, para poder quitarlas aunque sean
    // de un departamento anterior a una derivación.
    state.labels
      .filter((label) => activeIds.has(label.id) || fitsConversation(label, conversation))
      .forEach((label) => {
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
        fitsConversation(canned, state.selected)
        && (canned.shortcode.toLowerCase().includes(query)
          || canned.title.toLowerCase().includes(query)),
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

  dom.departmentFilter.addEventListener("change", () => {
    rememberWorkDepartment();
    fillLabelFilter();
  });

  FILTERS.forEach((element) => {
    reflectFilterTitle(element);
    element.addEventListener("change", async () => {
      reflectFilterTitle(element);
      syncDepartmentTabs();
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
    // Una vista de otro departamento —o de «todos», para quien no puede
    // verlos— no cambia el departamento de trabajo.
    if ([...dom.departmentFilter.options].some((o) => o.value === (department || ""))) {
      dom.departmentFilter.value = department || "";
      rememberWorkDepartment();
      fillLabelFilter();
    }
    dom.labelFilter.value = label || "";
    syncDepartmentTabs();
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

    const fillWithPlaceholder = (select, placeholder, options = state.departments) => {
      select.textContent = "";
      const option = document.createElement("option");
      option.value = "";
      option.textContent = placeholder;
      select.appendChild(option);
      options.forEach((department) => {
        const departmentOption = document.createElement("option");
        departmentOption.value = department.id;
        departmentOption.textContent = department.name;
        select.appendChild(departmentOption);
      });
    };

    // El filtro de la bandeja solo ofrece los departamentos que quien
    // pregunta puede atender (``department_ids`` nulo = sin acotación, la
    // administración). El resto de los desplegables siguen con la lista
    // completa: derivar, dar de alta o configurar no exige atenderlo uno
    // mismo.
    const attendable = state.me?.department_ids
      ? state.departments.filter((department) => state.me.department_ids.includes(department.id))
      : state.departments;

    fillWorkDepartment(attendable);
    renderDepartmentTabs(attendable);
    fillWithPlaceholder(dom.transferDepartment, i18n.t("aside.choose"));
    fillWithPlaceholder(dom.newUserDepartment, i18n.t("admin.noDepartment"));
    fillWithPlaceholder(dom.newAccountDepartment, i18n.t("admin.noDepartment"));
    fillWithPlaceholder(dom.hotelAdminDepartment, i18n.t("aside.choose"));
    fillWithPlaceholder(dom.hotelDepartment, i18n.t("aside.choose"));
    fillCsatDepartments();
    for (const select of [
      dom.newLabelDepartment,
      dom.newCannedDepartment,
      dom.newMacroDepartment,
    ]) {
      const elegido = select.value;
      fillWithPlaceholder(select, i18n.t("knowledge.wholeCompany"));
      select.value = elegido;
    }

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
        dom.departmentList.appendChild(buildDepartmentRow(department));
      });
    }
  }

  /* El logo lo pone solo administración: el resto del equipo lo ve puesto
     (en la pestaña de su departamento), pero no tiene el control aquí ni el
     servidor le acepta la subida si lo forzara a mano. */
  function buildDepartmentRow(department) {
    const item = document.createElement("li");
    item.className = "department-row";

    if (department.logo_url) {
      const img = document.createElement("img");
      img.className = "department-row__logo";
      img.src = department.logo_url;
      img.alt = "";
      item.appendChild(img);
    }

    const name = document.createElement("span");
    name.className = "department-row__name";
    name.textContent = department.name;
    item.appendChild(name);

    if (state.me?.role === "admin") {
      const picker = document.createElement("label");
      picker.className = "ghost-button department-row__logo-picker";
      picker.textContent = i18n.t("admin.departmentLogo");
      const input = document.createElement("input");
      input.type = "file";
      input.accept = "image/png,image/jpeg,image/webp,image/gif";
      input.hidden = true;
      input.addEventListener("change", async () => {
        const file = input.files?.[0];
        if (file) {
          await uploadDepartmentLogo(department.id, file);
        }
        input.value = "";
      });
      picker.appendChild(input);
      item.appendChild(picker);

      if (department.logo_url) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "ghost-button";
        remove.textContent = i18n.t("admin.departmentLogoRemove");
        remove.addEventListener("click", () => removeDepartmentLogo(department.id));
        item.appendChild(remove);
      }
    }

    return item;
  }

  async function uploadDepartmentLogo(departmentId, file) {
    showAdminError("");
    const form = new FormData();
    form.append("file", file);
    try {
      await api(`/api/departments/${departmentId}/logo`, {
        method: "PUT",
        headers: {},
        body: form,
      });
      await loadDepartments();
      setStatus(i18n.t("admin.departmentLogoUpdated"));
    } catch (error) {
      showAdminError(error.message);
    }
  }

  async function removeDepartmentLogo(departmentId) {
    showAdminError("");
    try {
      await api(`/api/departments/${departmentId}/logo`, { method: "DELETE" });
      await loadDepartments();
      setStatus(i18n.t("admin.departmentLogoUpdated"));
    } catch (error) {
      showAdminError(error.message);
    }
  }

  /* -------------------------------------------------- pestañas de departamento */

  /* Cada pestaña es el puesto de trabajo de un departamento: al abrirla, la
     bandeja —cola común incluida— queda acotada a él, y a la derecha aparecen
     las acciones del módulo que ese departamento tenga activo. Por eso el
     botón de reservas vive aquí y no suelto en la barra lateral: solo tiene
     sentido dentro del departamento que lleva hotel. */
  function syncDepartmentTabs() {
    const active = dom.departmentFilter.value;
    const department = (state.departments || []).find((row) => row.id === active);
    if (dom.workDepartmentSelect) {
      dom.workDepartmentSelect.value = active;
      dom.workDepartmentSelect.title =
        dom.workDepartmentSelect.options[dom.workDepartmentSelect.selectedIndex]?.textContent || "";
    }
    if (dom.workDepartmentLogo) {
      dom.workDepartmentLogo.hidden = !department?.logo_url;
      if (department?.logo_url) {
        dom.workDepartmentLogo.src = department.logo_url;
      }
    }
    // Puede correr antes de que las pestañas existan (un filtro que cambia
    // durante el arranque): sin botón todavía, no hay nada que ajustar.
    if (!dom.departmentHotelButton) {
      return;
    }
    dom.departmentHotelButton.hidden = !department?.hotel_enabled;
  }

  function openHotelPanelFor(departmentId) {
    showHotelError("");
    dom.hotelBody.hidden = true;
    dom.hotelAvailabilityList.textContent = "";
    cancelEditingReservation();
    dom.hotelPanel.hidden = false;
    // El departamento ya está elegido —es la pestaña abierta—; se avisa al
    // desplegable del panel para que cargue sus datos por el camino de
    // siempre, en vez de repetir aquí esa carga.
    dom.hotelDepartment.value = departmentId;
    dom.hotelDepartment.dispatchEvent(new Event("change"));
  }

  /* ------------------------------------------- departamento de trabajo */

  /* El departamento de la barra superior es aquel en el que la persona está
     trabajando: la bandeja, los contadores, las etiquetas del filtro y los
     informes lo siguen. Cada agente elige solo entre los suyos; ver todos a
     la vez es cosa de administración. Se recuerda en el navegador, por
     persona, para no tener que elegirlo en cada visita. */

  function workDepartmentKey() {
    return `chatbox.workDepartment.${state.me?.agent?.id || "anon"}`;
  }

  /** Administración no tiene acotación por departamento y puede ver todos. */
  function seesAllDepartments() {
    return !state.me?.department_ids;
  }

  let workDepartmentReady = false;

  function fillWorkDepartment(attendable) {
    const select = dom.departmentFilter;
    const previous = select.value;
    select.textContent = "";
    // Sin departamentos asignados solo queda la cola común: lo que todavía
    // no tiene departamento.
    if (seesAllDepartments() || attendable.length === 0) {
      const all = document.createElement("option");
      all.value = "";
      all.textContent = seesAllDepartments()
        ? i18n.t("filter.department.all")
        : i18n.t("work.unrouted");
      select.appendChild(all);
    }
    attendable.forEach((department) => {
      const option = document.createElement("option");
      option.value = department.id;
      option.textContent = department.name;
      select.appendChild(option);
    });

    let saved = null;
    try {
      saved = window.localStorage.getItem(workDepartmentKey());
    } catch {
      saved = null;
    }
    const allowed = [...select.options].map((option) => option.value);
    // Al entrar manda lo recordado; después, lo que ya estaba elegido.
    const candidates = workDepartmentReady ? [previous, saved] : [saved, previous];
    select.value = candidates.find((value) => value !== null && allowed.includes(value))
      ?? allowed[0];
    workDepartmentReady = true;
    reflectFilterTitle(select);
  }

  function rememberWorkDepartment() {
    try {
      window.localStorage.setItem(workDepartmentKey(), dom.departmentFilter.value);
    } catch {
      // Sin almacenamiento (ventana privada): se elige de nuevo en cada visita.
    }
  }

  function renderDepartmentTabs(options) {
    dom.departmentTabs.textContent = "";

    // Rótulo fijo: deja claro en qué departamento se está trabajando, también
    // a quien tiene uno solo y no tiene nada que elegir.
    const rotulo = document.createElement("span");
    rotulo.className = "department-tabs__label";
    rotulo.textContent = i18n.t("work.label");
    dom.departmentTabs.appendChild(rotulo);

    // Un desplegable y no una pestaña por departamento: con muchos áreas las
    // pestañas no caben, y un campo deja claro que se elige uno solo. Copia
    // las opciones del filtro oculto, que sigue siendo la fuente de verdad.
    const logo = document.createElement("img");
    logo.className = "department-tab__logo";
    logo.alt = "";
    logo.hidden = true;
    dom.departmentTabs.appendChild(logo);
    dom.workDepartmentLogo = logo;

    const select = document.createElement("select");
    select.className = "work-department";
    select.id = "work-department";
    select.setAttribute("aria-label", i18n.t("work.label"));
    for (const option of dom.departmentFilter.options) {
      select.appendChild(new Option(option.textContent, option.value));
    }
    // Con una sola opción no hay nada que elegir, pero se sigue viendo dónde
    // se trabaja.
    select.disabled = select.options.length <= 1;
    select.addEventListener("change", () => {
      if (dom.departmentFilter.value === select.value) {
        return;
      }
      dom.departmentFilter.value = select.value;
      dom.departmentFilter.dispatchEvent(new Event("change"));
    });
    dom.departmentTabs.appendChild(select);
    dom.workDepartmentSelect = select;

    const hotel = document.createElement("button");
    hotel.type = "button";
    hotel.className = "department-tabs__action";
    hotel.id = "department-hotel-button";
    hotel.textContent = i18n.t("hotel.button");
    hotel.hidden = true;
    hotel.addEventListener("click", () => openHotelPanelFor(dom.departmentFilter.value));
    dom.departmentTabs.appendChild(hotel);
    dom.departmentHotelButton = hotel;

    syncDepartmentTabs();
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
    const departamento = dom.newMacroDepartment.value || null;
    (state[spec.origen] || [])
      .filter(
        (row) =>
          spec.origen === "departments" || !row.department_id || row.department_id === departamento,
      )
      .forEach((row) => {
        const option = document.createElement("option");
        option.value = row.id;
        option.textContent = row.name || `/${row.shortcode} — ${row.title}`;
        dom.macroStepTarget.appendChild(option);
      });
  }

  dom.newMacroDepartment.addEventListener("change", refreshMacroStepFields);

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
        body: JSON.stringify({
          name,
          steps: state.macroDraft,
          department_id: dom.newMacroDepartment.value || null,
        }),
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
        label.textContent = `${i18n.t("macro.stepCount", {
          name: macro.name,
          n: macro.steps.length,
        })}${departmentSuffix(macro)}`;
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
    const aplicables = state.macros.filter((macro) => fitsConversation(macro, state.selected));
    dom.macroPanel.hidden = aplicables.length === 0;
    aplicables.forEach((macro) => {
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

  /** El filtro de etiquetas ofrece las de toda la empresa y las del
      departamento de trabajo. */
  function fillLabelFilter() {
    const elegida = dom.labelFilter.value;
    const departamento = dom.departmentFilter.value;
    dom.labelFilter.textContent = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = i18n.t("filter.label.all");
    dom.labelFilter.appendChild(placeholder);
    state.labels
      .filter((label) => !departamento || !label.department_id || label.department_id === departamento)
      .forEach((label) => {
        const option = document.createElement("option");
        option.value = label.id;
        option.textContent = label.name;
        dom.labelFilter.appendChild(option);
      });
    // Si la etiqueta elegida no es de este departamento, se quita el filtro.
    dom.labelFilter.value = [...dom.labelFilter.options].some((o) => o.value === elegida)
      ? elegida
      : "";
    reflectFilterTitle(dom.labelFilter);
  }

  async function loadLabels() {
    try {
      state.labels = await api("/api/labels");
    } catch {
      state.labels = [];
    }

    fillLabelFilter();

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
        swatch.textContent = `${label.name}${departmentSuffix(label)}`;
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
        body: JSON.stringify({
          name,
          color: dom.newLabelColor.value,
          department_id: dom.newLabelDepartment.value || null,
        }),
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
        label.textContent = `/${canned.shortcode} — ${canned.title}${departmentSuffix(canned)}`;
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
        body: JSON.stringify({
          shortcode,
          title,
          body,
          department_id: dom.newCannedDepartment.value || null,
        }),
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

  const CHANNEL_ACCOUNT_LABELS = {
    whatsapp: "WhatsApp",
    facebook: "Facebook",
    msbot: "Teams",
    telegram: "Telegram",
    email: "Correo",
    sms: "SMS (Twilio)",
  };

  /* Credenciales que pide cada canal. Es el reflejo de CHANNEL_CREDENTIAL_FIELDS
     en app/api/console.py: si allí se añade un canal, aquí va su entrada y el
     formulario se arma solo. `secret` decide si el campo se escribe a ciegas. */
  const CHANNEL_CREDENTIAL_FIELDS = {
    whatsapp: [
      { name: "access_token", secret: true },
      { name: "verify_token", secret: true },
      { name: "app_secret", secret: true },
    ],
    facebook: [
      { name: "access_token", secret: true },
      { name: "verify_token", secret: true },
      { name: "app_secret", secret: true },
    ],
    msbot: [],
    telegram: [
      { name: "access_token", secret: true },
      { name: "verify_token", secret: true },
    ],
    sms: [
      { name: "account_sid", secret: false },
      { name: "auth_token", secret: true },
      { name: "from_number", secret: false },
    ],
    email: [
      { name: "imap_host", secret: false },
      { name: "imap_port", secret: false },
      { name: "imap_username", secret: false },
      { name: "imap_password", secret: true },
      { name: "imap_folder", secret: false },
      { name: "smtp_host", secret: false },
      { name: "smtp_port", secret: false },
      { name: "smtp_username", secret: false },
      { name: "smtp_password", secret: true },
      { name: "from_email", secret: false },
    ],
  };

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

    // Antes esta columna era un único campo que siempre mandaba `access_token`.
    // Con canales de diez credenciales eso no vale: se elige cuál se cambia.
    const tokenCell = document.createElement("td");
    const fields = CHANNEL_CREDENTIAL_FIELDS[account.channel] || [];
    const fieldSelect = document.createElement("select");
    fields.forEach((field) => {
      const option = document.createElement("option");
      option.value = field.name;
      option.textContent = i18n.t(`accounts.field.${field.name}`);
      fieldSelect.appendChild(option);
    });
    fieldSelect.setAttribute("aria-label", `Credencial de ${account.external_id}`);
    const tokenInput = document.createElement("input");
    tokenInput.type = "password";
    tokenInput.maxLength = 4096;
    tokenInput.autocomplete = "off";
    tokenInput.placeholder = account.has_own_credentials ? "Reemplazar" : "Sin valor propio";
    tokenInput.setAttribute("aria-label", `Nuevo valor de ${account.external_id}`);
    if (fields.length === 0) {
      // Teams no guarda credenciales; el hueco se deja explicado, no vacío.
      tokenCell.textContent = i18n.t("accounts.noCredentials");
    } else {
      tokenCell.append(fieldSelect, tokenInput);
    }
    row.appendChild(tokenCell);

    const statusCell = document.createElement("td");
    statusCell.textContent = account.is_active ? "Activa" : "Inactiva";
    row.appendChild(statusCell);

    const actionsCell = document.createElement("td");

    // Telegram no tiene panel de configuración: el webhook se da de alta
    // llamando a su propia API con el token dentro. Sin este botón habría que
    // salir de la consola y componer ese `curl` a mano.
    if (account.channel === "telegram") {
      const hookButton = document.createElement("button");
      hookButton.type = "button";
      hookButton.className = "ghost-button";
      hookButton.textContent = i18n.t("accounts.registerWebhook");
      hookButton.addEventListener("click", async () => {
        showAdminError("");
        hookButton.disabled = true;
        try {
          const result = await api(
            `/api/channel-accounts/${account.id}/telegram/webhook`,
            { method: "POST" },
          );
          setStatus(i18n.t("accounts.webhookRegistered", { url: result.url }));
        } catch (error) {
          showAdminError(error.message);
        } finally {
          hookButton.disabled = false;
        }
      });
      actionsCell.appendChild(hookButton);
    }

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
      if (tokenInput.value && fieldSelect.value) {
        body.credentials = { [fieldSelect.value]: tokenInput.value };
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

  /* Cada canal pide lo suyo y solo lo suyo. Teams no pide nada: su
     autenticación es un JWT firmado por Microsoft, y enseñar campos invitaría a
     rellenarlos con algo que no serviría para nada. */
  function reflectAccountChannel() {
    const channel = dom.newAccountChannel.value;
    dom.newAccountCredentials.replaceChildren();
    for (const field of CHANNEL_CREDENTIAL_FIELDS[channel] || []) {
      const label = document.createElement("label");
      label.className = "panel__field";
      const caption = document.createElement("span");
      caption.textContent = i18n.t(`accounts.field.${field.name}`);
      const input = document.createElement("input");
      input.type = field.secret ? "password" : "text";
      input.autocomplete = field.secret ? "new-password" : "off";
      input.maxLength = 4096;
      input.dataset.credential = field.name;
      label.append(caption, input);
      dom.newAccountCredentials.appendChild(label);
    }
    dom.newAccountHint.textContent = i18n.t(`accounts.idHint.${channel}`);
  }

  function collectAccountCredentials() {
    const credentials = {};
    for (const input of dom.newAccountCredentials.querySelectorAll("[data-credential]")) {
      if (input.value.trim()) {
        credentials[input.dataset.credential] = input.value.trim();
      }
    }
    return credentials;
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
          credentials: collectAccountCredentials(),
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
    if (cents == null) {
      return "—";
    }
    // El guaraní no tiene céntimos y se escribe con punto de miles: «Gs. 350.000».
    if (currency === "PYG") {
      const entero = String(Math.round(cents / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
      return `Gs. ${entero}`;
    }
    return `${(cents / 100).toFixed(2)} ${currency}`;
  }

  let hotelAdminRoomTypes = [];
  // Nulo = el formulario de tarifas crea una nueva; con id, corrige esta.
  let editingRatePlanId = null;

  function fillRoomTypeSelect(select) {
    select.textContent = "";
    hotelAdminRoomTypes.forEach((roomType) => {
      const option = document.createElement("option");
      option.value = roomType.id;
      option.textContent = roomType.is_active
        ? roomType.name
        : `${roomType.name} (${i18n.t("hotel.retired").toLowerCase()})`;
      select.appendChild(option);
    });
  }

  function buildRoomTypeRow(roomType) {
    const row = document.createElement("tr");
    [
      roomType.name,
      roomType.capacity,
      i18n.t(roomType.is_active ? "hotel.active" : "hotel.retired"),
    ].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    });
    const actionsCell = document.createElement("td");
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "ghost-button";
    toggle.textContent = i18n.t(roomType.is_active ? "hotel.withdraw" : "hotel.reactivate");
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
      ["available", i18n.t("hotel.roomStatus.available")],
      ["maintenance", i18n.t("hotel.roomStatus.maintenance")],
      ["out_of_service", i18n.t("hotel.roomStatus.outOfService")],
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
    save.textContent = i18n.t("common.save");
    save.addEventListener("click", async () => {
      showAdminError("");
      try {
        await api(`/api/departments/${dom.hotelAdminDepartment.value}/hotel/rooms/${room.id}`, {
          method: "PATCH",
          body: JSON.stringify({ status: statusSelect.value }),
        });
        await loadHotelRooms();
        setStatus(i18n.t("hotel.roomUpdated"));
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
        : i18n.t("hotel.allYear");
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
    edit.textContent = i18n.t("hotel.edit");
    edit.addEventListener("click", () => startEditingRatePlan(ratePlan));
    actionsCell.appendChild(edit);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "ghost-button ghost-button--danger";
    remove.textContent = i18n.t("admin.delete");
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
    dom.newRatePlanPrice.value = String(ratePlan.nightly_price_cents / 100);
    dom.newRatePlanCurrency.value = ratePlan.currency;
    dom.ratePlanFormTitle.textContent = i18n.t("hotel.admin.editRateTitle");
    dom.ratePlanSubmit.textContent = i18n.t("hotel.saveChanges");
    dom.ratePlanCancelEdit.hidden = false;
  }

  function cancelEditingRatePlan() {
    editingRatePlanId = null;
    dom.createRatePlanForm.reset();
    dom.newRatePlanTypeSelect.disabled = false;
    dom.newRatePlanCurrency.value = "PYG";
    dom.ratePlanFormTitle.textContent = i18n.t("hotel.admin.rates");
    dom.ratePlanSubmit.textContent = i18n.t("hotel.admin.createRate");
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
      setStatus(i18n.t(enabled ? "hotel.moduleOn" : "hotel.moduleOff"));
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
      setStatus(i18n.t("hotel.roomTypeCreated"));
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
      setStatus(i18n.t("hotel.roomCreated"));
    } catch (error) {
      showAdminError(error.message);
    }
  });

  dom.createRatePlanForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");
    const price = Number(dom.newRatePlanPrice.value);
    if (!price || price <= 0) {
      showAdminError(i18n.t("hotel.priceMustBePositive"));
      return;
    }
    const body = {
      name: dom.newRatePlanName.value.trim(),
      starts_on: dom.newRatePlanStarts.value || null,
      ends_on: dom.newRatePlanEnds.value || null,
      nightly_price_cents: Math.round(price * 100),
      currency: (dom.newRatePlanCurrency.value || "PYG").toUpperCase(),
    };
    try {
      if (editingRatePlanId) {
        await api(
          `/api/departments/${dom.hotelAdminDepartment.value}/hotel/rate-plans/${editingRatePlanId}`,
          { method: "PATCH", body: JSON.stringify(body) },
        );
        cancelEditingRatePlan();
        await loadHotelRatePlans();
        setStatus(i18n.t("hotel.rateUpdated"));
      } else {
        await api(`/api/departments/${dom.hotelAdminDepartment.value}/hotel/rate-plans`, {
          method: "POST",
          body: JSON.stringify({ ...body, room_type_id: dom.newRatePlanTypeSelect.value }),
        });
        dom.createRatePlanForm.reset();
        dom.newRatePlanCurrency.value = "PYG";
        await loadHotelRatePlans();
        setStatus(i18n.t("hotel.rateCreated"));
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
      await loadReports();
    } catch (error) {
      setStatus(error.message);
    }
  });

  dom.supervisorClose.addEventListener("click", () => {
    dom.supervisorPanel.hidden = true;
  });

  /* ------------------------------------------------------------- informes */

  function reportsQuery() {
    const params = new URLSearchParams();
    if (dom.reportsSince.value) {
      params.set("since", dom.reportsSince.value);
    }
    if (dom.reportsUntil.value) {
      params.set("until", dom.reportsUntil.value);
    }
    if (dom.reportsDepartment.value) {
      params.set("department_id", dom.reportsDepartment.value);
    }
    return params.toString();
  }

  function formatDuration(seconds) {
    if (seconds == null) {
      return "—";
    }
    if (seconds < 60) {
      return `${Math.round(seconds)} s`;
    }
    if (seconds < 3600) {
      return `${Math.round(seconds / 60)} min`;
    }
    return `${(seconds / 3600).toFixed(1)} h`;
  }

  function renderTiles(report) {
    const cifras = [
      [i18n.t("reports.conversations"), String(report.summary.conversations)],
      [i18n.t("reports.closed"), String(report.summary.closed)],
      [
        i18n.t("reports.firstResponse"),
        formatDuration(report.summary.first_response_seconds_p50),
      ],
      [i18n.t("reports.p90"), formatDuration(report.summary.first_response_seconds_p90)],
      [
        i18n.t("reports.compliance"),
        report.summary.sla_compliance == null
          ? "—"
          : `${Math.round(report.summary.sla_compliance * 100)} %`,
      ],
      [i18n.t("reports.aiRuns"), String(report.ai.runs)],
    ];

    dom.reportsTiles.replaceChildren();
    for (const [etiqueta, valor] of cifras) {
      const dt = document.createElement("dt");
      dt.textContent = etiqueta;
      const dd = document.createElement("dd");
      dd.textContent = valor;
      dom.reportsTiles.append(dt, dd);
    }
  }

  /* Gráfica en SVG escrito a mano: esta aplicación no carga nada de fuera —no
     hay CDN— y la política de contenido tampoco lo permitiría. Una serie de
     barras no justifica empaquetar una biblioteca entera. */
  function renderChart(series) {
    dom.reportsChart.replaceChildren();
    if (series.length === 0) {
      return;
    }
    const maximo = Math.max(...series.map((fila) => fila.conversations), 1);
    const ancho = Math.max(series.length * 12, 240);
    const alto = 120;

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${ancho} ${alto}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", i18n.t("reports.chartLabel"));
    svg.classList.add("reports-chart__svg");

    series.forEach((fila, indice) => {
      const altura = (fila.conversations / maximo) * (alto - 16);
      const barra = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      barra.setAttribute("x", String(indice * 12 + 2));
      barra.setAttribute("y", String(alto - altura));
      barra.setAttribute("width", "8");
      barra.setAttribute("height", String(Math.max(altura, 1)));
      barra.setAttribute("rx", "2");
      const titulo = document.createElementNS("http://www.w3.org/2000/svg", "title");
      titulo.textContent = `${fila.day}: ${fila.conversations}`;
      barra.appendChild(titulo);
      svg.appendChild(barra);
    });
    dom.reportsChart.appendChild(svg);
  }

  function renderRows(tbody, rows, columns) {
    tbody.replaceChildren();
    for (const row of rows) {
      const tr = document.createElement("tr");
      for (const value of columns(row)) {
        const td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
  }

  async function loadReports() {
    // El desplegable se llena con lo ya cargado; «todos» respeta el alcance
    // que el servidor aplique a quien pregunta.
    // Solo los departamentos que la persona atiende, y de entrada el de
    // trabajo: un informe de otro departamento el servidor no lo daría.
    const elegido = dom.reportsDepartment.value || dom.departmentFilter.value;
    dom.reportsDepartment.replaceChildren();
    const todos = document.createElement("option");
    todos.value = "";
    todos.textContent = i18n.t("reports.allDepartments");
    dom.reportsDepartment.appendChild(todos);
    const propios = seesAllDepartments()
      ? state.departments
      : state.departments.filter((d) => state.me.department_ids.includes(d.id));
    for (const department of propios) {
      const option = document.createElement("option");
      option.value = department.id;
      option.textContent = department.name;
      dom.reportsDepartment.appendChild(option);
    }
    dom.reportsDepartment.value = elegido;

    let report;
    try {
      const query = reportsQuery();
      report = await api(`/api/reports/summary${query ? `?${query}` : ""}`);
    } catch (error) {
      setStatus(error.message);
      return;
    }

    renderTiles(report);
    renderChart(report.timeseries);
    renderRows(dom.reportsChannels, report.channels, (row) => [
      CHANNEL_LABELS[row.channel] || row.channel,
      String(row.conversations),
    ]);
    renderRows(dom.reportsAgents, report.agents, (row) => [
      row.agent,
      String(row.conversations),
      String(row.closed),
      String(row.sla_breached),
      formatDuration(row.first_response_seconds_p50),
    ]);
  }

  for (const control of [dom.reportsSince, dom.reportsUntil, dom.reportsDepartment]) {
    control.addEventListener("change", () => loadReports());
  }

  for (const button of document.querySelectorAll("[data-report-export]")) {
    button.addEventListener("click", () => {
      const params = new URLSearchParams(reportsQuery());
      params.set("dataset", button.dataset.reportExport);
      // Descarga directa: la respuesta es un fichero, no JSON que interpretar.
      window.location.href = `/api/reports/export.csv?${params.toString()}`;
    });
  }

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
      // El servidor lo exige igual (AdminDep); ocultarlo evita ofrecer a
      // supervisión un botón que solo le daría un 403.
      dom.profileErase.hidden = state.me?.role !== "admin";
      // Exportar también es de administración: la copia lleva las
      // conversaciones de todos los departamentos.
      dom.profileExport.hidden = state.me?.role !== "admin";
      showPrivacyError("");
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

  /* ------------------------------------------ datos personales (RGPD) */

  function showPrivacyError(message) {
    dom.profilePrivacyError.textContent = message;
    dom.profilePrivacyError.hidden = !message;
  }

  dom.profileExport.addEventListener("click", async () => {
    if (!state.contactProfileId) {
      return;
    }
    showPrivacyError("");
    // Descarga directa y no `api()`: la respuesta es un fichero adjunto, no
    // JSON que la consola vaya a interpretar. La cookie de sesión viaja igual.
    window.location.href = `/api/contacts/${state.contactProfileId}/export`;
  });

  dom.profileErase.addEventListener("click", async () => {
    if (!state.contactProfileId) {
      return;
    }
    if (!window.confirm(i18n.t("privacy.confirm"))) {
      return;
    }
    showPrivacyError("");
    dom.profileErase.disabled = true;
    try {
      await api(`/api/contacts/${state.contactProfileId}/erase?confirm=true`, {
        method: "POST",
      });
      await openContactProfile(state.contactProfileId);
      await loadContacts(dom.contactsSearch.value.trim());
    } catch (error) {
      showPrivacyError(error.message);
    } finally {
      dom.profileErase.disabled = false;
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
    tab.addEventListener("click", () => {
      showAdminTab(tab.dataset.adminTab);
      // La traza puede ser larga; se pide al abrir su pestaña y no al abrir
      // el panel entero, que se usa sobre todo para otras cosas.
      if (tab.dataset.adminTab === "audit") {
        loadAudit({ reset: true });
      }
      if (tab.dataset.adminTab === "knowledge") {
        loadKnowledge();
      }
      if (tab.dataset.adminTab === "assistant") {
        loadAssistant();
      }
      if (tab.dataset.adminTab === "system") {
        loadSystem();
      }
      if (tab.dataset.adminTab === "rules") {
        loadRules();
      }
    }),
  );

  /* ---------------------------------------------------------- reglas */

  /* El formulario ofrece un subconjunto deliberado: las cuatro acciones que se
     repiten a mano en un buzón. La API admite listas de acciones más largas y
     expresiones regulares; quien las necesite las manda por ahí. Un
     constructor visual completo sería mucha interfaz para muy pocos casos. */
  function ruleActionsFromForm() {
    const actions = [];
    if (dom.ruleLabel.value) {
      actions.push({ action: "add_label", label_id: dom.ruleLabel.value });
    }
    if (dom.ruleRoute.value) {
      actions.push({ action: "assign_department", department_id: dom.ruleRoute.value });
    }
    if (dom.ruleReply.value.trim()) {
      actions.push({ action: "reply", body: dom.ruleReply.value.trim() });
    }
    if (dom.ruleStop.checked) {
      actions.push({ action: "stop" });
    }
    return actions;
  }

  function describeRule(rule) {
    const partes = [];
    for (const accion of rule.actions || []) {
      if (accion.action === "add_label") {
        const etiqueta = state.labels.find((row) => row.id === accion.label_id);
        partes.push(`${i18n.t("rules.label")}: ${etiqueta ? etiqueta.name : "—"}`);
      } else if (accion.action === "assign_department") {
        const department = state.departments.find((row) => row.id === accion.department_id);
        partes.push(`${i18n.t("rules.route")}: ${department ? department.name : "—"}`);
      } else if (accion.action === "reply") {
        partes.push(`${i18n.t("rules.reply")}: ${accion.body}`);
      } else if (accion.action === "stop") {
        partes.push(i18n.t("rules.stop"));
      }
    }
    return partes.join(" · ");
  }

  async function loadRules() {
    const fill = (select, placeholder, rows) => {
      const elegido = select.value;
      select.replaceChildren();
      const vacio = document.createElement("option");
      vacio.value = "";
      vacio.textContent = placeholder;
      select.appendChild(vacio);
      for (const row of rows) {
        const option = document.createElement("option");
        option.value = row.id;
        option.textContent = row.name;
        select.appendChild(option);
      }
      select.value = elegido;
    };
    fill(dom.ruleDepartment, i18n.t("knowledge.wholeCompany"), state.departments);
    fill(dom.ruleRoute, i18n.t("rules.none"), state.departments);
    fill(dom.ruleLabel, i18n.t("rules.none"), state.labels);

    let reglas;
    try {
      reglas = await api("/api/rules");
    } catch (error) {
      showAdminError(error.message);
      return;
    }

    dom.rulesTable.replaceChildren();
    for (const rule of reglas) {
      const row = document.createElement("tr");
      const department = state.departments.find((d) => d.id === rule.department_id);
      for (const value of [
        String(rule.priority),
        rule.name,
        department ? department.name : i18n.t("knowledge.wholeCompany"),
        describeRule(rule),
        rule.is_active ? i18n.t("rules.active") : i18n.t("rules.inactive"),
      ]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      }

      const actions = document.createElement("td");
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "ghost-button";
      toggle.textContent = rule.is_active ? i18n.t("rules.disable") : i18n.t("rules.enable");
      toggle.addEventListener("click", async () => {
        try {
          await api(`/api/rules/${rule.id}`, {
            method: "PATCH",
            body: JSON.stringify({ is_active: !rule.is_active }),
          });
          await loadRules();
        } catch (error) {
          showAdminError(error.message);
        }
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "ghost-button";
      remove.textContent = i18n.t("common.delete");
      remove.addEventListener("click", async () => {
        if (!window.confirm(i18n.t("rules.confirmDelete", { name: rule.name }))) {
          return;
        }
        try {
          await api(`/api/rules/${rule.id}`, { method: "DELETE" });
          await loadRules();
        } catch (error) {
          showAdminError(error.message);
        }
      });
      actions.append(toggle, remove);
      row.appendChild(actions);
      dom.rulesTable.appendChild(row);
    }
  }

  dom.ruleForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");

    const actions = ruleActionsFromForm();
    if (actions.length === 0) {
      showAdminError(i18n.t("rules.needAction"));
      return;
    }

    const keywords = dom.ruleKeywords.value
      .split(",")
      .map((palabra) => palabra.trim())
      .filter(Boolean);
    const conditions = {};
    if (keywords.length) {
      conditions.keywords = keywords;
    }
    if (dom.ruleFirstOnly.checked) {
      conditions.only_first_message = true;
    }

    try {
      await api("/api/rules", {
        method: "POST",
        body: JSON.stringify({
          name: dom.ruleName.value.trim(),
          priority: Number(dom.rulePriority.value) || 100,
          department_id: dom.ruleDepartment.value || null,
          conditions,
          actions,
        }),
      });
      dom.ruleForm.reset();
      dom.rulePriority.value = "100";
      await loadRules();
      setStatus(i18n.t("rules.created"));
    } catch (error) {
      showAdminError(error.message);
    }
  });

  /* ------------------------------------------ encuesta de satisfacción */

  function fillCsatDepartments() {
    const elegido = dom.csatDepartment.value;
    dom.csatDepartment.replaceChildren();
    const vacio = document.createElement("option");
    vacio.value = "";
    vacio.textContent = i18n.t("aside.choose");
    dom.csatDepartment.appendChild(vacio);
    for (const department of state.departments) {
      const option = document.createElement("option");
      option.value = department.id;
      option.textContent = department.name;
      dom.csatDepartment.appendChild(option);
    }
    dom.csatDepartment.value = elegido;
  }

  dom.csatDepartment.addEventListener("change", async () => {
    const department_id = dom.csatDepartment.value;
    dom.csatSettings.hidden = !department_id;
    if (!department_id) {
      return;
    }
    try {
      const estado = await api(`/api/departments/${department_id}/csat`);
      dom.csatEnabled.checked = estado.enabled;
      dom.csatQuestion.value = estado.question;
    } catch (error) {
      showAdminError(error.message);
    }
  });

  dom.csatSave.addEventListener("click", async () => {
    const department_id = dom.csatDepartment.value;
    if (!department_id) {
      return;
    }
    showAdminError("");
    dom.csatSave.disabled = true;
    try {
      await api(`/api/departments/${department_id}/csat`, {
        method: "PUT",
        body: JSON.stringify({
          enabled: dom.csatEnabled.checked,
          question: dom.csatQuestion.value.trim(),
        }),
      });
      setStatus(i18n.t("csat.saved"));
    } catch (error) {
      showAdminError(error.message);
    } finally {
      dom.csatSave.disabled = false;
    }
  });

  /* --------------------------------------------- base de conocimiento */

  async function loadKnowledge() {
    // El desplegable se rellena con los departamentos ya cargados; «toda la
    // empresa» queda como primera opción, que es el caso más común.
    dom.knowledgeDepartment.replaceChildren();
    const todos = document.createElement("option");
    todos.value = "";
    todos.textContent = i18n.t("knowledge.wholeCompany");
    dom.knowledgeDepartment.appendChild(todos);
    for (const department of state.departments) {
      const option = document.createElement("option");
      option.value = department.id;
      option.textContent = department.name;
      dom.knowledgeDepartment.appendChild(option);
    }

    let documents;
    try {
      documents = await api("/api/knowledge/documents");
    } catch (error) {
      showAdminError(error.message);
      return;
    }

    dom.knowledgeTable.replaceChildren();
    for (const document_ of documents) {
      const row = document.createElement("tr");
      const department = state.departments.find((d) => d.id === document_.department_id);
      // Trocear y vectorizar ocurre en segundo plano, así que un documento
      // recién subido aparece «procesando» hasta que termina.
      const estado =
        document_.status === "ready"
          ? i18n.t("knowledge.ready")
          : document_.status === "failed"
            ? `${i18n.t("knowledge.failed")}: ${document_.error || ""}`
            : i18n.t("knowledge.processing");

      for (const value of [
        document_.title,
        department ? department.name : i18n.t("knowledge.wholeCompany"),
        String(document_.chunk_count),
        estado,
      ]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      }

      const actions = document.createElement("td");
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "ghost-button";
      remove.textContent = i18n.t("common.delete");
      remove.addEventListener("click", async () => {
        if (!window.confirm(i18n.t("knowledge.confirmDelete", { title: document_.title }))) {
          return;
        }
        try {
          await api(`/api/knowledge/documents/${document_.id}`, { method: "DELETE" });
          await loadKnowledge();
        } catch (error) {
          showAdminError(error.message);
        }
      });
      actions.appendChild(remove);
      row.appendChild(actions);
      dom.knowledgeTable.appendChild(row);
    }
  }

  dom.knowledgeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showAdminError("");
    const title = dom.knowledgeTitle.value.trim();
    const text = dom.knowledgeText.value.trim();
    const file = dom.knowledgeFile.files[0];
    if (!file && !text) {
      showAdminError(i18n.t("knowledge.needContent"));
      return;
    }

    setStatus(i18n.t("knowledge.working"));
    try {
      if (file) {
        // Con fichero va como formulario: `api()` fija Content-Type a JSON y
        // aquí lo tiene que poner el navegador, con su separador de partes.
        const body = new FormData();
        body.append("file", file);
        if (title) {
          body.append("title", title);
        }
        if (dom.knowledgeDepartment.value) {
          body.append("department_id", dom.knowledgeDepartment.value);
        }
        const response = await fetch("/api/knowledge/documents/upload", {
          method: "POST",
          credentials: "same-origin",
          body,
        });
        if (!response.ok) {
          throw new Error(extractDetail(await response.text(), response.status));
        }
      } else {
        await api("/api/knowledge/documents", {
          method: "POST",
          body: JSON.stringify({
            title,
            text,
            department_id: dom.knowledgeDepartment.value || null,
          }),
        });
      }
      dom.knowledgeForm.reset();
      await loadKnowledge();
      setStatus(i18n.t("knowledge.added"));
      // El troceado corre en segundo plano; se vuelve a mirar en unos segundos
      // para que la tabla no se quede diciendo «procesando» hasta que alguien
      // cambie de pestaña.
      window.setTimeout(() => loadKnowledge(), 4000);
    } catch (error) {
      showAdminError(error.message);
    }
  });

  /* ---------------------------------------------- estado del sistema */

  function formatBytes(bytes) {
    if (bytes == null) {
      return "—";
    }
    const unidades = ["B", "KB", "MB", "GB", "TB"];
    let valor = bytes;
    let i = 0;
    while (valor >= 1024 && i < unidades.length - 1) {
      valor /= 1024;
      i += 1;
    }
    return `${valor.toFixed(i === 0 ? 0 : 1)} ${unidades[i]}`;
  }

  function formatSeconds(seconds) {
    if (seconds == null) {
      return "—";
    }
    if (seconds < 60) {
      return `${seconds} s`;
    }
    if (seconds < 3600) {
      return `${Math.round(seconds / 60)} min`;
    }
    return `${(seconds / 3600).toFixed(1)} h`;
  }

  function statusPill(status) {
    const pill = document.createElement("span");
    pill.className = `system-pill system-pill--${status}`;
    pill.textContent = i18n.t(`system.status.${status}`);
    return pill;
  }

  /** Una tarjeta: título, semáforo y pares «dato: valor». */
  function systemCard(title, status, rows) {
    const card = document.createElement("article");
    card.className = `system-card system-card--${status}`;
    const head = document.createElement("header");
    const h = document.createElement("h4");
    h.textContent = title;
    head.append(h, statusPill(status));
    card.appendChild(head);
    const list = document.createElement("dl");
    for (const [label, value] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = value;
      list.append(dt, dd);
    }
    card.appendChild(list);
    return card;
  }

  async function loadSystem() {
    showAdminError("");
    let data;
    try {
      data = await api("/api/admin/system");
    } catch (error) {
      showAdminError(error.message);
      return;
    }
    dom.systemOverall.className = `system-pill system-pill--${data.status}`;
    dom.systemOverall.textContent = i18n.t(`system.status.${data.status}`);
    dom.systemGenerated.textContent = i18n.t("system.generated", {
      at: formatTime(data.generated_at),
      since: formatTime(data.started_at) || "—",
    });

    const ai = data.ai;
    const outbox = data.outbox;
    const disk = data.disk;
    dom.systemCards.replaceChildren(
      systemCard(i18n.t("system.ai"), ai.status, [
        [i18n.t("system.provider"), ai.provider ? `${ai.provider} · ${ai.model}` : i18n.t("system.aiOff")],
        [i18n.t("system.runs"), String(ai.runs_24h)],
        [i18n.t("system.errors"), `${ai.errors_24h} (${Math.round(ai.error_rate * 100)} %)`],
        [i18n.t("system.lastError"), ai.last_error
          ? `${formatTime(ai.last_error.at)} — ${ai.last_error.text}`
          : "—"],
      ]),
      systemCard(i18n.t("system.outbox"), outbox.status, [
        [i18n.t("system.pending"), String(outbox.pending)],
        [i18n.t("system.oldest"), formatSeconds(outbox.oldest_pending_seconds)],
        [i18n.t("system.sent"), String(outbox.sent_24h)],
        [i18n.t("system.lost"), String(outbox.failed_24h)],
      ]),
      systemCard(i18n.t("system.disk"), disk.status, disk.error ? [
        [i18n.t("system.lastError"), disk.error],
      ] : [
        [i18n.t("system.free"), `${formatBytes(disk.free_bytes)} (${Math.round(disk.free_ratio * 100)} %)`],
        [i18n.t("system.total"), formatBytes(disk.total_bytes)],
      ]),
      systemCard(i18n.t("system.database"), data.database.status, [
        [i18n.t("system.size"), formatBytes(data.database.size_bytes)],
        [i18n.t("system.environment"), data.environment],
      ]),
    );

    dom.systemChannels.replaceChildren();
    for (const row of data.channels) {
      const tr = document.createElement("tr");
      const celdas = [
        CHANNEL_LABELS[row.channel] || row.channel,
        null,
        String(row.active_accounts),
        formatTime(row.last_inbound_at) || "—",
        formatTime(row.last_outbound_at) || "—",
        String(row.failures_24h),
        row.last_error ? `${formatTime(row.last_error.at)} — ${row.last_error.text}` : "—",
      ];
      celdas.forEach((value, index) => {
        const td = document.createElement("td");
        if (index === 1) {
          td.appendChild(statusPill(row.status));
        } else {
          td.textContent = value;
        }
        tr.appendChild(td);
      });
      dom.systemChannels.appendChild(tr);
    }
  }

  dom.systemRefresh.addEventListener("click", () => loadSystem());

  /* ------------------------------------------------- asistente IA */

  //: Catálogo de OpenRouter tal como lo devuelve el servidor, por id.
  let assistantModels = new Map();
  let assistantDefaultModel = "";

  function describeModel(id) {
    const model = assistantModels.get(id);
    if (!model) {
      return assistantModels.size && id ? i18n.t("assistant.unknownModel") : "";
    }
    const partes = [model.name];
    if (model.input_modalities.length) {
      partes.push(i18n.t("assistant.inputs", { list: model.input_modalities.join(", ") }));
    }
    if (model.prompt_per_million !== null && model.completion_per_million !== null) {
      partes.push(
        i18n.t("assistant.price", {
          input: model.prompt_per_million,
          output: model.completion_per_million,
        }),
      );
    }
    partes.push(model.reasoning ? i18n.t("assistant.reasons") : i18n.t("assistant.noReasoning"));
    return partes.join(" · ");
  }

  function fillAssistant(settings) {
    assistantDefaultModel = settings.default_model;
    const proveedor =
      settings.provider === "openrouter"
        ? i18n.t("assistant.providerOpenrouter")
        : settings.provider === "gemini"
          ? i18n.t("assistant.providerGemini")
          : i18n.t("assistant.providerNone");
    dom.assistantProvider.textContent = proveedor;
    dom.assistantModel.value = settings.model;
    dom.assistantEffort.replaceChildren();
    for (const effort of settings.efforts) {
      const option = document.createElement("option");
      option.value = effort;
      option.textContent = i18n.t(`assistant.effort.${effort}`);
      dom.assistantEffort.appendChild(option);
    }
    dom.assistantEffort.value = settings.effort;
    dom.assistantModelInfo.textContent = describeModel(settings.model);
  }

  /** Una ficha por departamento: interruptor, instrucciones y «Guardar». */
  function buildDepartmentAssistant(row) {
    const card = document.createElement("form");
    card.className = "assistant-department";

    const title = document.createElement("h4");
    title.textContent = row.is_active ? row.name : `${row.name} (${i18n.t("rules.inactive").toLowerCase()})`;
    card.appendChild(title);

    const toggle = document.createElement("label");
    toggle.className = "panel__field panel__field--checkbox";
    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.id = `assistant-enabled-${row.id}`;
    enabled.checked = row.enabled;
    const toggleText = document.createElement("span");
    toggleText.textContent = i18n.t("assistant.departmentEnabled");
    toggle.append(enabled, toggleText);
    card.appendChild(toggle);

    const field = document.createElement("label");
    field.className = "panel__field";
    const fieldText = document.createElement("span");
    fieldText.textContent = i18n.t("assistant.departmentInstructions");
    const instructions = document.createElement("textarea");
    instructions.id = `assistant-instructions-${row.id}`;
    instructions.rows = 4;
    instructions.maxLength = 4000;
    instructions.placeholder = i18n.t("assistant.departmentPlaceholder");
    instructions.value = row.instructions;
    field.append(fieldText, instructions);
    card.appendChild(field);

    const save = document.createElement("button");
    save.type = "submit";
    save.className = "send-button";
    save.textContent = i18n.t("assistant.save");
    card.appendChild(save);

    card.addEventListener("submit", async (event) => {
      event.preventDefault();
      showAdminError("");
      save.disabled = true;
      try {
        await api(`/api/admin/ai/departments/${row.id}`, {
          method: "PUT",
          body: JSON.stringify({
            enabled: enabled.checked,
            instructions: instructions.value,
          }),
        });
        setStatus(i18n.t("assistant.departmentSaved", { name: row.name }));
      } catch (error) {
        showAdminError(error.message);
      } finally {
        save.disabled = false;
      }
    });
    return card;
  }

  async function loadDepartmentAssistants() {
    dom.assistantDepartments.replaceChildren();
    let rows;
    try {
      rows = await api("/api/admin/ai/departments");
    } catch (error) {
      showAdminError(error.message);
      return;
    }
    if (rows.length === 0) {
      const empty = document.createElement("p");
      empty.className = "console__muted";
      empty.textContent = i18n.t("admin.departmentsEmpty");
      dom.assistantDepartments.appendChild(empty);
      return;
    }
    for (const row of rows) {
      dom.assistantDepartments.appendChild(buildDepartmentAssistant(row));
    }
  }

  async function loadAssistant() {
    try {
      fillAssistant(await api("/api/admin/ai"));
    } catch (error) {
      showAdminError(error.message);
      return;
    }
    await loadDepartmentAssistants();
    // El catálogo llega aparte: puede tardar, o no llegar, y el formulario
    // tiene que poder usarse igual.
    try {
      const { models } = await api("/api/admin/ai/models");
      assistantModels = new Map(models.map((model) => [model.id, model]));
      dom.assistantModelList.replaceChildren();
      for (const model of models) {
        const option = document.createElement("option");
        option.value = model.id;
        option.label = model.name;
        dom.assistantModelList.appendChild(option);
      }
      dom.assistantModelInfo.textContent = describeModel(dom.assistantModel.value.trim());
    } catch (error) {
      dom.assistantModelInfo.textContent = i18n.t("assistant.catalogUnavailable");
    }
  }

  dom.assistantModel.addEventListener("input", () => {
    dom.assistantModelInfo.textContent = describeModel(dom.assistantModel.value.trim());
  });

  async function saveAssistant(model) {
    showAdminError("");
    dom.assistantSave.disabled = true;
    try {
      fillAssistant(
        await api("/api/admin/ai", {
          method: "PUT",
          body: JSON.stringify({ model, effort: dom.assistantEffort.value }),
        }),
      );
      setStatus(i18n.t("assistant.saved"));
    } catch (error) {
      showAdminError(error.message);
    } finally {
      dom.assistantSave.disabled = false;
    }
  }

  dom.assistantForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveAssistant(dom.assistantModel.value.trim() || null);
  });

  dom.assistantReset.addEventListener("click", () => {
    dom.assistantModel.value = assistantDefaultModel;
    saveAssistant(null);
  });

  /* ------------------------------------------------ traza de auditoría */

  const AUDIT_PAGE = 100;
  let auditOffset = 0;

  function auditQuery() {
    const params = new URLSearchParams({ limit: String(AUDIT_PAGE), offset: String(auditOffset) });
    if (dom.auditActor.value.trim()) {
      params.set("actor", dom.auditActor.value.trim());
    }
    if (dom.auditAction.value) {
      params.set("action", dom.auditAction.value);
    }
    if (dom.auditSince.value) {
      // El campo da una fecha; el filtro espera un instante.
      params.set("since", `${dom.auditSince.value}T00:00:00`);
    }
    return params.toString();
  }

  async function loadAudit({ reset = false } = {}) {
    if (reset) {
      auditOffset = 0;
      dom.auditTable.replaceChildren();
      await loadAuditActions();
    }
    let entries;
    try {
      entries = await api(`/api/audit?${auditQuery()}`);
    } catch (error) {
      showAdminError(error.message);
      return;
    }
    for (const entry of entries) {
      const row = document.createElement("tr");
      for (const value of [
        formatTime(entry.created_at),
        entry.actor,
        entry.action,
        entry.subject_type ? `${entry.subject_type} ${entry.subject_id ?? ""}`.trim() : "",
        Object.keys(entry.detail || {}).length ? JSON.stringify(entry.detail) : "",
      ]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      }
      dom.auditTable.appendChild(row);
    }
    auditOffset += entries.length;
    dom.auditMore.hidden = entries.length < AUDIT_PAGE;
  }

  async function loadAuditActions() {
    let actions;
    try {
      actions = await api("/api/audit/actions");
    } catch {
      return;
    }
    const chosen = dom.auditAction.value;
    dom.auditAction.replaceChildren();
    const any = document.createElement("option");
    any.value = "";
    any.textContent = i18n.t("audit.anyAction");
    dom.auditAction.appendChild(any);
    for (const action of actions) {
      const option = document.createElement("option");
      option.value = action;
      option.textContent = action;
      dom.auditAction.appendChild(option);
    }
    dom.auditAction.value = chosen;
  }

  dom.auditMore.addEventListener("click", () => loadAudit());
  for (const control of [dom.auditActor, dom.auditAction, dom.auditSince]) {
    control.addEventListener("change", () => loadAudit({ reset: true }));
  }

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

  // Función y no un objeto fijo: así toma el idioma activo en el momento de
  // dibujar la fila, no el que estaba puesto cuando se cargó la página.
  function hotelStatusLabel(status) {
    return i18n.t(`hotel.status.${status}`) || status;
  }

  // Reflejo, solo para no ofrecer botones que el servidor rechazaría; la
  // regla de verdad vive en HOTEL_RESERVATION_TRANSITIONS, en console.py.
  const HOTEL_STATUS_TRANSITIONS = {
    pending: ["confirmed", "cancelled"],
    confirmed: ["checked_in", "cancelled", "no_show"],
    checked_in: ["checked_out"],
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
        empty.textContent = i18n.t("hotel.noRoomsAvailable");
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
      return i18n.t("hotel.noRevenue");
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
    dom.hotelContactLinked.textContent = i18n.t("hotel.linkedTo", {
      name: contact.display_name || contact.primary_email || contact.primary_phone,
    });
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
        empty.textContent = i18n.t("hotel.noMatches");
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
          button.textContent = label || i18n.t("hotel.noContactData");
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
      hotelStatusLabel(reservation.status),
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
      edit.textContent = i18n.t("hotel.edit");
      edit.addEventListener("click", () => startEditingReservation(reservation));
      actionsCell.appendChild(edit);
    }

    (HOTEL_STATUS_TRANSITIONS[reservation.status] || []).forEach((nextStatus) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ghost-button";
      button.textContent = i18n.t(`hotel.action.${nextStatus}`);
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
          setStatus(i18n.t("hotel.statusChangedTo", { status: hotelStatusLabel(nextStatus) }));
        } catch (error) {
          showHotelError(error.message);
        }
      });
      actionsCell.appendChild(button);
    });

    // El teléfono viene del huésped o del contacto que escribió por el
    // canal; sin él no hay a quién mandarle la plantilla de confirmación.
    // Confirmar por WhatsApp es escribir primero, y eso quedó reservado a
    // administración: sin esta condición el botón seguiría a la vista para
    // dar un 403 al tocarlo.
    if (reservation.guest_phone && state.me?.role === "admin") {
      const confirmButton = document.createElement("button");
      confirmButton.type = "button";
      confirmButton.className = "ghost-button";
      confirmButton.textContent = i18n.t("hotel.confirmWhatsApp");
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
      ? i18n.t("hotel.linkedToThisReservation")
      : "";
    dom.hotelReservationFormTitle.textContent = i18n.t("hotel.editReservationTitle");
    dom.hotelReservationSubmit.textContent = i18n.t("hotel.saveChanges");
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
    dom.hotelReservationFormTitle.textContent = i18n.t("hotel.newReservation");
    dom.hotelReservationSubmit.textContent = i18n.t("hotel.createReservation");
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
      showHotelError(i18n.t("hotel.pickRoomFirst"));
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
        setStatus(i18n.t("hotel.reservationUpdated"));
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
        setStatus(i18n.t("hotel.reservationCreated"));
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

      // Aviso de una conversación que no se puede ver (otro departamento, u
      // otra persona ya la tomó). Solo importa si estaba en pantalla.
      if (payload.type === "refresh") {
        const enPantalla =
          state.selected?.id === payload.conversation_id ||
          state.conversations.some((c) => c.id === payload.conversation_id);
        if (!enPantalla) {
          return;
        }
        if (state.selected?.id === payload.conversation_id) {
          clearSelection(i18n.t("console.noLongerVisible"));
        }
        await loadConversations();
        return;
      }

      const esMio = payload.assignee_id === state.me?.agent?.id;
      if (payload.type === "assignment" && esMio) {
        setStatus(`${payload.by} le derivó una conversación.`);
      }

      if (payload.type === "hotel_reservation_created") {
        setStatus(
          i18n.t("hotel.liveNotification", {
            room: payload.room,
            checkIn: payload.check_in,
            checkOut: payload.check_out,
          }),
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
    // Los estados y acciones de reserva se traducen al construir cada fila:
    // sin volver a cargar la tabla, un panel ya abierto se quedaría con el
    // idioma con el que se abrió.
    if (!dom.hotelPanel.hidden && dom.hotelDepartment.value) {
      loadHotelReservations();
      loadHotelReport();
    }
    if (!dom.hotelAdminSetup.hidden && dom.hotelAdminDepartment.value) {
      loadHotelAdminSetup();
    }
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
