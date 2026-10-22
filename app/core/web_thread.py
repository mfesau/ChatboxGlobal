"""Qué hilo del chatbox le corresponde a un contacto ahora mismo.

Hasta aquí el hilo era el propio contacto: ``channel_conversation_id`` valía
``str(contact.id)``, escrito así en cuatro sitios distintos. Eso significa un
único hilo por persona, para siempre, que se reabre solo en cuanto vuelve a
escribir — de modo que cerrar una conversación no cerraba nada desde el lado
del cliente: seguía viendo el mismo historial y podía continuarlo.

Ahora el hilo vigente es **el más reciente** de ese contacto, y se puede
estrenar uno nuevo. El primero conserva el identificador antiguo, así que las
conversaciones que ya existían siguen siendo las suyas sin migrar nada.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.envelope import ChannelKind, ConversationRef
from app.db.models import Conversation, Department

#: Identificador de la cuenta de canal del chatbox propio.
WEB_ACCOUNT = "web"


def first_thread_id(contact: Any) -> str:
    """Identificador del primer hilo: el del propio contacto.

    Se conserva a propósito. Cambiarlo obligaría a migrar las conversaciones
    que ya existen, y no se gana nada: lo que hacía falta era poder tener un
    segundo hilo, no renombrar el primero.
    """
    return str(contact.id)


def new_thread_id(contact: Any) -> str:
    """Identificador de un hilo nuevo para ese contacto.

    Lleva un sufijo aleatorio y no un contador: dos pestañas pidiendo hilo
    nuevo a la vez chocarían contra la restricción de unicidad, y no hay razón
    para que el número signifique nada.
    """
    return f"{contact.id}:{uuid.uuid4().hex[:8]}"


def ref_for(thread_id: str) -> ConversationRef:
    return ConversationRef(
        channel=ChannelKind.WEB,
        channel_conversation_id=thread_id,
        channel_account_id=WEB_ACCOUNT,
    )


async def current(session: AsyncSession, contact: Any) -> Conversation | None:
    """Hilo vigente del contacto, o ``None`` si todavía no escribió nunca."""
    stmt = (
        select(Conversation)
        .where(
            Conversation.tenant_id == contact.tenant_id,
            Conversation.channel == ChannelKind.WEB,
            Conversation.contact_id == contact.id,
        )
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def current_thread_id(session: AsyncSession, contact: Any) -> str:
    """Identificador del hilo vigente, estrenando el primero si no hay ninguno."""
    conversation = await current(session, contact)
    return conversation.channel_conversation_id if conversation else first_thread_id(contact)


#: Lo que el chatbox recibe cuando intenta escribir sin haber elegido área.
DEPARTMENT_REQUIRED = "department_required"


async def needs_department(session: AsyncSession, *, tenant_id: Any, thread_id: str) -> bool:
    """¿Le falta al hilo el departamento para poder escribir en él?

    El cliente tiene que elegir con qué área habla antes del primer mensaje:
    así cada conversación nace en la cola de quien la atiende. Solo se exige
    si hay alguna área activa entre la que elegir; sin ninguna, todo va a la
    cola común como siempre.
    """
    activos = (
        await session.execute(
            select(func.count())
            .select_from(Department)
            .where(Department.tenant_id == tenant_id, Department.is_active.is_(True))
        )
    ).scalar_one()
    if not activos:
        return False
    conversation = (
        await session.execute(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.channel == ChannelKind.WEB,
                Conversation.channel_conversation_id == thread_id,
            )
        )
    ).scalar_one_or_none()
    return conversation is None or conversation.department_id is None
