"""Datos del contacto y directorio de contactos."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from app.api.console._common import ConversationDep, _agent_names, _conversation_out
from app.api.console.schemas import (
    ContactCommentIn,
    ContactCommentOut,
    ContactDetailOut,
    ContactProfileOut,
    ContactSummaryOut,
    ContactUpdateIn,
)
from app.api.deps import (
    Principal,
    SessionDep,
    SettingsDep,
    SupervisorDep,
)
from app.db import repositories as repo
from app.db.models import (
    Contact,
)

router = APIRouter(prefix="/api", tags=["consola"])



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
        session,
        tenant_id=tenant_row.id,
        search=search,
        limit=limit,
        offset=offset,
        department_ids=principal.department_ids,
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


async def _visible_contact(
    session: Any, principal: Principal, contact_id: uuid.UUID
) -> Contact:
    """El contacto, si quien pregunta puede verlo; si no, 404.

    Administración ve a todos. Una supervisión acotada por departamento solo a
    quienes tienen alguna conversación en sus departamentos o en la cola
    común: es la misma regla que el directorio, para que lo que no aparece en
    la lista tampoco se pueda abrir escribiendo su identificador.
    """
    contact = await repo.get_contact(session, contact_id)
    visible = contact is not None and (
        principal.department_ids is None
        or bool(
            await repo.list_conversations_for_contact(
                session, contact_id, department_ids=principal.department_ids
            )
        )
    )
    if not visible:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contacto no encontrado")
    return contact


@router.get("/contacts/{contact_id}", response_model=ContactProfileOut)
async def get_contact_profile(
    contact_id: uuid.UUID, session: SessionDep, principal: SupervisorDep
) -> ContactProfileOut:
    """Ficha completa: datos, comentarios y sus conversaciones visibles."""
    contact = await _visible_contact(session, principal, contact_id)
    comments = await repo.list_contact_comments(session, contact_id)
    conversations = await repo.list_conversations_for_contact(
        session, contact_id, department_ids=principal.department_ids
    )
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
    contact = await _visible_contact(session, principal, contact_id)

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
    contact = await _visible_contact(session, principal, contact_id)

    comment = await repo.add_contact_comment(
        session,
        contact_id=contact_id,
        tenant_id=contact.tenant_id,
        agent_id=principal.id,
        body=body.body,
    )
    return {"status": "ok", "comment_id": str(comment.id)}
