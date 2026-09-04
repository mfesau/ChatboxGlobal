"""Herramientas de IA del módulo Hotel: consulta de disponibilidad y reserva.

Solo se ofrecen al modelo cuando el departamento de la conversación tiene
activo ``Department.enabled_modules["hotel_booking"]`` — ver
``AIHandler._available_tools`` en ``app/handlers/ai.py``, que es quien decide
si incluir ``HOTEL_TOOLS`` en la llamada al modelo. Este módulo no vuelve a
comprobar esa condición: confía en que quien lo invoca ya lo hizo, igual que
``derivar_a_agente`` confía en que solo se ofrece dentro de una conversación.

Las reservas que crea el bot nacen en estado ``pending`` y no ``confirmed``,
a diferencia de las que carga un agente desde la consola: no hay cobro en
línea que valide por sí solo una reserva hecha por autoservicio, así que
queda a la espera de que el hotel la confirme. Aun así, ocupa la fecha desde
el momento en que se crea —cuenta para ``hotel_room_has_overlap``— para que
dos huéspedes no puedan quedarse con la misma habitación mientras el hotel
la revisa.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.core.hub import hub, inbox_topic
from app.core.pipeline import TurnContext
from app.db import repositories as repo
from app.db.models import HotelRoom
from app.logging_setup import get_logger

log = get_logger(__name__)

CONSULTAR_DISPONIBILIDAD_TOOL: dict[str, Any] = {
    "name": "consultar_disponibilidad_hotel",
    "description": (
        "Consulta qué categorías de habitación del hotel tienen cupo libre para un "
        "rango de fechas, con su precio por noche. Úsala antes de ofrecer una "
        "habitación o de intentar reservarla; nunca inventes disponibilidad ni precios."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "check_in": {
                "type": "string",
                "description": "Fecha de entrada, en formato AAAA-MM-DD.",
            },
            "check_out": {
                "type": "string",
                "description": "Fecha de salida, en formato AAAA-MM-DD.",
            },
        },
        "required": ["check_in", "check_out"],
        "additionalProperties": False,
    },
    "strict": True,
}

CREAR_RESERVA_TOOL: dict[str, Any] = {
    "name": "crear_reserva_hotel",
    "description": (
        "Crea una reserva de hotel para quien escribe. Solo debe usarse después de "
        "haber consultado disponibilidad con consultar_disponibilidad_hotel y de que "
        "el huésped confirmó explícitamente la categoría, las fechas y el precio. La "
        "reserva queda pendiente de que el hotel la confirme: dígale al huésped que "
        "recibirá la confirmación, no que la habitación ya está garantizada."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "room_type_name": {
                "type": "string",
                "description": (
                    "Nombre de la categoría elegida, exactamente como la devolvió "
                    "consultar_disponibilidad_hotel."
                ),
            },
            "check_in": {"type": "string", "description": "Fecha de entrada, AAAA-MM-DD."},
            "check_out": {"type": "string", "description": "Fecha de salida, AAAA-MM-DD."},
            "guest_name": {
                "type": "string",
                "description": "Nombre completo de quien se aloja.",
            },
            "guests": {"type": "integer", "description": "Número de personas que se alojan."},
        },
        "required": ["room_type_name", "check_in", "check_out", "guest_name", "guests"],
        "additionalProperties": False,
    },
    "strict": True,
}

#: Ofrecidas juntas: no tendría sentido reservar sin poder antes consultar.
HOTEL_TOOLS: list[dict[str, Any]] = [CONSULTAR_DISPONIBILIDAD_TOOL, CREAR_RESERVA_TOOL]


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Fecha no válida: {raw!r}; use el formato AAAA-MM-DD") from exc


async def dispatch(ctx: TurnContext, name: str, arguments: dict[str, Any]) -> str | None:
    """Ejecuta una herramienta del módulo Hotel. ``None`` si ``name`` no es una de las suyas."""
    if name == "consultar_disponibilidad_hotel":
        return await _check_availability(ctx, arguments)
    if name == "crear_reserva_hotel":
        return await _create_reservation(ctx, arguments)
    return None


def _group_by_room_type(rooms: list[HotelRoom]) -> dict[str, list[HotelRoom]]:
    by_type: dict[str, list[HotelRoom]] = {}
    for room in rooms:
        by_type.setdefault(room.room_type.name, []).append(room)
    return by_type


async def _check_availability(ctx: TurnContext, arguments: dict[str, Any]) -> str:
    check_in = _parse_date(arguments["check_in"])
    check_out = _parse_date(arguments["check_out"])
    if check_out <= check_in:
        return "La fecha de salida debe ser posterior a la de entrada."

    department_id = ctx.conversation.department_id
    assert department_id is not None  # quien ofrece esta herramienta ya lo comprobó
    rooms = await repo.list_available_hotel_rooms(
        ctx.session, department_id=department_id, check_in=check_in, check_out=check_out
    )
    if not rooms:
        return "No hay ninguna habitación libre para esas fechas."

    lines = [f"Disponibilidad del {check_in.isoformat()} al {check_out.isoformat()}:"]
    for type_name, type_rooms in _group_by_room_type(rooms).items():
        rate_plan = await repo.rate_plan_for_stay(
            ctx.session, room_type_id=type_rooms[0].room_type_id, check_in=check_in
        )
        price = (
            f"{rate_plan.nightly_price_cents / 100:.2f} {rate_plan.currency}/noche"
            if rate_plan is not None
            else "precio a consultar con el hotel"
        )
        capacity = type_rooms[0].room_type.capacity
        lines.append(
            f"- {type_name} (hasta {capacity} personas): {len(type_rooms)} libres, {price}"
        )
    return "\n".join(lines)


async def _create_reservation(ctx: TurnContext, arguments: dict[str, Any]) -> str:
    check_in = _parse_date(arguments["check_in"])
    check_out = _parse_date(arguments["check_out"])
    if check_out <= check_in:
        return "La fecha de salida debe ser posterior a la de entrada."
    guests = int(arguments["guests"])
    if guests < 1:
        return "El número de huéspedes debe ser al menos 1."

    department_id = ctx.conversation.department_id
    assert department_id is not None

    room_type = await repo.find_hotel_room_type_by_name(
        ctx.session, department_id=department_id, name=str(arguments["room_type_name"])
    )
    if room_type is None or not room_type.is_active:
        return "No existe esa categoría de habitación; consulte antes la disponibilidad."

    available = await repo.list_available_hotel_rooms(
        ctx.session,
        department_id=department_id,
        check_in=check_in,
        check_out=check_out,
        room_type_id=room_type.id,
    )
    if not available:
        return "Esa categoría ya no tiene cupo libre para esas fechas; ofrezca otra opción."
    room = available[0]
    # Segunda comprobación, ya sobre la habitación elegida: cierra la ventana
    # entre la consulta anterior y esta reserva, por si otra petición se
    # adelantó justo en ese instante.
    if await repo.hotel_room_has_overlap(
        ctx.session, room_id=room.id, check_in=check_in, check_out=check_out
    ):
        return "Esa habitación se acaba de reservar; consulte disponibilidad de nuevo."

    rate_plan = await repo.rate_plan_for_stay(
        ctx.session, room_type_id=room_type.id, check_in=check_in
    )
    guest_name = str(arguments["guest_name"]).strip() or "Huésped sin nombre"

    reservation = await repo.create_hotel_reservation(
        ctx.session,
        tenant_id=ctx.tenant.id,
        department_id=department_id,
        room_id=room.id,
        guest_name=guest_name,
        guest_phone=ctx.contact.primary_phone if ctx.contact else None,
        guest_email=ctx.contact.primary_email if ctx.contact else None,
        check_in=check_in,
        check_out=check_out,
        guests=guests,
        contact_id=ctx.contact.id if ctx.contact else None,
        conversation_id=ctx.conversation.id,
        nightly_price_cents=rate_plan.nightly_price_cents if rate_plan else None,
        currency=rate_plan.currency if rate_plan else "USD",
        status="pending",
    )
    await repo.record_audit(
        ctx.session,
        tenant_id=ctx.tenant.id,
        actor="ai",
        action="hotel_reservation_created",
        subject_type="hotel_reservation",
        subject_id=str(reservation.id),
        detail={
            "room": room.code,
            "check_in": arguments["check_in"],
            "check_out": arguments["check_out"],
        },
    )
    await hub.publish(
        inbox_topic(ctx.tenant.slug),
        {
            "type": "hotel_reservation_created",
            "conversation_id": str(ctx.conversation.id),
            "reservation_id": str(reservation.id),
            "department_id": str(department_id),
            "room": room.code,
            "check_in": arguments["check_in"],
            "check_out": arguments["check_out"],
        },
    )
    log.info(
        "hotel_reservation_created_by_ai",
        conversation_id=str(ctx.conversation.id),
        reservation_id=str(reservation.id),
    )

    price_text = (
        f"{reservation.nightly_price_cents / 100:.2f} {reservation.currency}/noche"
        if reservation.nightly_price_cents
        else "precio a confirmar con el hotel"
    )
    return (
        f"Reserva registrada: {room_type.name}, del {arguments['check_in']} al "
        f"{arguments['check_out']}, {price_text}. Queda pendiente de que el hotel la "
        "confirme; avise al huésped de que recibirá la confirmación, no que la "
        "habitación ya está garantizada."
    )
