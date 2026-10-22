// Equipo: compañeros, supervisión, informes, directorio de contactos y RGPD.

import { CHANNEL_LABELS, api, dom, formatTime, setStatus, state } from "./core.js";
import { seesAllDepartments } from "./inbox.js";
import { describeAssignment, selectConversation } from "./thread.js";
import { showAdminError } from "./admin.js";

/* ----------------------------------------------------------- compañeros */

export function buildDepartmentSelect(agent, { multiple = false } = {}) {
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

export function buildAdminUserRow(agent) {
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

export async function loadAgents() {
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

export function reportsQuery() {
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

export function formatDuration(seconds) {
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

export function renderTiles(report) {
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
export function renderChart(series) {
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

export function renderRows(tbody, rows, columns) {
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

export async function loadReports() {
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

export let contactsSearchTimer = null;

export function renderContactsTable(rows) {
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

export async function loadContacts(search) {
  try {
    const query = search ? `?search=${encodeURIComponent(search)}` : "";
    const rows = await api(`/api/contacts${query}`);
    renderContactsTable(rows);
  } catch (error) {
    setStatus(error.message);
  }
}

export function showProfileError(message) {
  dom.profileError.textContent = message;
  dom.profileError.hidden = !message;
}

export async function openContactProfile(contactId) {
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

export function showPrivacyError(message) {
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
