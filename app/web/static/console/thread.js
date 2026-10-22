// Hilo abierto: mensajes, traducción, respuesta, adjuntos y conversación saliente.

import { CHANNEL_LABELS, api, dom, formatTime, isWideLayout, setStatus, state } from "./core.js";
import { loadConversations, renderConversations } from "./inbox.js";
import { renderMacroButtons } from "./admin.js";
import { start } from "./main.js";

/* ----------------------------------------------------------------- hilo */

export async function selectConversation(conversation) {
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
export function fitsConversation(row, conversation) {
  return !row.department_id || (conversation && row.department_id === conversation.department_id);
}

/** « · Hotel» detrás del nombre, para distinguir en las listas de
    administración lo que es de un departamento. */
export function departmentSuffix(row) {
  const department = state.departments.find((d) => d.id === row.department_id);
  return department ? ` · ${department.name}` : "";
}

export function renderConversationLabelToggles() {
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

export async function toggleConversationLabel(labelId) {
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

export function renderOwnership() {
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

export async function loadMessages() {
  if (!state.selected) {
    return;
  }
  try {
    const messages = await api(`/api/conversations/${state.selected.id}/messages`);
    dom.thread.textContent = "";
    const burbujas = new Map();
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
      burbujas.set(message.id, { bubble, meta, message });
    });
    dom.thread.scrollTop = dom.thread.scrollHeight;
    setStatus(i18n.t("thread.messageCount", { n: messages.length }));
    updateSend();
    translateThread(state.selected.id, burbujas);
  } catch (error) {
    handleThreadError(error, i18n.t("thread.loadFailed", { error: error.message }));
  }
}

/* ----------------------------------------------------------- traducción */

/* Lo que escribe el cliente aparece también en el idioma de la consola, y
   la respuesta puede salir en el del cliente. Así cualquier agente puede
   llevar cualquier conversación. El original nunca se tapa: la traducción
   va debajo, con su idioma delante. */

//: Idioma del cliente en la conversación abierta, si se conoce.
state.customerLanguage = null;

export function languageName(code) {
  const clave = `translate.lang.${code}`;
  const nombre = i18n.t(clave);
  return nombre === clave ? code : nombre;
}

export function showTranslation(entry, texto) {
  if (!texto || texto === entry.message.text) {
    return;
  }
  entry.bubble.querySelector(".bubble__translation")?.remove();
  const linea = document.createElement("span");
  linea.className = "bubble__translation";
  const etiqueta = document.createElement("strong");
  etiqueta.textContent = i18n.t("translate.in", { lang: languageName(i18n.current()) });
  linea.append(etiqueta, document.createTextNode(` ${texto}`));
  entry.bubble.insertBefore(linea, entry.meta);
}

export function renderTranslateReply() {
  const cliente = state.customerLanguage;
  const visible = Boolean(cliente) && cliente !== i18n.current();
  dom.translateReplyField.hidden = !visible;
  if (visible) {
    dom.translateReplyLabel.textContent = i18n.t("translate.reply", {
      lang: languageName(cliente),
    });
  }
}

export async function translateThread(conversationId, burbujas) {
  const destino = i18n.current();
  // Lo ya traducido se pinta al momento; solo se pregunta por lo que falta.
  let faltan = false;
  for (const entry of burbujas.values()) {
    const guardadas = entry.message.translations || {};
    if (!entry.message.text) {
      continue;
    }
    if (destino in guardadas) {
      showTranslation(entry, guardadas[destino]);
    } else {
      faltan = true;
    }
  }
  state.customerLanguage = null;
  let respuesta;
  try {
    respuesta = await api(`/api/conversations/${conversationId}/translate`, {
      method: "POST",
      body: JSON.stringify({ target: destino }),
    });
  } catch (error) {
    // Sin asistente configurado no hay traducción; el hilo se lee igual.
    if (faltan) {
      setStatus(i18n.t("translate.unavailable"));
    }
    renderTranslateReply();
    return;
  }
  // La conversación abierta pudo cambiar mientras se traducía.
  if (state.selected?.id !== conversationId) {
    return;
  }
  for (const [id, texto] of Object.entries(respuesta.translations || {})) {
    const entry = burbujas.get(id);
    if (entry) {
      showTranslation(entry, texto);
    }
  }
  state.customerLanguage = respuesta.customer_language;
  renderTranslateReply();
}

/* Un adjunto se muestra según lo que sea. Antes solo se contemplaba la
   imagen, de modo que un vídeo o un audio dejaban la burbuja vacía y no había
   ni forma de descargarlos. El documento no se puede previsualizar, así que
   al menos se ofrece como enlace con su nombre. */
export function renderAttachment(attachment) {
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

export function authorLabel(message) {
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

export async function loadNotes() {
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

export async function loadAssignments() {
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

export async function loadContact() {
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

export function describeAssignment(entry) {
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

export function clearSelection(message) {
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

export function handleThreadError(error, fallback) {
  // Un 404 sobre el hilo abierto no es un fallo: alguien lo derivó o lo tomó
  // mientras estaba a la vista. Se informa con naturalidad y se limpia.
  if (error.status === 404) {
    clearSelection("La conversación ya la atiende otro compañero.");
    return;
  }
  setStatus(fallback);
}

export function stillVisibleToMe(assigneeId) {
  // Un agente conserva acceso a lo propio y a lo que queda en la cola común;
  // supervisión lo conserva siempre. Se decide con la respuesta del servidor,
  // sin una petición extra que además fallaría con 404.
  if (state.me?.is_supervisor) {
    return true;
  }
  return !assigneeId || assigneeId === state.me?.agent?.id;
}

export async function act(path, body, successMessage) {
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

export function renderConversationState() {
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
    const traducir =
      text && !dom.translateReplyField.hidden && dom.translateReply.checked;
    if (traducir) {
      setStatus(i18n.t("translate.sending", { lang: languageName(state.customerLanguage) }));
    }
    await api(`/api/conversations/${state.selected.id}/reply`, {
      method: "POST",
      body: JSON.stringify({
        text,
        attachments: attachment ? [attachment] : [],
        translate_to: traducir ? state.customerLanguage : null,
        source_language: i18n.current(),
      }),
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

export function updateSend() {
  const hasText = dom.input.value.trim().length > 0;
  dom.send.disabled = !state.selected || (!hasText && !state.pendingAttachment);
}

/* -------------------------------------------------------------- adjuntos */

export function clearAttachment() {
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

export function updateCannedSuggestions() {
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

export function renderCannedSuggestions() {
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

export function hideCannedSuggestions() {
  state.cannedMatches = [];
  state.cannedHighlight = -1;
  dom.cannedSuggestions.hidden = true;
  dom.cannedSuggestions.textContent = "";
}

export function applyCannedResponse(canned) {
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

/* ------------------------------------- nueva conversacion saliente */

/* WhatsApp no deja escribir texto libre a quien no ha escrito en las ultimas
   24 horas: fuera de esa ventana solo admite plantillas aprobadas. Por eso
   este dialogo no tiene campo de texto, sino una plantilla y sus huecos. */

export let startTemplates = [];

export function showStartError(message) {
  dom.startError.textContent = message;
  dom.startError.hidden = !message;
}

export function selectedTemplate() {
  return startTemplates.find(
    (t) => `${t.name}|${t.language}` === dom.startTemplate.value,
  );
}

/* Enseña el texto tal como lo recibira la persona, con los huecos ya
   rellenos. El nombre tecnico de una plantilla no dice nada de lo que
   contiene, y enviarla a ciegas es como firmar sin leer. */
export function renderStartPreview() {
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

export function renderStartVariables() {
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

export function fillStartTemplates() {
  dom.startTemplate.textContent = "";
  startTemplates.forEach((plantilla) => {
    const option = document.createElement("option");
    option.value = `${plantilla.name}|${plantilla.language}`;
    option.textContent = `${plantilla.name} (${plantilla.language})`;
    dom.startTemplate.appendChild(option);
  });
  renderStartVariables();
}

export async function openStartPanel() {
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
