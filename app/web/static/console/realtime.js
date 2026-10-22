// Tiempo real: avisos de la cola común por WebSocket.

import { dom, setStatus, state } from "./core.js";
import { loadConversations } from "./inbox.js";
import { clearSelection, loadAssignments, loadContact, loadMessages, loadNotes, stillVisibleToMe } from "./thread.js";
import { loadHotelReport, loadHotelReservations } from "./hotel.js";

/* ------------------------------------------------------------ tiempo real */

export function connectInbox() {
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
