// Bandeja: lista, filtros, vistas guardadas y departamento de trabajo.

import { CHANNEL_LABELS, api, dom, formatTime, setStatus, state } from "./core.js";
import { selectConversation } from "./thread.js";
import { loadAgents } from "./team.js";
import { fillLabelFilter, loadChannelAccounts, loadDepartments, showAdminError } from "./admin.js";
import { cancelEditingReservation, showHotelError } from "./hotel.js";

/* -------------------------------------------------------------- bandeja */

export async function loadCounts() {
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
export let cargaEnCurso = null;

export async function loadConversations() {
  if (cargaEnCurso) {
    return cargaEnCurso;
  }
  cargaEnCurso = fetchConversations().finally(() => {
    cargaEnCurso = null;
  });
  return cargaEnCurso;
}

export async function fetchConversations() {
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

export function renderConversations() {
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
export function slaCountdown(dueAt) {
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

export function badge(text, extraClass) {
  const element = document.createElement("span");
  element.className = `console__badge ${extraClass || ""}`.trim();
  element.textContent = text;
  return element;
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
export function reflectFilterTitle(select) {
  select.title = select.options[select.selectedIndex]?.textContent || "";
}

export const FILTERS = [
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
export function viewsKey() {
  return `chatbox.views.${state.me?.agent?.id || "anon"}`;
}

export async function loadSavedViews() {
  try {
    state.savedViews = await api("/api/saved-views");
  } catch {
    state.savedViews = [];
  }
  await migrateBrowserViews();
  renderSavedViews();
}

/** Sube las vistas que quedaron en el navegador de la versión anterior. */
export async function migrateBrowserViews() {
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
export function cleanFilters(filters) {
  const clean = {};
  Object.entries(filters || {}).forEach(([key, value]) => {
    if (value) {
      clean[key] = value;
    }
  });
  return clean;
}

export function currentFilters() {
  return {
    scope: state.scope,
    status: dom.statusFilter.value,
    channel: dom.channelFilter.value,
    department: dom.departmentFilter.value,
    label: dom.labelFilter.value,
  };
}

export function sameFilters(a, b) {
  return ["scope", "status", "channel", "department", "label"].every(
    (key) => (a[key] || "") === (b[key] || ""),
  );
}

export function renderSavedViews() {
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

export async function removeSavedView(view) {
  try {
    await api(`/api/saved-views/${view.id}`, { method: "DELETE" });
    state.savedViews = state.savedViews.filter((row) => row.id !== view.id);
    renderSavedViews();
  } catch (error) {
    setStatus(error.message);
  }
}

export async function applySavedView(view) {
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
export function openSaveView() {
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

export function closeSaveView() {
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

/* -------------------------------------------------- pestañas de departamento */

/* Cada pestaña es el puesto de trabajo de un departamento: al abrirla, la
   bandeja —cola común incluida— queda acotada a él, y a la derecha aparecen
   las acciones del módulo que ese departamento tenga activo. Por eso el
   botón de reservas vive aquí y no suelto en la barra lateral: solo tiene
   sentido dentro del departamento que lleva hotel. */
export function syncDepartmentTabs() {
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

export function openHotelPanelFor(departmentId) {
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

export function workDepartmentKey() {
  return `chatbox.workDepartment.${state.me?.agent?.id || "anon"}`;
}

/** Administración no tiene acotación por departamento y puede ver todos. */
export function seesAllDepartments() {
  return !state.me?.department_ids;
}

export let workDepartmentReady = false;

export function fillWorkDepartment(attendable) {
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

export function rememberWorkDepartment() {
  try {
    window.localStorage.setItem(workDepartmentKey(), dom.departmentFilter.value);
  } catch {
    // Sin almacenamiento (ventana privada): se elige de nuevo en cada visita.
  }
}

export function renderDepartmentTabs(options) {
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
