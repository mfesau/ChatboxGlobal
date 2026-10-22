"""Piezas que usan varios temas de la consola: dependencias, ayudantes y avisos."""

from __future__ import annotations

import uuid
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, HTTPException, status

from app.api.console.schemas import ConversationOut, DepartmentOut, LabelOut, Scope, TransferIn
from app.api.deps import (
    Principal,
    authorized_conversation,
)
from app.core.envelope import (
    utcnow,
)
from app.core.hub import agent_topic, conversation_topic, hub, inbox_topic
from app.db import repositories as repo
from app.db.models import (
    Agent,
    Conversation,
    Department,
    Tenant,
)
from app.logging_setup import get_logger

log = get_logger(__name__)

ConversationDep = Annotated[Conversation, Depends(authorized_conversation)]


async def _transfer_to_department(
    conversation: Conversation, body: TransferIn, session: Any, principal: Principal
) -> dict[str, Any]:
    department = await repo.get_department(session, body.to_department_id)
    if (
        department is None
        or department.tenant_id != conversation.tenant_id
        or not department.is_active
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El departamento no existe o está inactivo",
        )
    if department.id == conversation.department_id and conversation.assignee_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La conversación ya está en la cola de ese departamento",
        )

    entry = await repo.transfer_to_department(
        session,
        conversation=conversation,
        department_id=department.id,
        by_agent_id=principal.id,
        note=body.note,
    )
    if body.note:
        await repo.add_internal_note(
            session,
            conversation=conversation,
            agent_id=principal.id,
            body=f"Derivada al departamento {department.name}: {body.note}",
        )
    await repo.record_audit(
        session,
        tenant_id=conversation.tenant_id,
        actor=principal.audit_actor,
        action="conversation_transferred_to_department",
        subject_type="conversation",
        subject_id=str(conversation.id),
        detail={"department": department.name, "note": body.note},
    )
    await _notify_assignment(session, conversation, entry, principal, department=department)
    log.info(
        "conversation_transferred_to_department",
        conversation_id=str(conversation.id),
        by=principal.label,
        department=department.name,
    )
    return {
        "status": "ok",
        "assignee_id": None,
        "assignee_name": None,
        "department_id": str(department.id),
        "department_name": department.name,
        "control": conversation.control,
    }


#: Lo que el equipo marca a mano, y su estado en la base. Los nombres de los
#: dos extremos vienen de antes de que existiera el intermedio.
WORK_STATES: dict[str, str] = {
    "pending": "open",
    "in_progress": "in_progress",
    "solved": "closed",
}


def _label_out(row: Any) -> LabelOut:
    return LabelOut(id=row.id, name=row.name, color=row.color, department_id=row.department_id)


# --------------------------------------------------------------------------- #
# Auxiliares
# --------------------------------------------------------------------------- #
def _effective_scope(requested: Scope | None, principal: Principal) -> str:
    if principal.is_supervisor:
        return requested or "all"
    if requested in (None, "all"):
        # Un agente no obtiene la vista global; se le da lo que sí puede ver.
        return "mine_or_unassigned"
    return requested


def _require_known_timezone(name: str | None) -> None:
    """Rechaza una zona inexistente al guardarla.

    Se comprueba ahora para no descubrirla de noche, con un cliente esperando
    del otro lado: en ese momento el cálculo caería a UTC en silencio.
    """
    if not name:
        return
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Zona horaria desconocida: {name}",
        ) from exc


def _work_state(status: str) -> str:
    """Del estado guardado al nombre que ve el equipo."""
    for name, stored in WORK_STATES.items():
        if stored == status:
            return name
    # ``snoozed`` y cualquier otro heredado cuentan como pendientes: nadie
    # los da por resueltos.
    return "pending"


def _sla_status(row: Conversation) -> str | None:
    """Estado del objetivo de primera respuesta, para pintarlo en la bandeja.

    Se marca vencida en cuanto pasó la hora, sin esperar al repaso periódico:
    la consola debe mostrarlo al instante y no hasta cinco minutos después.
    """
    if row.first_response_at is not None:
        return "met" if row.sla_breached_at is None else "breached"
    if row.first_response_due_at is None:
        return None
    # ``as_utc`` porque SQLite, el motor de las pruebas, devuelve la fecha sin
    # zona y compararla con ``utcnow()`` reventaría.
    if row.sla_breached_at is not None or repo.as_utc(row.first_response_due_at) <= utcnow():
        return "breached"
    return "pending"


def _department_out(row: Department) -> DepartmentOut:
    return DepartmentOut(
        id=row.id,
        name=row.name,
        is_active=row.is_active,
        business_hours=dict(row.business_hours or {}),
        timezone=row.timezone,
        out_of_hours_message=row.out_of_hours_message,
        first_response_target_minutes=row.first_response_target_minutes,
        # Se compone aquí y no se guarda: así cambiar el esquema de rutas no
        # exige tocar una sola fila.
        logo_url=f"/api/departments/{row.id}/logo" if row.logo_path else None,
        # La consola lo necesita para saber qué pestaña de departamento lleva
        # el botón de reservas; el acceso real lo sigue decidiendo el servidor
        # en cada operación del módulo (ver _require_hotel_department).
        hotel_enabled=repo.hotel_module_enabled(row),
    )


def _conversation_out(row: Conversation) -> ConversationOut:
    return ConversationOut(
        id=row.id,
        channel=str(row.channel),
        status=row.status,
        control=row.control,
        subject=row.subject,
        contact_name=row.contact.display_name if row.contact else None,
        assignee_id=row.assignee_id,
        assignee_name=row.assignee.label if row.assignee else None,
        department_id=row.department_id,
        department_name=row.department.name if row.department else None,
        unread_count=row.unread_count,
        work_state=_work_state(row.status),
        last_message_at=row.last_message_at.isoformat() if row.last_message_at else None,
        sla_status=_sla_status(row),
        sla_due_at=row.first_response_due_at.isoformat() if row.first_response_due_at else None,
        labels=[
            _label_out(label) for label in row.labels
        ],
    )


async def _agent_names(session: Any, tenant_id: uuid.UUID) -> dict[uuid.UUID | None, str]:
    agents = await repo.list_agents(session, tenant_id=tenant_id, only_active=False)
    return {agent.id: agent.label for agent in agents}


async def _department_names(session: Any, tenant_id: uuid.UUID) -> dict[uuid.UUID | None, str]:
    departments = await repo.list_departments(session, tenant_id=tenant_id, only_active=False)
    return {department.id: department.name for department in departments}


async def _notify_assignment(
    session: Any,
    conversation: Conversation,
    entry: Any,
    principal: Principal,
    target: Agent | None = None,
    department: Department | None = None,
) -> None:
    """Avisa a las personas implicadas y, si procede, a toda la cola común.

    Cada tema recibe solo lo que le concierne. Una derivación entre dos agentes
    no altera la cola común, de modo que no se publica allí: hacerlo obligaría a
    cada consola suscrita a los dos temas a refrescarse dos veces por el mismo
    hecho.
    """
    event = {
        "type": "assignment",
        "action": entry.action,
        "conversation_id": str(conversation.id),
        "assignee_id": str(conversation.assignee_id) if conversation.assignee_id else None,
        "assignee_name": target.label if target else None,
        "department_id": str(department.id) if department else None,
        "department_name": department.name if department else None,
        "by": principal.label,
        "note": entry.note,
    }

    destinatarios = {
        str(agent_id)
        for agent_id in (entry.to_agent_id, entry.from_agent_id)
        if agent_id is not None
    }
    for agent_id in destinatarios:
        await hub.publish(agent_topic(agent_id), event)

    # Solo las acciones que cambian el contenido de la cola común —o la de un
    # departamento— interesan a quienes la vigilan.
    if entry.action in {"claim", "release", "close", "reopen", "transfer_department"}:
        tenant = await session.get(Tenant, conversation.tenant_id)
        await hub.publish(inbox_topic(tenant.slug if tenant else "default"), event)

    if entry.action in {"claim", "transfer", "release", "transfer_department"}:
        await _publish_control_change(
            conversation,
            control=conversation.control,
            assignee_name=target.label if target else None,
        )


async def _publish_state_change(conversation: Conversation, *, status: str) -> None:
    """Avisa al chatbox del cliente de que su conversación se cerró o se reabrió.

    Sin esto, marcarla como resuelta no cambiaba nada de lo que el cliente ve:
    seguía con el hilo abierto delante y podía escribir, reabriéndola sin que
    nadie se enterara.
    """
    await hub.publish(
        conversation_topic(conversation.channel_conversation_id),
        {"type": "state_changed", "status": status},
    )


async def _publish_control_change(
    conversation: Conversation, *, control: str, assignee_name: str | None
) -> None:
    """Avisa al chatbox del cliente para que el encabezado muestre quién le atiende."""
    await hub.publish(
        conversation_topic(conversation.channel_conversation_id),
        {"type": "control_changed", "control": control, "assignee_name": assignee_name},
    )
