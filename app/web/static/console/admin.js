// Administración: departamentos, canales, etiquetas, macros, reglas y ajustes.

import { CHANNEL_LABELS, api, dom, extractDetail, formatTime, setStatus, state } from "./core.js";
import { fillWorkDepartment, loadConversations, reflectFilterTitle, renderDepartmentTabs } from "./inbox.js";
import { departmentSuffix, fitsConversation, loadAssignments, loadMessages, loadNotes, renderConversationLabelToggles } from "./thread.js";
import { loadAgents } from "./team.js";

/* ------------------------------------------------------------ departamentos */

export async function loadDepartments() {
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
export function buildDepartmentRow(department) {
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

export async function uploadDepartmentLogo(departmentId, file) {
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

export async function removeDepartmentLogo(departmentId) {
  showAdminError("");
  try {
    await api(`/api/departments/${departmentId}/logo`, { method: "DELETE" });
    await loadDepartments();
    setStatus(i18n.t("admin.departmentLogoUpdated"));
  } catch (error) {
    showAdminError(error.message);
  }
}

/* ------------------------------------------------------------------ macros */

//: La etiqueta se resuelve al pintar, no aquí: si se guardara traducida,
//: cambiar de idioma dejaría los pasos ya dibujados en el idioma anterior.
export const MACRO_ACTIONS = {
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
export function refreshMacroStepFields() {
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

export function renderMacroDraft() {
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

export function describeMacroStep(step) {
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

export async function loadMacros() {
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

export function renderMacroButtons() {
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

export async function runMacro(macro, button) {
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
export const WEEKDAYS = ["1", "2", "3", "4", "5", "6", "7"];

export function buildHoursRows() {
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

export function fillHoursForm(department) {
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

export function collectHours() {
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
export const SERVICE_DEFAULTS = "__defaults__";

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
export function fillLabelFilter() {
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

export async function loadLabels() {
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

export async function loadCannedResponses() {
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

export const CHANNEL_ACCOUNT_LABELS = {
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
export const CHANNEL_CREDENTIAL_FIELDS = {
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

export function buildChannelAccountRow(account) {
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
export async function removeChannelAccount(account, button) {
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

export async function loadChannelAccounts() {
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
export function reflectAccountChannel() {
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

export function collectAccountCredentials() {
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

/* ------------------------------------- pestañas de administración */

/* El panel reunía en una sola columna usuarios, canales, etiquetas, macros
   y horarios: había que desplazarse mucho para encontrar cada cosa. Ahora
   cada asunto vive en su pestaña y solo se muestra una a la vez. */
export const adminTabs = [...document.querySelectorAll(".admin-tabs button")];
export const adminPanels = [...document.querySelectorAll(".admin-tab-panel")];

export function showAdminTab(name) {
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
export function ruleActionsFromForm() {
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

export function describeRule(rule) {
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

export async function loadRules() {
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

export function fillCsatDepartments() {
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

export async function loadKnowledge() {
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

export function formatBytes(bytes) {
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

export function formatSeconds(seconds) {
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

export function statusPill(status) {
  const pill = document.createElement("span");
  pill.className = `system-pill system-pill--${status}`;
  pill.textContent = i18n.t(`system.status.${status}`);
  return pill;
}

/** Una tarjeta: título, semáforo y pares «dato: valor». */
export function systemCard(title, status, rows) {
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

export async function loadSystem() {
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
export let assistantModels = new Map();
export let assistantDefaultModel = "";

export function describeModel(id) {
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

export function fillAssistant(settings) {
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
export function buildDepartmentAssistant(row) {
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

export async function loadDepartmentAssistants() {
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

export async function loadAssistant() {
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

export async function saveAssistant(model) {
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

export const AUDIT_PAGE = 100;
export let auditOffset = 0;

export function auditQuery() {
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

export async function loadAudit({ reset = false } = {}) {
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

export async function loadAuditActions() {
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

export function showAdminError(message) {
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

/* ------------------------------------- apariencia: color de la marca */

/* Ocho colores de arranque. No son una paleta cerrada —al lado hay un
   selector libre y un campo hexadecimal— sino un atajo: la mayoria de las
   marcas cae cerca de alguno, y elegir de una lista corta es mas rapido y
   da mejor resultado que buscar a pulso dentro de un cuadrado de color. */
export const BRAND_PRESETS = [
  "#2f5bd7", "#0b7285", "#1f9254", "#b7791f",
  "#c0392b", "#d6336c", "#7048e8", "#495057",
];

//: Color guardado en el servidor. `null` = ninguno, y rige la paleta de
//: partida de la hoja de estilos.
export let brandSaved = null;

/* «Volver al color de partida» solo tiene sentido si hay algo que deshacer.
   Deshabilitado dice, sin gastar una linea de texto, que la consola ya esta
   con los colores originales. */
export function reflectBrandSaved() {
  dom.brandReset.disabled = brandSaved === null;
}

export function renderBrandPresets() {
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

export function markBrandPreset(color) {
  [...dom.brandPresets.children].forEach((button) =>
    button.setAttribute("aria-pressed", String(button.dataset.color === color)),
  );
}

/* Pinta la muestra con los colores que devolvio el servidor. El calculo de
   contraste no se repite aqui a proposito: vive entero en
   app/core/branding.py, y una segunda copia en JavaScript acabaria
   desviandose de la primera sin que nadie lo notara. */
export function paintBrandPreview(palette) {
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
export function applyBrandCss(css) {
  dom.brandStyle.textContent = css || "";
}

export let brandPreviewTimer = null;

/* Deja el color elegido en los tres sitios que lo muestran y pide la
   muestra. La peticion se retrasa un poco porque arrastrar el selector de
   color dispara un evento por cada pixel recorrido. */
export function setBrandChoice(color, { preview = true } = {}) {
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

export async function loadBranding() {
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

export async function loadAutoReply() {
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

export function updateNewUserPasswordVisibility() {
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
