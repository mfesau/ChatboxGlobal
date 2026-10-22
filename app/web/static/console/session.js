// Acceso: identidad, cambio de contraseña y salida.

import { api, dom, state } from "./core.js";

/* ---------------------------------------------------------------- acceso */

document.addEventListener("console:session-expired", () => showGate());

export function showGate() {
  // "/" es la entrada única de la aplicación: sin sesión de agente válida,
  // la consola no tiene login propio, redirige allá.
  if (state.socket) {
    state.socket.close();
    state.socket = null;
  }
  window.location.href = "/";
}

export function showApp() {
  dom.app.hidden = false;
}

export function showPasswordChange() {
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

export async function loadIdentity() {
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
