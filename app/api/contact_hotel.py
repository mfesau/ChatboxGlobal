"""Reservas de hotel desde el chatbox, por el propio cliente.

El módulo ya se podía operar de dos maneras: a mano desde la consola, y por
conversación cuando el asistente tiene herramientas (ver
``app/core/hotel_booking.py``). Faltaba la tercera, que es la que espera quien
entra a la rama de Hotel: consultar qué hay libre y reservar él mismo, con un
formulario, sin depender de que haya un modelo configurado ni de que alguien
del equipo esté mirando la bandeja.

La lógica de negocio no se repite: son las mismas funciones del repositorio que
usan la consola y el asistente. Lo propio de aquí es el alcance —el cliente
solo opera sobre la rama que eligió y sobre reservas a su nombre— y que la
reserva nace ``pending``: la confirma el hotel, no quien la pide.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import ContactDep, SessionDep
from app.core.envelope import ChannelKind
from app.db import repositories as repo
from app.db.models import Contact, Department, HotelRoom
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api/contact/hotel", tags=["chatbox: hotel"])

#: Tope de noches por reserva desde el chatbox. No es una regla del negocio
#: sino un freno: una estadía de años bloquearía la habitación entera y el
#: hotel se enteraría cuando ya no hubiera nada que hacer.
_MAX_NOCHES = 60


class ReservationIn(BaseModel):
    check_in: date
    check_out: date
    guests: int = Field(ge=1, le=20)
    room_type_id: uuid.UUID
    guest_name: str = Field(min_length=1, max_length=160)


async def _hotel_department(session: Any, contact: Contact) -> Department:
    """Rama de hotel con la que este cliente está hablando.

    Se responde 409 y no 404 cuando la rama existe pero no lleva hotel: el
    cliente eligió esa rama a propósito y merece saber que ahí no se reserva,
    en vez de un «no existe» que parece un error del sistema.
    """
    conversation = await repo.find_conversation(
        session,
        tenant_id=contact.tenant_id,
        channel=ChannelKind.WEB,
        channel_conversation_id=str(contact.id),
    )
    department = None
    if conversation is not None and conversation.department_id is not None:
        department = await repo.get_department(session, conversation.department_id)
    if department is None or department.tenant_id != contact.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Elija primero con qué área quiere hablar",
        )
    if not repo.hotel_module_enabled(department):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta área no toma reservas de habitaciones",
        )
    return department


def _check_stay(check_in: date, check_out: date) -> None:
    if check_out <= check_in:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La fecha de salida debe ser posterior a la de entrada",
        )
    if check_in < date.today():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No se puede reservar una fecha que ya pasó",
        )
    if (check_out - check_in).days > _MAX_NOCHES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"La estadía no puede pasar de {_MAX_NOCHES} noches",
        )


def _by_room_type(rooms: list[HotelRoom]) -> dict[uuid.UUID, list[HotelRoom]]:
    grouped: dict[uuid.UUID, list[HotelRoom]] = {}
    for room in rooms:
        grouped.setdefault(room.room_type_id, []).append(room)
    return grouped


@router.get("/availability")
async def availability(
    check_in: date, check_out: date, contact: ContactDep, session: SessionDep
) -> list[dict[str, Any]]:
    """Qué hay libre en esas fechas, por categoría de habitación.

    Se agrupa por categoría y no se devuelve la habitación concreta: al cliente
    le importa el tipo y el precio, y decidir cuál le toca es del hotel.
    """
    _check_stay(check_in, check_out)
    department = await _hotel_department(session, contact)

    rooms = await repo.list_available_hotel_rooms(
        session, department_id=department.id, check_in=check_in, check_out=check_out
    )
    nights = (check_out - check_in).days
    options: list[dict[str, Any]] = []
    for room_type_id, type_rooms in _by_room_type(rooms).items():
        room_type = type_rooms[0].room_type
        rate_plan = await repo.rate_plan_for_stay(
            session, room_type_id=room_type_id, check_in=check_in
        )
        options.append(
            {
                "room_type_id": str(room_type_id),
                "name": room_type.name,
                "description": room_type.description,
                "capacity": room_type.capacity,
                "available": len(type_rooms),
                "nightly_price_cents": rate_plan.nightly_price_cents if rate_plan else None,
                "total_price_cents": (
                    rate_plan.nightly_price_cents * nights if rate_plan else None
                ),
                "currency": rate_plan.currency if rate_plan else None,
                "nights": nights,
            }
        )
    options.sort(key=lambda row: row["name"])
    return options


@router.post("/reservations", status_code=status.HTTP_201_CREATED)
async def reserve(
    body: ReservationIn, contact: ContactDep, session: SessionDep
) -> dict[str, Any]:
    """Reserva una habitación de la categoría elegida, a nombre del cliente.

    Nace ``pending``: el hotel la confirma. La habitación concreta la elige el
    servidor entre las libres, y se vuelve a comprobar el solapamiento sobre esa
    habitación para cerrar la ventana entre consultar y reservar.
    """
    _check_stay(body.check_in, body.check_out)
    department = await _hotel_department(session, contact)

    room_type = await repo.get_hotel_room_type(session, body.room_type_id)
    if (
        room_type is None
        or room_type.department_id != department.id
        or not room_type.is_active
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Esa categoría no existe"
        )
    if body.guests > room_type.capacity:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"«{room_type.name}» admite hasta {room_type.capacity} personas",
        )

    available = await repo.list_available_hotel_rooms(
        session,
        department_id=department.id,
        check_in=body.check_in,
        check_out=body.check_out,
        room_type_id=room_type.id,
    )
    if not available:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya no queda cupo de esa categoría para esas fechas",
        )
    room = available[0]
    if await repo.hotel_room_has_overlap(
        session, room_id=room.id, check_in=body.check_in, check_out=body.check_out
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esa habitación se acaba de reservar; consulte de nuevo",
        )

    rate_plan = await repo.rate_plan_for_stay(
        session, room_type_id=room_type.id, check_in=body.check_in
    )
    conversation = await repo.find_conversation(
        session,
        tenant_id=contact.tenant_id,
        channel=ChannelKind.WEB,
        channel_conversation_id=str(contact.id),
    )
    reservation = await repo.create_hotel_reservation(
        session,
        tenant_id=contact.tenant_id,
        department_id=department.id,
        room_id=room.id,
        guest_name=body.guest_name.strip(),
        guest_phone=contact.primary_phone,
        guest_email=contact.primary_email,
        check_in=body.check_in,
        check_out=body.check_out,
        guests=body.guests,
        contact_id=contact.id,
        conversation_id=conversation.id if conversation is not None else None,
        nightly_price_cents=rate_plan.nightly_price_cents if rate_plan else None,
        currency=rate_plan.currency if rate_plan else "USD",
        status="pending",
    )
    await repo.record_audit(
        session,
        tenant_id=contact.tenant_id,
        actor=f"contact:{contact.primary_email or contact.id}",
        action="hotel_reservation_requested",
        subject_type="hotel_reservation",
        subject_id=str(reservation.id),
        detail={"room": room.code, "check_in": body.check_in.isoformat()},
    )
    log.info(
        "contact_hotel_reservation",
        contact=str(contact.id),
        department=department.name,
        room=room.code,
    )
    return {
        "id": str(reservation.id),
        "status": reservation.status,
        "room": room.code,
        "room_type": room_type.name,
        "check_in": body.check_in.isoformat(),
        "check_out": body.check_out.isoformat(),
        "guests": body.guests,
        "nightly_price_cents": reservation.nightly_price_cents,
        "currency": reservation.currency,
    }
