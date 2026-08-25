"""Consola de equipo: cola común, cartera propia, derivaciones y supervisión.

Reglas de visibilidad, aplicadas en SQL y no en memoria:

* La **cola común** —conversaciones sin responsable— es el punto único por el que
  entra todo, sea de WhatsApp, de Microsoft Teams o del chatbox web. Cualquier
  agente la ve y puede tomar trabajo de ella.
* Un **agente** ve además su propia cartera. No ve la de sus compañeros.
* **Supervisión** ve la totalidad de las conversaciones del inquilino.

Derivar no mueve ni copia nada: la conversación es la misma fila y conserva su
historial íntegro. Solo cambia el responsable, y el cambio queda anotado en
``assignments`` con autor, destinatario y motivo.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from app.api.deps import (
    AdminDep,
    OrchestratorDep,
    Principal,
    PrincipalDep,
    SessionDep,
    SettingsDep,
    SupervisorDep,
    authorized_conversation,
)
from app.core.envelope import Attachment, ChannelKind, ContentType, Direction, OutboundMessage
from app.core.hub import agent_topic, conversation_topic, hub, inbox_topic
from app.core.secrets import EncryptionNotConfiguredError, encrypt_json
from app.core.security import WeakPasswordError, hash_password
from app.core.storage import save_upload
from app.db import repositories as repo
from app.db.models import (
    ROLE_ADMIN,
    ROLE_AGENT,
    Agent,
    ChannelAccount,
    Conversation,
    Department,
    Tenant,
)
from app.handlers.builtin import FallbackHandler
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["consola"])

ConversationDep = Annotated[Conversation, Depends(authorized_conversation)]
Scope = Literal["unassigned", "mine", "mine_or_unassigned", "all"]


# --------------------------------------------------------------------------- #
# Esquemas
# --------------------------------------------------------------------------- #
class ConversationOut(BaseModel):
    id: uuid.UUID
    channel: str
    status: str
    control: str
    subject: str | None
    contact_name: str | None
    assignee_id: uuid.UUID | None
    assignee_name: str | None
    department_id: uuid.UUID | None
    department_name: str | None
    unread_count: int
    last_message_at: str | None


class MessageOut(BaseModel):
    id: uuid.UUID
    direction: str
    author_type: str
    content_type: str
    status: str
    text: str | None
    attachments: list[dict[str, Any]]
    created_at: str | None


class AssignmentOut(BaseModel):
    id: uuid.UUID
    action: str
    from_agent: str | None
    to_agent: str | None
    to_department: str | None
    by_agent: str | None
    note: str | None
    created_at: str


class DepartmentOut(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool


class DepartmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class AgentDepartmentsIn(BaseModel):
    department_id: uuid.UUID | None = None
    extra_department_ids: list[uuid.UUID] = Field(default_factory=list)


class NoteOut(BaseModel):
    id: uuid.UUID
    agent: str | None
    body: str
    created_at: str


class ContactCommentOut(BaseModel):
    id: uuid.UUID
    agent: str | None
    body: str
    created_at: str


class ContactDetailOut(BaseModel):
    id: uuid.UUID
    display_name: str | None
    primary_phone: str | None
    primary_email: str | None
    comments: list[ContactCommentOut]


class ContactUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=160)
    primary_phone: str | None = Field(default=None, max_length=32)
    primary_email: str | None = Field(default=None, max_length=254)


class ContactCommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=2_000)


class ContactSummaryOut(BaseModel):
    id: uuid.UUID
    display_name: str | None
    primary_phone: str | None
    primary_email: str | None
    is_blocked: bool
    conversation_count: int
    last_message_at: str | None


class ContactProfileOut(BaseModel):
    id: uuid.UUID
    display_name: str | None
    primary_phone: str | None
    primary_email: str | None
    is_blocked: bool
    comments: list[ContactCommentOut]
    conversations: list[ConversationOut]


class AgentOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    role: str
    presence: str
    is_active: bool
    department_id: uuid.UUID | None
    extra_department_ids: list[uuid.UUID]


class AttachmentIn(BaseModel):
    content_type: str = "document"
    url: str | None = None
    mime_type: str | None = None
    filename: str | None = None
    size_bytes: int | None = None
    caption: str | None = None


class ReplyIn(BaseModel):
    text: str = Field(default="", max_length=8_000)
    quick_replies: list[dict[str, str]] = Field(default_factory=list)
    attachments: list[AttachmentIn] = Field(default_factory=list)


class TransferIn(BaseModel):
    #: Exactamente uno de los dos: a un compañero, o a la cola de un departamento.
    to_agent_id: uuid.UUID | None = None
    to_department_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=2_000)


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=4_000)
    mentions: list[uuid.UUID] = Field(default_factory=list)


class ReleaseIn(BaseModel):
    note: str | None = Field(default=None, max_length=2_000)


class ControlIn(BaseModel):
    control: Literal["bot", "human"]


class NewAgentIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    display_name: str | None = Field(default=None, max_length=160)
    role: Literal["agent", "supervisor", "admin"] = ROLE_AGENT
    password: str = Field(min_length=8, max_length=256)
    #: Departamento principal. Ninguno equivale a "generalista": atiende la
    #: cola sin departamento, no la de uno concreto salvo que se le otorgue.
    department_id: uuid.UUID | None = None


class PasswordIn(BaseModel):
    password: str = Field(min_length=8, max_length=256)


class AgentUpdateIn(BaseModel):
    #: Solo se aplican los campos recibidos; ``None`` significa "no tocar".
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    #: Permite reactivar una cuenta desactivada; ``DELETE`` sigue siendo el
    #: camino corto para desactivarla.
    is_active: bool | None = None


class AdminSettingsIn(BaseModel):
    fallback_message: str = Field(min_length=1, max_length=2_000)


# --------------------------------------------------------------------------- #
# Bandeja
# --------------------------------------------------------------------------- #
@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    scope: Scope | None = None,
    conversation_status: str | None = Query(default="open", alias="status"),
    channel: ChannelKind | None = None,
    department: uuid.UUID | None = None,
    tenant: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ConversationOut]:
    """Devuelve la bandeja acotada al alcance permitido.

    Sin ``scope`` explícito, supervisión recibe todo y un agente recibe su
    cartera más la cola común. Si un agente pide ``all``, se degrada su petición
    en lugar de rechazarla: la consola es la misma para todos los roles.
    ``department`` acota la vista a uno de los departamentos ya accesibles;
    no amplía lo que ``department_ids`` permite.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    effective = _effective_scope(scope, principal)

    conversations = await repo.list_conversations(
        session,
        tenant_id=tenant_row.id,
        status=conversation_status,
        channel=channel,
        scope=effective,
        agent_id=principal.id,
        department_ids=principal.department_ids,
        department=department,
        limit=limit,
        offset=offset,
    )
    return [_conversation_out(row) for row in conversations]


@router.get("/inbox/summary")
async def inbox_summary(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> dict[str, Any]:
    """Contadores de las pestañas: cola común, cartera propia y total."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    summary: dict[str, Any] = {
        "unassigned": await repo.count_conversations(
            session,
            tenant_id=tenant_row.id,
            scope="unassigned",
            department_ids=principal.department_ids,
        ),
        "mine": await repo.count_conversations(
            session, tenant_id=tenant_row.id, scope="mine", agent_id=principal.id
        )
        if principal.id
        else 0,
        "is_supervisor": principal.is_supervisor,
    }
    if principal.is_supervisor:
        summary["all"] = await repo.count_conversations(session, tenant_id=tenant_row.id)
    return summary


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation: ConversationDep,
    session: SessionDep,
    limit: int = Query(default=200, ge=1, le=1_000),
) -> list[MessageOut]:
    """Historial completo del hilo, con independencia de cuántas veces se derivó."""
    messages = await repo.recent_messages(session, conversation.id, limit=limit)
    conversation.unread_count = 0
    return [
        MessageOut(
            id=row.id,
            direction="inbound" if row.direction is Direction.INBOUND else "outbound",
            author_type=row.author_type,
            content_type=str(row.content_type),
            status=str(row.status),
            text=row.text,
            attachments=list(row.attachments or []),
            created_at=row.created_at.isoformat() if row.created_at else None,
        )
        for row in messages
    ]


@router.post("/conversations/{conversation_id}/reply", status_code=status.HTTP_202_ACCEPTED)
async def reply_as_agent(
    conversation: ConversationDep,
    body: ReplyIn,
    session: SessionDep,
    principal: PrincipalDep,
    orchestrator: OrchestratorDep,
) -> dict[str, str]:
    """Responde al cliente por el canal de origen del hilo.

    Quien responde una conversación de la cola común la asume de forma
    implícita: evita que dos personas contesten a la vez y ahorra un paso en la
    interfaz.
    """
    if (
        principal.agent is not None
        and not principal.is_supervisor
        and conversation.assignee_id not in (None, principal.agent.id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La conversación la atiende otro compañero",
        )
    if not body.text and not body.attachments:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La respuesta necesita texto o al menos un adjunto",
        )

    attachments = [Attachment.from_dict(a.model_dump()) for a in body.attachments]
    content_type = (
        attachments[0].content_type if attachments and not body.text else ContentType.TEXT
    )
    outbox_id = await orchestrator.send_from_agent(
        conversation_id=conversation.id,
        outbound=OutboundMessage(
            text=body.text or None,
            quick_replies=body.quick_replies,
            attachments=attachments,
            content_type=content_type,
        ),
        agent_id=principal.id,
        # La misma transacción de la petición: ni una segunda conexión ni riesgo
        # de interbloqueo, y la respuesta se confirma junto al resto del turno.
        session=session,
    )
    if outbox_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversación no encontrada"
        )
    return {"status": "queued", "outbox_id": str(outbox_id)}


# --------------------------------------------------------------------------- #
# Adjuntos
# --------------------------------------------------------------------------- #
@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile,
    principal: PrincipalDep,
    settings: SettingsDep,
    session: SessionDep,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Sube una imagen para adjuntarla a la próxima respuesta de un agente."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    attachment = await save_upload(file, namespace=str(tenant_row.id), settings=settings)
    return attachment.to_dict()


@router.post("/conversations/{conversation_id}/reopen")
async def reopen_conversation(
    conversation: ConversationDep, session: SessionDep, principal: PrincipalDep
) -> dict[str, str]:
    """Reabre un hilo cerrado sin perder nada de lo conversado."""
    conversation.status = "open"
    await repo.record_assignment(
        session,
        conversation=conversation,
        action="reopen",
        to_agent_id=conversation.assignee_id,
        by_agent_id=principal.id,
    )
    return {"status": "ok", "conversation_status": "open"}


# --------------------------------------------------------------------------- #
# Derivación entre compañeros
# --------------------------------------------------------------------------- #
@router.post("/conversations/{conversation_id}/claim")
async def claim_conversation(
    conversation: ConversationDep, session: SessionDep, principal: PrincipalDep
) -> dict[str, Any]:
    """Toma una conversación de la cola común."""
    if principal.agent is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La clave de servicio no puede atender conversaciones; inicie sesión",
        )
    if conversation.assignee_id not in (None, principal.agent.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Otro compañero ya la está atendiendo",
        )

    entry = await repo.record_assignment(
        session,
        conversation=conversation,
        action="claim",
        to_agent_id=principal.agent.id,
        by_agent_id=principal.agent.id,
    )
    await _notify_assignment(session, conversation, entry, principal, target=principal.agent)
    return {
        "status": "ok",
        "assignee_id": str(principal.agent.id),
        "assignee_name": principal.agent.label,
        "control": conversation.control,
    }


@router.post("/conversations/{conversation_id}/transfer")
async def transfer_conversation(
    conversation: ConversationDep,
    body: TransferIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """Deriva la conversación a un compañero, o a la cola de un departamento.

    Un agente solo puede derivar lo que atiende; supervisión puede reasignar
    cualquier conversación. El motivo, si se indica, queda además como nota
    interna, de modo que quien la recibe entiende el contexto sin preguntar.
    """
    if (body.to_agent_id is None) == (body.to_department_id is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Elija un compañero o un departamento, no ambos ni ninguno",
        )
    if (
        principal.agent is not None
        and not principal.is_supervisor
        and conversation.assignee_id not in (None, principal.agent.id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo puede derivar las conversaciones que atiende",
        )

    if body.to_department_id is not None:
        return await _transfer_to_department(conversation, body, session, principal)

    target = await repo.get_agent(session, body.to_agent_id)
    if target is None or target.tenant_id != conversation.tenant_id or not target.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El destinatario no existe o está inactivo",
        )
    if target.id == conversation.assignee_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="La conversación ya es suya"
        )

    entry = await repo.record_assignment(
        session,
        conversation=conversation,
        action="transfer",
        to_agent_id=target.id,
        by_agent_id=principal.id,
        note=body.note,
    )
    if body.note:
        await repo.add_internal_note(
            session,
            conversation=conversation,
            agent_id=principal.id,
            body=f"Derivada a {target.label}: {body.note}",
            mentions=[str(target.id)],
        )
    await repo.record_audit(
        session,
        tenant_id=conversation.tenant_id,
        actor=principal.audit_actor,
        action="conversation_transferred",
        subject_type="conversation",
        subject_id=str(conversation.id),
        detail={"to": target.email, "note": body.note},
    )
    await _notify_assignment(session, conversation, entry, principal, target=target)
    log.info(
        "conversation_transferred",
        conversation_id=str(conversation.id),
        by=principal.label,
        to=target.email,
    )
    return {
        "status": "ok",
        "assignee_id": str(target.id),
        "assignee_name": target.label,
        "control": conversation.control,
    }


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


@router.post("/conversations/{conversation_id}/release")
async def release_conversation(
    conversation: ConversationDep,
    body: ReleaseIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """Devuelve la conversación a la cola común y reactiva al asistente."""
    if (
        principal.agent is not None
        and not principal.is_supervisor
        and conversation.assignee_id != principal.agent.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo puede liberar las conversaciones que atiende",
        )

    entry = await repo.record_assignment(
        session,
        conversation=conversation,
        action="release",
        to_agent_id=None,
        by_agent_id=principal.id,
        note=body.note,
    )
    await _notify_assignment(session, conversation, entry, principal)
    return {
        "status": "ok",
        "assignee_id": None,
        "assignee_name": None,
        "control": conversation.control,
    }


@router.get("/conversations/{conversation_id}/assignments", response_model=list[AssignmentOut])
async def assignment_history(
    conversation: ConversationDep, session: SessionDep
) -> list[AssignmentOut]:
    """Traza de derivaciones: quién la atendió, quién la pasó y por qué."""
    entries = await repo.assignment_history(session, conversation.id)
    names = await _agent_names(session, conversation.tenant_id)
    department_names = await _department_names(session, conversation.tenant_id)
    return [
        AssignmentOut(
            id=entry.id,
            action=entry.action,
            from_agent=names.get(entry.from_agent_id),
            to_agent=names.get(entry.to_agent_id),
            to_department=department_names.get(entry.to_department_id),
            by_agent=names.get(entry.by_agent_id),
            note=entry.note,
            created_at=entry.created_at.isoformat(),
        )
        for entry in entries
    ]


# --------------------------------------------------------------------------- #
# Notas internas
# --------------------------------------------------------------------------- #
@router.get("/conversations/{conversation_id}/notes", response_model=list[NoteOut])
async def list_notes(conversation: ConversationDep, session: SessionDep) -> list[NoteOut]:
    notes = await repo.list_internal_notes(session, conversation.id)
    names = await _agent_names(session, conversation.tenant_id)
    return [
        NoteOut(
            id=note.id,
            agent=names.get(note.agent_id),
            body=note.body,
            created_at=note.created_at.isoformat(),
        )
        for note in notes
    ]


@router.post("/conversations/{conversation_id}/notes", status_code=status.HTTP_201_CREATED)
async def add_note(
    conversation: ConversationDep,
    body: NoteIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> dict[str, str]:
    """Añade una anotación visible solo para el equipo; el cliente no la recibe."""
    note = await repo.add_internal_note(
        session,
        conversation=conversation,
        agent_id=principal.id,
        body=body.body,
        mentions=[str(mention) for mention in body.mentions],
    )
    for mentioned in body.mentions:
        await hub.publish(
            agent_topic(str(mentioned)),
            {
                "type": "mentioned",
                "conversation_id": str(conversation.id),
                "by": principal.label,
                "body": body.body[:200],
            },
        )
    return {"status": "ok", "note_id": str(note.id)}


# --------------------------------------------------------------------------- #
# Datos del contacto
# --------------------------------------------------------------------------- #
@router.get("/conversations/{conversation_id}/contact", response_model=ContactDetailOut)
async def get_contact_detail(
    conversation: ConversationDep, session: SessionDep
) -> ContactDetailOut:
    """Ficha del contacto: nombre, teléfono, correo e historial de comentarios.

    Cualquier agente con acceso a la conversación puede consultarla; editarla
    queda reservado a supervisión y administración (ver ``update_contact_detail``).
    """
    if conversation.contact_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esta conversación no tiene contacto asociado",
        )
    contact = await repo.get_contact(session, conversation.contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contacto no encontrado")

    comments = await repo.list_contact_comments(session, conversation.contact_id)
    names = await _agent_names(session, conversation.tenant_id)
    return ContactDetailOut(
        id=contact.id,
        display_name=contact.display_name,
        primary_phone=contact.primary_phone,
        primary_email=contact.primary_email,
        comments=[
            ContactCommentOut(
                id=comment.id,
                agent=names.get(comment.agent_id),
                body=comment.body,
                created_at=comment.created_at.isoformat(),
            )
            for comment in comments
        ],
    )


@router.patch("/conversations/{conversation_id}/contact")
async def update_contact_detail(
    conversation: ConversationDep,
    body: ContactUpdateIn,
    session: SessionDep,
    principal: SupervisorDep,
) -> dict[str, str]:
    """Edita nombre, teléfono o correo del contacto. Reservado a supervisión."""
    if conversation.contact_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esta conversación no tiene contacto asociado",
        )
    changes = body.model_dump(exclude_unset=True)
    if "primary_email" in changes and changes["primary_email"]:
        existing = await repo.find_contact_by_email(
            session, tenant_id=conversation.tenant_id, email=changes["primary_email"]
        )
        if existing is not None and existing.id != conversation.contact_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe otro contacto con ese correo",
            )

    await repo.update_contact(session, contact_id=conversation.contact_id, **changes)
    await repo.record_audit(
        session,
        tenant_id=conversation.tenant_id,
        actor=principal.audit_actor,
        action="contact_updated",
        subject_type="contact",
        subject_id=str(conversation.contact_id),
        detail=changes,
    )
    return {"status": "ok"}


@router.post(
    "/conversations/{conversation_id}/contact/comments", status_code=status.HTTP_201_CREATED
)
async def add_contact_comment(
    conversation: ConversationDep,
    body: ContactCommentIn,
    session: SessionDep,
    principal: SupervisorDep,
) -> dict[str, str]:
    """Añade un comentario al historial del contacto. Reservado a supervisión."""
    if conversation.contact_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esta conversación no tiene contacto asociado",
        )
    comment = await repo.add_contact_comment(
        session,
        contact_id=conversation.contact_id,
        tenant_id=conversation.tenant_id,
        agent_id=principal.id,
        body=body.body,
    )
    return {"status": "ok", "comment_id": str(comment.id)}


# --------------------------------------------------------------------------- #
# Directorio de contactos
#
# A diferencia de "Datos del contacto" —accesible a cualquier agente, pero
# solo a través de una conversación propia—, este directorio muestra TODOS
# los contactos del inquilino de una sola vez, con su historial completo de
# conversaciones en cualquier canal. Por eso queda reservado a supervisión y
# administración, que de todos modos ya ven la totalidad de la bandeja.
# --------------------------------------------------------------------------- #
@router.get("/contacts", response_model=list[ContactSummaryOut])
async def list_contacts(
    session: SessionDep,
    settings: SettingsDep,
    principal: SupervisorDep,
    search: str | None = None,
    tenant: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ContactSummaryOut]:
    """Directorio completo de clientes, con cuántas conversaciones tiene cada
    uno y cuándo fue la última."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    rows = await repo.list_contacts(
        session, tenant_id=tenant_row.id, search=search, limit=limit, offset=offset
    )
    return [
        ContactSummaryOut(
            id=contact.id,
            display_name=contact.display_name,
            primary_phone=contact.primary_phone,
            primary_email=contact.primary_email,
            is_blocked=contact.is_blocked,
            conversation_count=count,
            last_message_at=last_message_at.isoformat() if last_message_at else None,
        )
        for contact, count, last_message_at in rows
    ]


@router.get("/contacts/{contact_id}", response_model=ContactProfileOut)
async def get_contact_profile(
    contact_id: uuid.UUID, session: SessionDep, principal: SupervisorDep
) -> ContactProfileOut:
    """Ficha completa: datos, comentarios y todas sus conversaciones."""
    contact = await repo.get_contact(session, contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contacto no encontrado")

    comments = await repo.list_contact_comments(session, contact_id)
    conversations = await repo.list_conversations_for_contact(session, contact_id)
    names = await _agent_names(session, contact.tenant_id)
    return ContactProfileOut(
        id=contact.id,
        display_name=contact.display_name,
        primary_phone=contact.primary_phone,
        primary_email=contact.primary_email,
        is_blocked=contact.is_blocked,
        comments=[
            ContactCommentOut(
                id=comment.id,
                agent=names.get(comment.agent_id),
                body=comment.body,
                created_at=comment.created_at.isoformat(),
            )
            for comment in comments
        ],
        conversations=[_conversation_out(row) for row in conversations],
    )


@router.patch("/contacts/{contact_id}")
async def update_contact_profile(
    contact_id: uuid.UUID, body: ContactUpdateIn, session: SessionDep, principal: SupervisorDep
) -> dict[str, str]:
    """Edita nombre, teléfono o correo desde el directorio. Reservado a
    supervisión."""
    contact = await repo.get_contact(session, contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contacto no encontrado")

    changes = body.model_dump(exclude_unset=True)
    if "primary_email" in changes and changes["primary_email"]:
        existing = await repo.find_contact_by_email(
            session, tenant_id=contact.tenant_id, email=changes["primary_email"]
        )
        if existing is not None and existing.id != contact_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe otro contacto con ese correo",
            )

    await repo.update_contact(session, contact_id=contact_id, **changes)
    await repo.record_audit(
        session,
        tenant_id=contact.tenant_id,
        actor=principal.audit_actor,
        action="contact_updated",
        subject_type="contact",
        subject_id=str(contact_id),
        detail=changes,
    )
    return {"status": "ok"}


@router.post("/contacts/{contact_id}/comments", status_code=status.HTTP_201_CREATED)
async def add_contact_profile_comment(
    contact_id: uuid.UUID, body: ContactCommentIn, session: SessionDep, principal: SupervisorDep
) -> dict[str, str]:
    """Añade un comentario desde el directorio. Reservado a supervisión."""
    contact = await repo.get_contact(session, contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contacto no encontrado")

    comment = await repo.add_contact_comment(
        session,
        contact_id=contact_id,
        tenant_id=contact.tenant_id,
        agent_id=principal.id,
        body=body.body,
    )
    return {"status": "ok", "comment_id": str(comment.id)}


# --------------------------------------------------------------------------- #
# Estado del hilo
# --------------------------------------------------------------------------- #
@router.post("/conversations/{conversation_id}/control")
async def set_control(
    conversation: ConversationDep,
    body: ControlIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> dict[str, str]:
    """Alterna entre atención automática y atención humana."""
    await repo.set_conversation_control(session, conversation.id, body.control)
    await repo.record_audit(
        session,
        tenant_id=conversation.tenant_id,
        actor=principal.audit_actor,
        action="control_changed",
        subject_type="conversation",
        subject_id=str(conversation.id),
        detail={"control": body.control},
    )
    assignee = (
        await repo.get_agent(session, conversation.assignee_id)
        if conversation.assignee_id
        else None
    )
    await _publish_control_change(
        conversation, control=body.control, assignee_name=assignee.label if assignee else None
    )
    return {"status": "ok", "control": body.control}


@router.post("/conversations/{conversation_id}/close")
async def close_conversation(
    conversation: ConversationDep, session: SessionDep, principal: PrincipalDep
) -> dict[str, str]:
    """Cierra el hilo. El historial permanece y se recupera al reabrirse."""
    conversation.status = "closed"
    await repo.record_assignment(
        session,
        conversation=conversation,
        action="close",
        to_agent_id=conversation.assignee_id,
        by_agent_id=principal.id,
    )
    return {"status": "ok", "conversation_status": "closed"}


# --------------------------------------------------------------------------- #
# Equipo
# --------------------------------------------------------------------------- #
@router.get("/agents", response_model=list[AgentOut])
async def list_agents(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> list[AgentOut]:
    """El equipo, activo e inactivo. Alimenta el desplegable de derivación y
    la tabla de administración; cada cliente filtra lo que no le sirve —por
    ejemplo, no se deriva a una cuenta desactivada—."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    return [
        AgentOut(
            id=agent.id,
            email=agent.email,
            display_name=agent.display_name,
            role=agent.role,
            presence=agent.presence,
            is_active=agent.is_active,
            department_id=agent.department_id,
            extra_department_ids=[d.id for d in agent.granted_departments],
        )
        for agent in await repo.list_agents(session, tenant_id=tenant_row.id, only_active=False)
    ]


@router.post("/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: NewAgentIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Da de alta un compañero. Reservado a supervisión."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    if await repo.find_agent_by_email(session, tenant_id=tenant_row.id, email=body.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe un agente con ese correo"
        )
    if body.department_id is not None:
        department = await repo.get_department(session, body.department_id)
        if department is None or department.tenant_id != tenant_row.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
            )
    try:
        # La derivación de la contraseña es intencionadamente costosa: se ejecuta
        # en un hilo para no bloquear el bucle de eventos.
        password_hash = await asyncio.to_thread(hash_password, body.password)
    except WeakPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    agent = await repo.create_agent(
        session,
        tenant_id=tenant_row.id,
        email=body.email,
        display_name=body.display_name,
        role=body.role,
        password_hash=password_hash,
        department_id=body.department_id,
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="agent_created",
        subject_type="agent",
        subject_id=str(agent.id),
        detail={"email": agent.email, "role": agent.role},
    )
    return {"status": "ok", "agent_id": str(agent.id), "email": agent.email}


@router.post("/agents/{agent_id}/password")
async def set_password(
    agent_id: uuid.UUID, body: PasswordIn, session: SessionDep, principal: AdminDep
) -> dict[str, str]:
    """Establece o restablece una contraseña. Reservado a administración."""
    agent = await repo.get_agent(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente no encontrado")

    try:
        agent.password_hash = await asyncio.to_thread(hash_password, body.password)
    except WeakPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return {"status": "ok"}


@router.patch("/agents/{agent_id}")
async def update_agent(
    agent_id: uuid.UUID, body: AgentUpdateIn, session: SessionDep, principal: AdminDep
) -> dict[str, str]:
    """Cambia el nombre visible de un agente, o reactiva una cuenta. Reservado
    a administración."""
    agent = await repo.get_agent(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente no encontrado")

    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Nada para actualizar"
        )
    if "display_name" in changes:
        agent.display_name = changes["display_name"].strip()
    if "is_active" in changes:
        agent.is_active = changes["is_active"]

    await repo.record_audit(
        session,
        tenant_id=agent.tenant_id,
        actor=principal.audit_actor,
        action="agent_updated",
        subject_type="agent",
        subject_id=str(agent.id),
        detail=changes,
    )
    return {"status": "ok"}


@router.delete("/agents/{agent_id}")
async def deactivate_agent(
    agent_id: uuid.UUID, session: SessionDep, principal: AdminDep
) -> dict[str, str]:
    """Desactiva una cuenta: no puede volver a iniciar sesión.

    No borra la fila: el nombre de la persona debe seguir apareciendo en el
    historial de derivaciones, notas y auditoría de antes de darla de baja.
    Reservado a administración.
    """
    agent = await repo.get_agent(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente no encontrado")
    if agent.id == principal.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="No puede desactivar su propia cuenta"
        )
    if agent.role == ROLE_ADMIN and agent.is_active:
        remaining = await repo.count_active_admins(session, tenant_id=agent.tenant_id)
        if remaining <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No puede desactivar al único administrador activo",
            )

    agent.is_active = False
    agent.presence = "offline"
    await repo.close_agent_sessions(session, agent.id)
    await repo.record_audit(
        session,
        tenant_id=agent.tenant_id,
        actor=principal.audit_actor,
        action="agent_deactivated",
        subject_type="agent",
        subject_id=str(agent.id),
    )
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Departamentos
# --------------------------------------------------------------------------- #
@router.get("/departments", response_model=list[DepartmentOut])
async def list_departments(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> list[DepartmentOut]:
    """Lista de departamentos. Cualquier persona autenticada la necesita para derivar."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    departments = await repo.list_departments(session, tenant_id=tenant_row.id)
    return [
        DepartmentOut(id=d.id, name=d.name, is_active=d.is_active) for d in departments
    ]


@router.post("/departments", status_code=status.HTTP_201_CREATED, response_model=DepartmentOut)
async def create_department(
    body: DepartmentIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> DepartmentOut:
    """Crea un departamento. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    if await repo.find_department_by_name(session, tenant_id=tenant_row.id, name=body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe un departamento con ese nombre"
        )
    department = await repo.create_department(session, tenant_id=tenant_row.id, name=body.name)
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="department_created",
        subject_type="department",
        subject_id=str(department.id),
        detail={"name": department.name},
    )
    return DepartmentOut(id=department.id, name=department.name, is_active=department.is_active)


@router.put("/agents/{agent_id}/departments")
async def set_agent_departments(
    agent_id: uuid.UUID, body: AgentDepartmentsIn, session: SessionDep, principal: AdminDep
) -> dict[str, Any]:
    """Fija el departamento principal y los adicionales de un agente.

    Reservado a administración: es quien "está por encima de todo" y decide
    qué colas puede atender cada persona, más allá de la suya.
    """
    agent = await repo.get_agent(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente no encontrado")

    for department_id in filter(None, [body.department_id, *body.extra_department_ids]):
        department = await repo.get_department(session, department_id)
        if department is None or department.tenant_id != agent.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Uno de los departamentos indicados no existe",
            )

    agent.department_id = body.department_id
    await repo.set_agent_departments(
        session, agent=agent, department_ids=body.extra_department_ids
    )
    await repo.record_audit(
        session,
        tenant_id=agent.tenant_id,
        actor=principal.audit_actor,
        action="agent_departments_updated",
        subject_type="agent",
        subject_id=str(agent.id),
        detail={
            "department_id": str(body.department_id) if body.department_id else None,
            "extra_department_ids": [str(d) for d in body.extra_department_ids],
        },
    )
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Cuentas de canal
#
# Da de alta, con departamento propio, tantos números de WhatsApp, páginas de
# Facebook o equipos de Teams como se quiera — sin esto, todos comparten la
# cola común, exactamente como antes de que existiera esta pantalla.
# --------------------------------------------------------------------------- #
class ChannelAccountOut(BaseModel):
    id: uuid.UUID
    channel: str
    external_id: str
    display_name: str | None
    is_active: bool
    department_id: uuid.UUID | None
    department_name: str | None
    #: Nunca se expone el token en sí, ni cifrado ni en claro.
    has_own_credentials: bool


class ChannelAccountIn(BaseModel):
    channel: Literal["whatsapp", "facebook", "msbot"]
    external_id: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=160)
    department_id: uuid.UUID | None = None
    #: Obligatorio en Facebook (cada página tiene el suyo); opcional en
    #: WhatsApp (si falta, se usa el token global de .env); ignorado en Teams.
    access_token: str | None = Field(default=None, max_length=4096)


class ChannelAccountUpdateIn(BaseModel):
    #: Solo se aplican los campos recibidos; ausente significa "no tocar".
    display_name: str | None = Field(default=None, max_length=160)
    department_id: uuid.UUID | None = None
    is_active: bool | None = None
    #: Cadena vacía = quitar el token propio y volver a la credencial global
    #: (solo tiene sentido en WhatsApp); ausente = no tocarlo.
    access_token: str | None = Field(default=None, max_length=4096)


def _channel_account_out(account: ChannelAccount) -> ChannelAccountOut:
    return ChannelAccountOut(
        id=account.id,
        channel=str(account.channel),
        external_id=account.external_id,
        display_name=account.display_name,
        is_active=account.is_active,
        department_id=account.department_id,
        department_name=account.department.name if account.department else None,
        has_own_credentials=bool(account.credentials_ciphertext),
    )


@router.get("/channel-accounts", response_model=list[ChannelAccountOut])
async def list_channel_accounts(
    session: SessionDep, settings: SettingsDep, principal: AdminDep, tenant: str | None = None
) -> list[ChannelAccountOut]:
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    accounts = await repo.list_channel_accounts(session, tenant_id=tenant_row.id)
    return [_channel_account_out(account) for account in accounts]


@router.post(
    "/channel-accounts", status_code=status.HTTP_201_CREATED, response_model=ChannelAccountOut
)
async def create_channel_account(
    body: ChannelAccountIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> ChannelAccountOut:
    """Alta manual de una cuenta de canal. Reservado a administración."""
    channel = ChannelKind(body.channel)
    if channel is ChannelKind.FACEBOOK and not body.access_token:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Facebook exige el token de acceso de la página",
        )

    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    if body.department_id is not None:
        department = await repo.get_department(session, body.department_id)
        if department is None or department.tenant_id != tenant_row.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
            )

    credentials_ciphertext = None
    if body.access_token and channel is not ChannelKind.MSBOT:
        try:
            credentials_ciphertext = encrypt_json(
                {"access_token": body.access_token}, settings=settings
            )
        except EncryptionNotConfiguredError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    account = await repo.create_channel_account(
        session,
        tenant_id=tenant_row.id,
        channel=channel,
        external_id=body.external_id,
        display_name=body.display_name,
        department_id=body.department_id,
        credentials_ciphertext=credentials_ciphertext,
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="channel_account_created",
        subject_type="channel_account",
        subject_id=str(account.id),
        detail={"channel": body.channel, "external_id": body.external_id},
    )
    account = await repo.get_channel_account(session, account.id)
    return _channel_account_out(account)


@router.patch("/channel-accounts/{account_id}", response_model=ChannelAccountOut)
async def update_channel_account(
    account_id: uuid.UUID,
    body: ChannelAccountUpdateIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
) -> ChannelAccountOut:
    """Edita nombre, departamento, estado o token propio. Reservado a administración."""
    account = await repo.get_channel_account(session, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada")

    changes = body.model_dump(exclude_unset=True)
    if changes.get("department_id") is not None:
        department = await repo.get_department(session, changes["department_id"])
        if department is None or department.tenant_id != account.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
            )

    if "access_token" in changes:
        token = changes.pop("access_token")
        if token:
            try:
                changes["credentials_ciphertext"] = encrypt_json(
                    {"access_token": token}, settings=settings
                )
            except EncryptionNotConfiguredError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=str(exc)
                ) from exc
        else:
            changes["credentials_ciphertext"] = None

    await repo.update_channel_account(session, account, **changes)
    await repo.record_audit(
        session,
        tenant_id=account.tenant_id,
        actor=principal.audit_actor,
        action="channel_account_updated",
        subject_type="channel_account",
        subject_id=str(account.id),
        detail={
            key: (str(value) if isinstance(value, uuid.UUID) else value)
            for key, value in changes.items()
            if key != "credentials_ciphertext"
        },
    )
    account = await repo.get_channel_account(session, account.id)
    return _channel_account_out(account)


# --------------------------------------------------------------------------- #
# Configuración del inquilino
# --------------------------------------------------------------------------- #
@router.get("/admin/settings")
async def get_admin_settings(
    session: SessionDep, settings: SettingsDep, principal: AdminDep, tenant: str | None = None
) -> dict[str, Any]:
    """Ajustes editables desde la consola. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    fallback_message = tenant_row.settings.get("fallback_message") or FallbackHandler.MESSAGE
    return {"fallback_message": fallback_message}


@router.put("/admin/settings")
async def update_admin_settings(
    body: AdminSettingsIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Actualiza la respuesta automática del asistente. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    await repo.update_tenant_settings(
        session, tenant_row, fallback_message=body.fallback_message.strip()
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="fallback_message_updated",
        subject_type="tenant",
        subject_id=str(tenant_row.id),
    )
    return {"status": "ok", "fallback_message": tenant_row.settings["fallback_message"]}


# --------------------------------------------------------------------------- #
# Supervisión
# --------------------------------------------------------------------------- #
@router.get("/supervisor/overview")
async def supervisor_overview(
    session: SessionDep,
    settings: SettingsDep,
    principal: SupervisorDep,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Panorama del equipo: carga por agente y últimas derivaciones."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    names = await _agent_names(session, tenant_row.id)
    return {
        "tenant": tenant_row.slug,
        "workload": await repo.workload_by_agent(session, tenant_row.id),
        "messages_by_channel": await repo.channel_stats(session, tenant_row.id),
        "recent_transfers": [
            {
                "conversation_id": str(entry.conversation_id),
                "action": entry.action,
                "from": names.get(entry.from_agent_id),
                "to": names.get(entry.to_agent_id),
                "by": names.get(entry.by_agent_id),
                "note": entry.note,
                "at": entry.created_at.isoformat(),
            }
            for entry in await repo.transfer_activity(session, tenant_row.id)
        ],
    }


@router.get("/stats")
async def stats(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> dict[str, Any]:
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    return {
        "tenant": tenant_row.slug,
        "messages_by_channel": await repo.channel_stats(session, tenant_row.id),
    }


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
        last_message_at=row.last_message_at.isoformat() if row.last_message_at else None,
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


async def _publish_control_change(
    conversation: Conversation, *, control: str, assignee_name: str | None
) -> None:
    """Avisa al chatbox del cliente para que el encabezado muestre quién le atiende."""
    await hub.publish(
        conversation_topic(conversation.channel_conversation_id),
        {"type": "control_changed", "control": control, "assignee_name": assignee_name},
    )
