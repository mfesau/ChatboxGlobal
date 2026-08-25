"""Descarga autenticada de los adjuntos.

Los ficheros se sirvieron durante un tiempo con un montaje estático, confiando
en que el nombre aleatorio bastara como control de acceso. No basta: la URL
viaja en la página, en los registros del servidor y en el historial del
navegador, de modo que cualquiera que la obtuviera —incluso desde fuera del
departamento dueño de la conversación— podía descargar el fichero.

Aquí se aplica al fichero la misma regla que al hilo que lo contiene: lo
descarga quien puede ver esa conversación, y nadie más. La ruta se conserva
(``/uploads/{inquilino}/{fichero}``) para que los adjuntos ya guardados en
mensajes anteriores sigan funcionando sin migrar dato alguno.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.api.deps import (
    CONTACT_SESSION_COOKIE,
    SESSION_COOKIE,
    Principal,
    SessionDep,
    SettingsDep,
)
from app.core.security import hash_token
from app.db import repositories as repo
from app.db.models import Agent, Contact
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["adjuntos"])

#: Nombre tal y como lo genera ``save_upload``: 32 dígitos hexadecimales más
#: extensión conocida. Validarlo con este patrón descarta de raíz el recorrido
#: de rutas: ni «..», ni separadores, ni nombres arbitrarios.
STORED_NAME = re.compile(r"^[0-9a-f]{32}\.(?:jpg|png|webp|gif)$")

MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@router.get("/uploads/{namespace}/{filename}", include_in_schema=False)
async def download_attachment(
    namespace: str,
    filename: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> FileResponse:
    """Entrega un adjunto a quien puede ver la conversación que lo contiene.

    Se responde 404 —y no 403— ante cualquier fallo de autorización, igual que
    hace ``authorized_conversation``: distinguir «no existe» de «existe pero no
    es suyo» ya filtraría información.
    """
    if not STORED_NAME.match(filename):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Adjunto no encontrado")

    agent, contact = await _resolve_viewer(request, session)
    if agent is None and contact is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicie sesión para ver el adjunto"
        )

    tenant_id = agent.tenant_id if agent is not None else contact.tenant_id
    if not _matches_tenant(namespace, tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Adjunto no encontrado")

    message = await repo.find_message_by_attachment(
        session, tenant_id=tenant_id, stored_name=filename
    )
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Adjunto no encontrado")

    conversation = await repo.get_conversation(session, message.conversation_id)
    if conversation is None or not _may_view(conversation, agent, contact):
        log.info(
            "attachment_denied",
            filename=filename,
            agent=agent.email if agent else None,
            contact=str(contact.id) if contact else None,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Adjunto no encontrado")

    path = Path(settings.uploads_dir) / namespace / filename
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Adjunto no encontrado")

    return FileResponse(
        path,
        media_type=MEDIA_TYPES.get(path.suffix, "application/octet-stream"),
        headers={
            # Contenido privado: ni las cachés compartidas ni el disco del
            # navegador deben conservarlo.
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _resolve_viewer(
    request: Request, session: SessionDep
) -> tuple[Agent | None, Contact | None]:
    """Identifica a quien pide el fichero: un agente o un cliente del chatbox.

    Se leen las cookies a mano en lugar de declarar dependencias: ambas
    identidades son válidas aquí, y una dependencia obligatoria rechazaría a la
    otra antes de llegar a la comprobación.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        agent = await repo.resolve_agent_session(session, hash_token(token))
        if agent is not None:
            return agent, None

    token = request.cookies.get(CONTACT_SESSION_COOKIE)
    if token:
        contact = await repo.resolve_contact_session(session, hash_token(token))
        if contact is not None:
            return None, contact

    return None, None


def _matches_tenant(namespace: str, tenant_id: uuid.UUID) -> bool:
    try:
        return uuid.UUID(namespace) == tenant_id
    except ValueError:
        return False


def _may_view(conversation, agent: Agent | None, contact: Contact | None) -> bool:
    if agent is not None:
        # Misma regla que en la consola, departamentos incluidos.
        return Principal(agent=agent, role=agent.role, via="session").can_access(conversation)
    # El cliente solo ve los adjuntos de su propia conversación.
    return contact is not None and conversation.contact_id == contact.id
