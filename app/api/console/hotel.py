"""Módulo Hotel: habitaciones, tarifas y reservas, acotado a su departamento."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.console.schemas import (
    HotelContactOut,
    HotelModuleIn,
    HotelModuleOut,
    HotelRatePlanIn,
    HotelRatePlanOut,
    HotelRatePlanPatchIn,
    HotelReportOut,
    HotelReservationIn,
    HotelReservationOut,
    HotelReservationPatchIn,
    HotelReservationStatusIn,
    HotelRevenueOut,
    HotelRoomIn,
    HotelRoomOut,
    HotelRoomPatchIn,
    HotelRoomTypeIn,
    HotelRoomTypeOut,
    HotelRoomTypePatchIn,
)
from app.api.deps import (
    AdminDep,
    Principal,
    PrincipalDep,
    SessionDep,
    SettingsDep,
)
from app.core import csat
from app.core.business_hours import resolve_timezone
from app.core.envelope import (
    utcnow,
)
from app.db import repositories as repo
from app.db.models import (
    Department,
    HotelRatePlan,
    HotelReservation,
    HotelRoom,
    HotelRoomType,
)

router = APIRouter(prefix="/api", tags=["consola"])



# --------------------------------------------------------------------------- #
# Módulo Hotel: habitaciones y reservas, acotado al departamento que lo activa
# --------------------------------------------------------------------------- #
async def _load_hotel_department(
    session: SessionDep, settings: SettingsDep, *, department_id: uuid.UUID, tenant: str | None
) -> Department:
    """Departamento del inquilino activo, sin comprobar acceso ni módulo.

    Es el paso común a los tres puntos de entrada del módulo: los dos que
    configuran su activación (que deben funcionar aunque el módulo todavía no
    esté encendido) y ``_require_hotel_department``, que suma las dos
    comprobaciones que sí dependen de para qué se use.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    department = await repo.get_department(session, department_id)
    if department is None or department.tenant_id != tenant_row.id or not department.is_active:
        # Mismo criterio que ``_transfer_to_department``: un departamento
        # inactivo no admite operaciones nuevas, sean conversaciones u hotel.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El departamento no existe o está inactivo",
        )
    return department


async def _require_hotel_department(
    session: SessionDep,
    settings: SettingsDep,
    principal: Principal,
    *,
    department_id: uuid.UUID,
    tenant: str | None,
) -> Department:
    """Departamento válido, accesible para quien pregunta y con el módulo activo.

    Un departamento inexistente y uno sin acceso responden igual —404—, para no
    filtrar por el código de error qué departamentos existen en el inquilino.
    """
    department = await _load_hotel_department(
        session, settings, department_id=department_id, tenant=tenant
    )
    allowed = principal.department_ids
    if allowed is not None and department.id not in allowed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
        )
    if not repo.hotel_module_enabled(department):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El módulo de hotel no está activo para este departamento",
        )
    return department


class CsatModuleOut(BaseModel):
    enabled: bool
    question: str


class CsatModuleIn(BaseModel):
    enabled: bool
    #: Nulo = conservar la que hubiera; cadena vacía = volver a la de omisión.
    question: str | None = Field(default=None, max_length=500)


@router.get("/departments/{department_id}/csat", response_model=CsatModuleOut)
async def get_csat_module(
    department_id: uuid.UUID, session: SessionDep, principal: AdminDep
) -> CsatModuleOut:
    """Si esta rama pregunta la satisfacción al cerrar. Reservado a administración."""
    department = await repo.get_department(session, department_id)
    if department is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
        )
    return CsatModuleOut(
        enabled=csat.enabled(department), question=csat.question(department)
    )


@router.put("/departments/{department_id}/csat", response_model=CsatModuleOut)
async def set_csat_module(
    department_id: uuid.UUID,
    body: CsatModuleIn,
    session: SessionDep,
    principal: AdminDep,
) -> CsatModuleOut:
    """Activa la encuesta en una rama y fija su pregunta. Reservado a administración.

    Por departamento y no global, igual que el módulo de hotel: una rama puede
    querer medirse y otra no, y preguntar donde no se va a mirar la respuesta
    solo molesta a quien escribe.
    """
    department = await repo.get_department(session, department_id)
    if department is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
        )

    await csat.set_enabled(
        session, department=department, enabled=body.enabled, question=body.question
    )
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="csat_module_toggled",
        subject_type="department",
        subject_id=str(department.id),
        detail={"enabled": body.enabled},
    )
    return CsatModuleOut(
        enabled=csat.enabled(department), question=csat.question(department)
    )


@router.get("/departments/{department_id}/hotel/module", response_model=HotelModuleOut)
async def get_hotel_module(
    department_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelModuleOut:
    """Si el módulo de hotel está activo para este departamento. Reservado a administración."""
    department = await _load_hotel_department(
        session, settings, department_id=department_id, tenant=tenant
    )
    return HotelModuleOut(enabled=repo.hotel_module_enabled(department))


@router.put("/departments/{department_id}/hotel/module", response_model=HotelModuleOut)
async def set_hotel_module(
    department_id: uuid.UUID,
    body: HotelModuleIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelModuleOut:
    """Activa o desactiva el módulo de hotel de un departamento. Reservado a administración.

    El resto de ramas de negocio del inquilino no se ven afectadas: cada
    departamento activa el módulo por su cuenta.
    """
    department = await _load_hotel_department(
        session, settings, department_id=department_id, tenant=tenant
    )
    await repo.set_hotel_module_enabled(session, department=department, enabled=body.enabled)
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_module_toggled",
        subject_type="department",
        subject_id=str(department.id),
        detail={"enabled": body.enabled},
    )
    return HotelModuleOut(enabled=body.enabled)


def _hotel_room_type_out(row: HotelRoomType) -> HotelRoomTypeOut:
    return HotelRoomTypeOut(
        id=row.id,
        name=row.name,
        description=row.description,
        capacity=row.capacity,
        is_active=row.is_active,
    )


@router.get(
    "/departments/{department_id}/hotel/room-types", response_model=list[HotelRoomTypeOut]
)
async def list_hotel_room_types(
    department_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> list[HotelRoomTypeOut]:
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    rows = await repo.list_hotel_room_types(session, department_id=department.id)
    return [_hotel_room_type_out(row) for row in rows]


@router.post(
    "/departments/{department_id}/hotel/room-types",
    status_code=status.HTTP_201_CREATED,
    response_model=HotelRoomTypeOut,
)
async def create_hotel_room_type(
    department_id: uuid.UUID,
    body: HotelRoomTypeIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelRoomTypeOut:
    """Crea una categoría de habitación (Individual, Doble, Suite…). Reservado a administración."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    if await repo.find_hotel_room_type_by_name(
        session, department_id=department.id, name=body.name
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe una categoría con ese nombre"
        )
    room_type = await repo.create_hotel_room_type(
        session,
        tenant_id=department.tenant_id,
        department_id=department.id,
        name=body.name,
        description=body.description,
        capacity=body.capacity,
    )
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_room_type_created",
        subject_type="hotel_room_type",
        subject_id=str(room_type.id),
        detail={"name": room_type.name},
    )
    return _hotel_room_type_out(room_type)


@router.patch(
    "/departments/{department_id}/hotel/room-types/{room_type_id}",
    response_model=HotelRoomTypeOut,
)
async def update_hotel_room_type(
    department_id: uuid.UUID,
    room_type_id: uuid.UUID,
    body: HotelRoomTypePatchIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelRoomTypeOut:
    """Edita una categoría de habitación. Reservado a administración."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    room_type = await repo.get_hotel_room_type(session, room_type_id)
    if room_type is None or room_type.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La categoría no existe")
    if body.description is not None:
        room_type.description = body.description
    if body.capacity is not None:
        room_type.capacity = body.capacity
    if body.is_active is not None:
        room_type.is_active = body.is_active
    await session.flush()
    return _hotel_room_type_out(room_type)


def _hotel_room_out(row: HotelRoom) -> HotelRoomOut:
    return HotelRoomOut(
        id=row.id,
        room_type_id=row.room_type_id,
        room_type_name=row.room_type.name,
        code=row.code,
        status=row.status,
        notes=row.notes,
    )


@router.get("/departments/{department_id}/hotel/rooms", response_model=list[HotelRoomOut])
async def list_hotel_rooms(
    department_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
    room_type_id: uuid.UUID | None = None,
) -> list[HotelRoomOut]:
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    rows = await repo.list_hotel_rooms(
        session, department_id=department.id, room_type_id=room_type_id
    )
    return [_hotel_room_out(row) for row in rows]


@router.post(
    "/departments/{department_id}/hotel/rooms",
    status_code=status.HTTP_201_CREATED,
    response_model=HotelRoomOut,
)
async def create_hotel_room(
    department_id: uuid.UUID,
    body: HotelRoomIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelRoomOut:
    """Da de alta una habitación física. Reservado a administración."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    room_type = await repo.get_hotel_room_type(session, body.room_type_id)
    if room_type is None or room_type.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La categoría no existe")
    if not room_type.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La categoría está retirada; no admite habitaciones nuevas",
        )
    if await repo.find_hotel_room_by_code(session, department_id=department.id, code=body.code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe una habitación con ese número"
        )
    room = await repo.create_hotel_room(
        session,
        tenant_id=department.tenant_id,
        department_id=department.id,
        room_type_id=room_type.id,
        code=body.code,
        notes=body.notes,
    )
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_room_created",
        subject_type="hotel_room",
        subject_id=str(room.id),
        detail={"code": room.code},
    )
    # Se asigna en Python y no se vuelve a consultar: en una sesión async una
    # carga perezosa de la relación fallaría fuera del ``await`` que la trajo.
    room.room_type = room_type
    return _hotel_room_out(room)


@router.patch("/departments/{department_id}/hotel/rooms/{room_id}", response_model=HotelRoomOut)
async def update_hotel_room(
    department_id: uuid.UUID,
    room_id: uuid.UUID,
    body: HotelRoomPatchIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelRoomOut:
    """Cambia el estado o las notas de una habitación. Reservado a administración."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    room = await repo.get_hotel_room(session, room_id)
    if room is None or room.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La habitación no existe")
    if body.status is not None:
        room.status = body.status
    if body.notes is not None:
        room.notes = body.notes
    await session.flush()
    return _hotel_room_out(room)


def _hotel_rate_plan_out(row: HotelRatePlan) -> HotelRatePlanOut:
    return HotelRatePlanOut(
        id=row.id,
        room_type_id=row.room_type_id,
        name=row.name,
        starts_on=row.starts_on.isoformat() if row.starts_on else None,
        ends_on=row.ends_on.isoformat() if row.ends_on else None,
        nightly_price_cents=row.nightly_price_cents,
        currency=row.currency,
    )


@router.get(
    "/departments/{department_id}/hotel/rate-plans", response_model=list[HotelRatePlanOut]
)
async def list_hotel_rate_plans(
    department_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
    room_type_id: uuid.UUID | None = None,
) -> list[HotelRatePlanOut]:
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    rows = await repo.list_hotel_rate_plans(
        session, department_id=department.id, room_type_id=room_type_id
    )
    return [_hotel_rate_plan_out(row) for row in rows]


@router.post(
    "/departments/{department_id}/hotel/rate-plans",
    status_code=status.HTTP_201_CREATED,
    response_model=HotelRatePlanOut,
)
async def create_hotel_rate_plan(
    department_id: uuid.UUID,
    body: HotelRatePlanIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelRatePlanOut:
    """Crea una tarifa por noche para una categoría. Reservado a administración."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    room_type = await repo.get_hotel_room_type(session, body.room_type_id)
    if room_type is None or room_type.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La categoría no existe")
    if (
        body.starts_on is not None
        and body.ends_on is not None
        and body.ends_on <= body.starts_on
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La fecha de fin debe ser posterior a la de inicio",
        )
    rate_plan = await repo.create_hotel_rate_plan(
        session,
        tenant_id=department.tenant_id,
        department_id=department.id,
        room_type_id=room_type.id,
        name=body.name,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
        nightly_price_cents=body.nightly_price_cents,
        currency=body.currency,
    )
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_rate_plan_created",
        subject_type="hotel_rate_plan",
        subject_id=str(rate_plan.id),
        detail={"name": rate_plan.name, "room_type": room_type.name},
    )
    return _hotel_rate_plan_out(rate_plan)


@router.delete(
    "/departments/{department_id}/hotel/rate-plans/{rate_plan_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_hotel_rate_plan(
    department_id: uuid.UUID,
    rate_plan_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> None:
    """Borra una tarifa. Reservado a administración."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    rate_plan = await repo.get_hotel_rate_plan(session, rate_plan_id)
    if rate_plan is None or rate_plan.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La tarifa no existe")
    await repo.delete_hotel_rate_plan(session, rate_plan)
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_rate_plan_deleted",
        subject_type="hotel_rate_plan",
        subject_id=str(rate_plan.id),
        detail={"name": rate_plan.name},
    )


@router.patch(
    "/departments/{department_id}/hotel/rate-plans/{rate_plan_id}",
    response_model=HotelRatePlanOut,
)
async def update_hotel_rate_plan(
    department_id: uuid.UUID,
    rate_plan_id: uuid.UUID,
    body: HotelRatePlanPatchIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelRatePlanOut:
    """Edita una tarifa ya cargada —por ejemplo, para corregir el precio.
    Reservado a administración."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    rate_plan = await repo.get_hotel_rate_plan(session, rate_plan_id)
    if rate_plan is None or rate_plan.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La tarifa no existe")

    starts_on = body.starts_on if body.starts_on is not None else rate_plan.starts_on
    ends_on = body.ends_on if body.ends_on is not None else rate_plan.ends_on
    if starts_on is not None and ends_on is not None and ends_on <= starts_on:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La fecha de fin debe ser posterior a la de inicio",
        )

    await repo.update_hotel_rate_plan(
        session,
        rate_plan=rate_plan,
        name=body.name,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
        nightly_price_cents=body.nightly_price_cents,
        currency=body.currency,
    )
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_rate_plan_updated",
        subject_type="hotel_rate_plan",
        subject_id=str(rate_plan.id),
        detail={"name": rate_plan.name},
    )
    return _hotel_rate_plan_out(rate_plan)


def _hotel_reservation_out(row: HotelReservation) -> HotelReservationOut:
    return HotelReservationOut(
        id=row.id,
        room_id=row.room_id,
        room_code=row.room.code,
        room_type_name=row.room.room_type.name,
        guest_name=row.guest_name,
        guest_phone=row.guest_phone,
        guest_email=row.guest_email,
        check_in=row.check_in.isoformat(),
        check_out=row.check_out.isoformat(),
        guests=row.guests,
        status=row.status,
        nightly_price_cents=row.nightly_price_cents,
        currency=row.currency,
        notes=row.notes,
        contact_id=row.contact_id,
        conversation_id=row.conversation_id,
    )


@router.get(
    "/departments/{department_id}/hotel/availability", response_model=list[HotelRoomOut]
)
async def hotel_availability(
    department_id: uuid.UUID,
    check_in: date,
    check_out: date,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
    room_type_id: uuid.UUID | None = None,
    #: Al editar una reserva, para que su propia habitación no quede excluida
    #: por chocar contra sí misma en sus propias fechas.
    exclude_reservation_id: uuid.UUID | None = None,
) -> list[HotelRoomOut]:
    """Habitaciones libres —sin ninguna reserva activa que se solape— para un rango de fechas."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    if check_out <= check_in:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La salida debe ser posterior a la entrada",
        )
    rows = await repo.list_available_hotel_rooms(
        session,
        department_id=department.id,
        check_in=check_in,
        check_out=check_out,
        room_type_id=room_type_id,
        exclude_reservation_id=exclude_reservation_id,
    )
    return [_hotel_room_out(row) for row in rows]


@router.get(
    "/departments/{department_id}/hotel/reservations",
    response_model=list[HotelReservationOut],
)
async def list_hotel_reservations(
    department_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[HotelReservationOut]:
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    rows = await repo.list_hotel_reservations(
        session,
        department_id=department.id,
        status=status_filter,
        from_date=from_date,
        to_date=to_date,
    )
    return [_hotel_reservation_out(row) for row in rows]


@router.post(
    "/departments/{department_id}/hotel/reservations",
    status_code=status.HTTP_201_CREATED,
    response_model=HotelReservationOut,
)
async def create_hotel_reservation(
    department_id: uuid.UUID,
    body: HotelReservationIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> HotelReservationOut:
    """Crea una reserva. Cualquier persona con acceso al departamento puede cargarla."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    room = await repo.get_hotel_room(session, body.room_id)
    if room is None or room.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La habitación no existe")
    if not room.room_type.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La categoría de esta habitación está retirada",
        )
    if room.status != "available":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La habitación no está disponible (en mantenimiento o fuera de servicio)",
        )
    if await repo.hotel_room_has_overlap(
        session, room_id=room.id, check_in=body.check_in, check_out=body.check_out
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La habitación ya está reservada en esas fechas",
        )
    if body.contact_id is not None:
        contact = await repo.get_contact(session, body.contact_id)
        if contact is None or contact.tenant_id != department.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="El contacto no existe"
            )

    price = body.nightly_price_cents
    currency = body.currency
    if price is None:
        rate_plan = await repo.rate_plan_for_stay(
            session, room_type_id=room.room_type_id, check_in=body.check_in
        )
        if rate_plan is not None:
            price = rate_plan.nightly_price_cents
            currency = rate_plan.currency

    reservation = await repo.create_hotel_reservation(
        session,
        tenant_id=department.tenant_id,
        department_id=department.id,
        room_id=room.id,
        guest_name=body.guest_name,
        guest_phone=body.guest_phone,
        guest_email=body.guest_email,
        contact_id=body.contact_id,
        check_in=body.check_in,
        check_out=body.check_out,
        guests=body.guests,
        created_by_agent_id=principal.id,
        nightly_price_cents=price,
        currency=currency,
        notes=body.notes,
    )
    # Ya se tenía cargada con su categoría; se evita una carga perezosa, que en
    # una sesión async fallaría al serializar la respuesta.
    reservation.room = room
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_reservation_created",
        subject_type="hotel_reservation",
        subject_id=str(reservation.id),
        detail={
            "room": room.code,
            "check_in": body.check_in.isoformat(),
            "check_out": body.check_out.isoformat(),
        },
    )
    return _hotel_reservation_out(reservation)


@router.patch(
    "/departments/{department_id}/hotel/reservations/{reservation_id}",
    response_model=HotelReservationOut,
)
async def update_hotel_reservation(
    department_id: uuid.UUID,
    reservation_id: uuid.UUID,
    body: HotelReservationPatchIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> HotelReservationOut:
    """Corrige fechas, habitación o datos del huésped de una reserva que
    todavía no llegó. Cualquier persona con acceso al departamento puede
    hacerlo, igual que crearla.
    """
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    reservation = await repo.get_hotel_reservation(session, reservation_id)
    if reservation is None or reservation.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La reserva no existe")
    if reservation.status not in {"pending", "confirmed"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Solo se puede editar una reserva pendiente o confirmada",
        )

    check_in = body.check_in if body.check_in is not None else reservation.check_in
    check_out = body.check_out if body.check_out is not None else reservation.check_out
    if check_out <= check_in:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La salida debe ser posterior a la entrada",
        )

    room = reservation.room
    if body.room_id is not None and body.room_id != reservation.room_id:
        room = await repo.get_hotel_room(session, body.room_id)
        if room is None or room.department_id != department.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="La habitación no existe"
            )
        if not room.room_type.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="La categoría de esta habitación está retirada",
            )
        if room.status != "available":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="La habitación no está disponible (en mantenimiento o fuera de servicio)",
            )

    if await repo.hotel_room_has_overlap(
        session,
        room_id=room.id,
        check_in=check_in,
        check_out=check_out,
        exclude_reservation_id=reservation.id,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La habitación ya está reservada en esas fechas",
        )
    if body.contact_id is not None:
        contact = await repo.get_contact(session, body.contact_id)
        if contact is None or contact.tenant_id != department.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="El contacto no existe"
            )

    await repo.update_hotel_reservation(
        session,
        reservation=reservation,
        room_id=room.id,
        check_in=check_in,
        check_out=check_out,
        guest_name=body.guest_name,
        guest_phone=body.guest_phone,
        guest_email=body.guest_email,
        contact_id=body.contact_id,
        guests=body.guests,
        nightly_price_cents=body.nightly_price_cents,
        currency=body.currency,
        notes=body.notes,
    )
    # Por si cambió de habitación: se evita una carga perezosa, que en una
    # sesión async fallaría al serializar la respuesta.
    reservation.room = room
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_reservation_updated",
        subject_type="hotel_reservation",
        subject_id=str(reservation.id),
        detail={
            "room": room.code,
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
        },
    )
    return _hotel_reservation_out(reservation)


#: A qué estados puede pasar cada estado de una reserva. Un intento de salto no
#: contemplado —de ``pending`` a ``checked_out`` sin pasar por ``confirmed``,
#: por ejemplo— recibe un error claro en vez de dejar un dato inconsistente.
HOTEL_RESERVATION_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"confirmed", "cancelled"},
    "confirmed": {"checked_in", "cancelled", "no_show"},
    "checked_in": {"checked_out"},
    "checked_out": set(),
    "cancelled": set(),
    "no_show": set(),
}


@router.put(
    "/departments/{department_id}/hotel/reservations/{reservation_id}/status",
    response_model=HotelReservationOut,
)
async def set_hotel_reservation_status(
    department_id: uuid.UUID,
    reservation_id: uuid.UUID,
    body: HotelReservationStatusIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> HotelReservationOut:
    """Cambia el estado de una reserva: confirmar, check-in, check-out, cancelar…"""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    reservation = await repo.get_hotel_reservation(session, reservation_id)
    if reservation is None or reservation.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La reserva no existe")
    previous_status = reservation.status
    allowed = HOTEL_RESERVATION_TRANSITIONS.get(previous_status, set())
    if body.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No se puede pasar de «{previous_status}» a «{body.status}»",
        )
    await repo.set_hotel_reservation_status(session, reservation=reservation, status=body.status)
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_reservation_status_changed",
        subject_type="hotel_reservation",
        subject_id=str(reservation.id),
        detail={"from": previous_status, "to": body.status},
    )
    return _hotel_reservation_out(reservation)


@router.get("/departments/{department_id}/hotel/contacts", response_model=list[HotelContactOut])
async def search_hotel_contacts(
    department_id: uuid.UUID,
    q: str,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> list[HotelContactOut]:
    """Busca un contacto ya conocido por nombre, teléfono o correo, para
    vincularlo a una reserva sin volver a escribir sus datos a mano.
    """
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    if not q.strip():
        return []
    contacts = await repo.search_contacts(session, tenant_id=department.tenant_id, search=q)
    return [
        HotelContactOut(
            id=c.id,
            display_name=c.display_name,
            primary_phone=c.primary_phone,
            primary_email=c.primary_email,
        )
        for c in contacts
    ]


@router.get("/departments/{department_id}/hotel/report", response_model=HotelReportOut)
async def hotel_report(
    department_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> HotelReportOut:
    """Resumen operativo: llegadas y salidas de hoy, ocupación e ingresos de
    los próximos 30 días, en el huso horario del departamento.
    """
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    today = utcnow().astimezone(resolve_timezone(department.timezone)).date()
    report = await repo.hotel_department_report(session, department_id=department.id, today=today)
    return HotelReportOut(
        reference_date=report["reference_date"].isoformat(),
        arrivals_today=report["arrivals_today"],
        departures_today=report["departures_today"],
        occupied_rooms=report["occupied_rooms"],
        total_rooms=report["total_rooms"],
        pending_count=report["pending_count"],
        revenue_next_30_days=[HotelRevenueOut(**row) for row in report["revenue_by_currency"]],
    )
