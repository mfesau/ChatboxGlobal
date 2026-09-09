"""Alta, baja y consulta de las reglas de automatización.

Reservado a administración: una regla mal escrita afecta a todas las
conversaciones que entren, no a una.

También vive aquí la programación de un saliente para más tarde, que es la otra
mitad de lo mismo: automatizar es decidir de antemano qué pasa, y a veces
también cuándo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AdminDep, PrincipalDep, SessionDep, SettingsDep, SupervisorDep
from app.core import rules
from app.core.envelope import OutboundMessage
from app.db import repositories as repo
from app.db.models import AutomationRule, ScheduledMessage
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["automatización"])

#: Cuánto se admite programar hacia adelante. Más allá, lo que se quiere no es
#: un seguimiento sino un recordatorio de calendario, que es otra herramienta.
MAX_SCHEDULE_DAYS = 90


class RuleOut(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool
    priority: int
    department_id: uuid.UUID | None
    conditions: dict[str, Any]
    actions: list[Any]
    created_by: str | None
    created_at: str


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    department_id: uuid.UUID | None = None
    is_active: bool = True
    priority: int = Field(default=100, ge=0, le=10_000)
    conditions: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)


class RuleUpdateIn(BaseModel):
    """Solo se aplican los campos recibidos; ausente significa «no tocar»."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    department_id: uuid.UUID | None = None
    is_active: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10_000)
    conditions: dict[str, Any] | None = None
    actions: list[dict[str, Any]] | None = None


def _out(rule: AutomationRule) -> RuleOut:
    return RuleOut(
        id=rule.id,
        name=rule.name,
        is_active=rule.is_active,
        priority=rule.priority,
        department_id=rule.department_id,
        conditions=rule.conditions,
        actions=rule.actions,
        created_by=rule.created_by,
        created_at=rule.created_at.isoformat(),
    )


@router.get("/rules", response_model=list[RuleOut])
async def list_rules(
    session: SessionDep, settings: SettingsDep, principal: SupervisorDep
) -> list[RuleOut]:
    """Las reglas del inquilino, en el orden en que se evalúan.

    Abierta a supervisión: quien ve una conversación etiquetada o derivada sola
    necesita poder averiguar por qué, y aquí no hay ningún dato de nadie.
    """
    tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
    stmt = (
        select(AutomationRule)
        .where(AutomationRule.tenant_id == tenant.id)
        .order_by(AutomationRule.priority, AutomationRule.created_at)
    )
    return [_out(fila) for fila in (await session.execute(stmt)).scalars()]


@router.post("/rules", status_code=status.HTTP_201_CREATED, response_model=RuleOut)
async def create_rule(
    body: RuleIn, session: SessionDep, settings: SettingsDep, principal: AdminDep
) -> RuleOut:
    """Crea una regla. Reservado a administración."""
    tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
    await _check_department(session, tenant.id, body.department_id)
    _validate(body.conditions, body.actions)

    rule = AutomationRule(
        tenant_id=tenant.id,
        department_id=body.department_id,
        name=body.name.strip(),
        is_active=body.is_active,
        priority=body.priority,
        conditions=body.conditions,
        actions=body.actions,
        created_by=principal.audit_actor,
    )
    session.add(rule)
    await session.flush()

    await repo.record_audit(
        session,
        tenant_id=tenant.id,
        actor=principal.audit_actor,
        action="automation_rule_created",
        subject_type="automation_rule",
        subject_id=str(rule.id),
        detail={"name": rule.name},
    )
    return _out(rule)


@router.patch("/rules/{rule_id}", response_model=RuleOut)
async def update_rule(
    rule_id: uuid.UUID,
    body: RuleUpdateIn,
    session: SessionDep,
    principal: AdminDep,
) -> RuleOut:
    """Cambia una regla. Reservado a administración."""
    rule = await session.get(AutomationRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Regla no encontrada")

    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Nada para actualizar"
        )
    if "department_id" in changes:
        await _check_department(session, rule.tenant_id, changes["department_id"])

    # Se valida el resultado, no lo que llegó: cambiar solo las acciones puede
    # dejarlas incoherentes con las condiciones que ya estaban guardadas.
    _validate(
        changes.get("conditions", rule.conditions), changes.get("actions", rule.actions)
    )
    for campo, valor in changes.items():
        setattr(rule, campo, valor.strip() if campo == "name" else valor)

    await repo.record_audit(
        session,
        tenant_id=rule.tenant_id,
        actor=principal.audit_actor,
        action="automation_rule_updated",
        subject_type="automation_rule",
        subject_id=str(rule.id),
        detail={"campos": sorted(changes)},
    )
    return _out(rule)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: uuid.UUID, session: SessionDep, principal: AdminDep
) -> None:
    """Borra una regla. Reservado a administración."""
    rule = await session.get(AutomationRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Regla no encontrada")

    await repo.record_audit(
        session,
        tenant_id=rule.tenant_id,
        actor=principal.audit_actor,
        action="automation_rule_deleted",
        subject_type="automation_rule",
        subject_id=str(rule.id),
        detail={"name": rule.name},
    )
    await session.delete(rule)


# --------------------------------------------------------------------------- #
# Mensajes programados
# --------------------------------------------------------------------------- #
class ScheduledOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    text: str | None
    send_at: str
    status: str
    created_by: str | None


class ScheduleIn(BaseModel):
    text: str = Field(min_length=1, max_length=4_000)
    send_at: datetime


@router.get("/conversations/{conversation_id}/scheduled", response_model=list[ScheduledOut])
async def list_scheduled(
    conversation_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> list[ScheduledOut]:
    """Lo que queda por salir en esta conversación."""
    stmt = (
        select(ScheduledMessage)
        .where(ScheduledMessage.conversation_id == conversation_id)
        .order_by(ScheduledMessage.send_at)
    )
    return [
        ScheduledOut(
            id=fila.id,
            conversation_id=fila.conversation_id,
            text=(fila.payload or {}).get("text"),
            send_at=fila.send_at.isoformat(),
            status=fila.status,
            created_by=fila.created_by,
        )
        for fila in (await session.execute(stmt)).scalars()
    ]


@router.post(
    "/conversations/{conversation_id}/scheduled",
    status_code=status.HTTP_201_CREATED,
    response_model=ScheduledOut,
)
async def schedule_message(
    conversation_id: uuid.UUID,
    body: ScheduleIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> ScheduledOut:
    """Programa un mensaje para más tarde en esta conversación.

    Cualquier agente puede hacerlo sobre una conversación que ya puede ver: es
    lo mismo que escribir, con la hora aparte. Si el cliente contesta antes, el
    programado se cancela solo (ver ``app/core/orchestrator.py``).
    """
    conversation = await repo.get_conversation(session, conversation_id)
    if conversation is None or not principal.can_access(conversation):
        # 404 y no 403, como en el resto de la consola: confirmar que existe
        # ya diría algo de la cartera de un compañero.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversación no encontrada"
        )

    cuando = body.send_at if body.send_at.tzinfo else body.send_at.replace(tzinfo=UTC)
    ahora = datetime.now(UTC)
    if cuando <= ahora:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La hora de envío ya pasó",
        )
    if (cuando - ahora).days > MAX_SCHEDULE_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No se puede programar a más de {MAX_SCHEDULE_DAYS} días",
        )

    item = await repo.schedule_message(
        session,
        conversation=conversation,
        outbound=OutboundMessage(text=body.text),
        send_at=cuando,
        created_by=principal.audit_actor,
    )
    return ScheduledOut(
        id=item.id,
        conversation_id=item.conversation_id,
        text=body.text,
        send_at=item.send_at.isoformat(),
        status=item.status,
        created_by=item.created_by,
    )


@router.delete("/scheduled/{scheduled_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_scheduled(
    scheduled_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> None:
    """Cancela un programado que todavía no salió."""
    item = await session.get(ScheduledMessage, scheduled_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontrado")

    conversation = await repo.get_conversation(session, item.conversation_id)
    if conversation is None or not principal.can_access(conversation):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontrado")
    if item.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ese mensaje ya salió o se canceló"
        )
    item.status = "cancelled"


# --------------------------------------------------------------------------- #
# Auxiliares
# --------------------------------------------------------------------------- #
def _validate(conditions: dict[str, Any], actions: list[Any]) -> None:
    try:
        rules.validate(conditions, actions)
    except rules.RuleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


async def _check_department(
    session: Any, tenant_id: uuid.UUID, department_id: uuid.UUID | None
) -> None:
    if department_id is None:
        return
    department = await repo.get_department(session, department_id)
    if department is None or department.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
        )
