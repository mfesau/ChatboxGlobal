// Módulo Hotel: configuración y reservas.

import { api, dom, setStatus, state } from "./core.js";
import { openStartPanel } from "./thread.js";
import { showAdminError } from "./admin.js";

/* --------------------------------------------- módulo Hotel: administración */

export function formatMoney(cents, currency) {
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

export let hotelAdminRoomTypes = [];
// Nulo = el formulario de tarifas crea una nueva; con id, corrige esta.
export let editingRatePlanId = null;

export function fillRoomTypeSelect(select) {
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

export function buildRoomTypeRow(roomType) {
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

export async function loadHotelRoomTypes() {
  const departmentId = dom.hotelAdminDepartment.value;
  hotelAdminRoomTypes = await api(`/api/departments/${departmentId}/hotel/room-types`);
  dom.roomTypesTable.textContent = "";
  hotelAdminRoomTypes.forEach((roomType) =>
    dom.roomTypesTable.appendChild(buildRoomTypeRow(roomType)),
  );
  fillRoomTypeSelect(dom.newRoomTypeSelect);
  fillRoomTypeSelect(dom.newRatePlanTypeSelect);
}

export function buildRoomRow(room) {
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

export async function loadHotelRooms() {
  const departmentId = dom.hotelAdminDepartment.value;
  const rooms = await api(`/api/departments/${departmentId}/hotel/rooms`);
  dom.roomsTable.textContent = "";
  rooms.forEach((room) => dom.roomsTable.appendChild(buildRoomRow(room)));
}

export function buildRatePlanRow(ratePlan) {
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

export function startEditingRatePlan(ratePlan) {
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

export function cancelEditingRatePlan() {
  editingRatePlanId = null;
  dom.createRatePlanForm.reset();
  dom.newRatePlanTypeSelect.disabled = false;
  dom.newRatePlanCurrency.value = "PYG";
  dom.ratePlanFormTitle.textContent = i18n.t("hotel.admin.rates");
  dom.ratePlanSubmit.textContent = i18n.t("hotel.admin.createRate");
  dom.ratePlanCancelEdit.hidden = true;
}

dom.ratePlanCancelEdit.addEventListener("click", cancelEditingRatePlan);

export async function loadHotelRatePlans() {
  const departmentId = dom.hotelAdminDepartment.value;
  const ratePlans = await api(`/api/departments/${departmentId}/hotel/rate-plans`);
  dom.ratePlansTable.textContent = "";
  ratePlans.forEach((ratePlan) => dom.ratePlansTable.appendChild(buildRatePlanRow(ratePlan)));
}

export async function loadHotelAdminSetup() {
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

/* --------------------------------------------------- módulo Hotel: reservas */

// Función y no un objeto fijo: así toma el idioma activo en el momento de
// dibujar la fila, no el que estaba puesto cuando se cargó la página.
export function hotelStatusLabel(status) {
  return i18n.t(`hotel.status.${status}`) || status;
}

// Reflejo, solo para no ofrecer botones que el servidor rechazaría; la
// regla de verdad vive en HOTEL_RESERVATION_TRANSITIONS, en console.py.
export const HOTEL_STATUS_TRANSITIONS = {
  pending: ["confirmed", "cancelled"],
  confirmed: ["checked_in", "cancelled", "no_show"],
  checked_in: ["checked_out"],
  checked_out: [],
  cancelled: [],
  no_show: [],
};

// Nulo = el formulario de abajo crea una reserva; con id, corrige esta.
export let editingReservationId = null;
// Contacto elegido en el buscador, para vincularlo a la reserva además de
// copiarle nombre/teléfono/correo. Se limpia junto con el formulario.
export let hotelSelectedContactId = null;
export let hotelContactSearchTimer = null;

export function showHotelError(message) {
  dom.hotelError.textContent = message;
  dom.hotelError.hidden = !message;
}

export async function loadHotelAvailability() {
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

export function formatHotelRevenue(revenue) {
  if (!revenue || revenue.length === 0) {
    return i18n.t("hotel.noRevenue");
  }
  return revenue.map((row) => formatMoney(row.total_cents, row.currency)).join(" + ");
}

export async function loadHotelReport() {
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

export function setHotelContactLink(contact) {
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

export async function searchHotelContacts(term) {
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

export function buildHotelReservationRow(reservation) {
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

export async function startEditingReservation(reservation) {
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

export function cancelEditingReservation() {
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
export async function loadHotelReservations() {
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
