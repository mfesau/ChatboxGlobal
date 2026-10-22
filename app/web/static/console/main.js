// Arranque de la consola.

import { dom, state } from "./core.js";
import { loadIdentity } from "./session.js";
import { loadConversations, loadSavedViews, renderConversations, renderSavedViews } from "./inbox.js";
import { loadMessages, renderConversationLabelToggles } from "./thread.js";
import { loadAgents } from "./team.js";
import { buildHoursRows, loadCannedResponses, loadChannelAccounts, loadDepartments, loadLabels, loadMacros, refreshMacroStepFields, renderMacroButtons, renderMacroDraft } from "./admin.js";
import { loadHotelAdminSetup, loadHotelReport, loadHotelReservations } from "./hotel.js";
import { connectInbox } from "./realtime.js";

/* ---------------------------------------------------------------- arranque */

/* Al cambiar de idioma, lo marcado en el HTML lo traduce `i18n` solo; aquí
   se rehace lo que dibuja esta hoja, que no lleva marcas. */
document.addEventListener("languagechange", () => {
  if (state.selected) {
    loadMessages();
  }
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

export async function start() {
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
