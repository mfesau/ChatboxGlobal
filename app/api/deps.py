"""Dependencias compartidas por los enrutadores: contexto, sesión y permisos."""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.orchestrator import Orchestrator
from app.core.security import hash_token
from app.db import repositories as repo
from app.db.engine import get_session
from app.db.models import ROLE_ADMIN, SUPERVISOR_ROLES, Agent, Contact

#: Nombre de la cookie de sesión de la consola.
SESSION_COOKIE = "chatbox_session"
SESSION_TTL = timedelta(hours=12)

#: Cookie de sesión del chatbox público. Nombre distinto de ``SESSION_COOKIE``:
#: son identidades independientes y no deben compartir ni pisarse la cookie.
CONTACT_SESSION_COOKIE = "chatbox_contact_session"
#: Vigencia larga: es un cliente que vuelve, no una consola operativa.
CONTACT_SESSION_TTL = timedelta(days=30)


def get_orchestrator(request: Request) -> Orchestrator:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:  # pragma: no cover - fallo de arranque
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El orquestador no está inicializado",
        )
    return orchestrator


OrchestratorDep = Annotated[Orchestrator, Depends(get_orchestrator)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


# --------------------------------------------------------------------------- #
# Identidad de quien opera la consola
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Principal:
    """Quien realiza la petición: una persona con sesión o una clave de servicio.

    La clave de servicio (``ADMIN_API_KEY``) existe para integraciones y para el
    arranque inicial, cuando todavía no hay ningún agente creado. Opera con
    alcance de supervisión y sin identidad personal, por lo que sus acciones se
    registran en la auditoría como ``service``.
    """

    agent: Agent | None
    role: str
    via: str  # "session" | "api_key"

    @property
    def id(self) -> uuid.UUID | None:
        return self.agent.id if self.agent else None

    @property
    def label(self) -> str:
        return self.agent.label if self.agent else "clave de servicio"

    @property
    def audit_actor(self) -> str:
        return f"agent:{self.agent.email}" if self.agent else "service"

    @property
    def is_supervisor(self) -> bool:
        return self.role in SUPERVISOR_ROLES

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def department_ids(self) -> set[uuid.UUID] | None:
        """Departamentos que puede atender. ``None`` = sin restricción.

        Administración y la clave de servicio no tienen ninguna acotación
        por departamento. Supervisión queda acotada a lo otorgado, igual que
        un agente.
        """
        if self.agent is None or self.is_admin:
            return None
        return repo.agent_department_ids(self.agent)

    def can_access(self, conversation) -> bool:
        if self.agent is None:
            return True  # la clave de servicio equivale a supervisión
        return repo.agent_can_access(conversation, self.agent)


async def resolve_principal(
    session: SessionDep,
    settings: SettingsDep,
    chatbox_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Principal:
    """Autentica la petición por cookie de sesión o por clave de servicio."""
    if chatbox_session:
        agent = await repo.resolve_agent_session(session, hash_token(chatbox_session))
        if agent is not None:
            return Principal(agent=agent, role=agent.role, via="session")

    expected = settings.admin_api_key
    if expected is not None and x_api_key is not None and hmac.compare_digest(
        x_api_key, expected.get_secret_value()
    ):
        return Principal(agent=None, role=ROLE_ADMIN, via="api_key")

    # El login es obligatorio en todo entorno: no hay acceso sin sesión de
    # agente ni clave de servicio, tampoco en desarrollo.
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Inicie sesión o presente una clave de servicio válida",
    )


PrincipalDep = Annotated[Principal, Depends(resolve_principal)]


async def require_supervisor(principal: PrincipalDep) -> Principal:
    if not principal.is_supervisor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta operación requiere permisos de supervisión",
        )
    return principal


SupervisorDep = Annotated[Principal, Depends(require_supervisor)]


async def require_admin(principal: PrincipalDep) -> Principal:
    if not principal.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta operación requiere permisos de administración",
        )
    return principal


AdminDep = Annotated[Principal, Depends(require_admin)]


async def authorized_conversation(
    conversation_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
):
    """Carga la conversación comprobando que quien pregunta puede verla.

    Se responde 404 y no 403 cuando el agente no tiene acceso: revelar que la
    conversación existe ya filtraría información sobre la cartera de un compañero.
    """
    conversation = await repo.get_conversation(session, conversation_id)
    if conversation is None or not principal.can_access(conversation):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversación no encontrada"
        )
    return conversation


# --------------------------------------------------------------------------- #
# Identidad de quien escribe en el chatbox público
# --------------------------------------------------------------------------- #
async def resolve_contact(
    session: SessionDep,
    chatbox_contact_session: Annotated[str | None, Cookie(alias=CONTACT_SESSION_COOKIE)] = None,
) -> Contact:
    """Autentica la petición del chatbox por su cookie de sesión.

    Sin variante alguna por entorno: el registro y el login son obligatorios
    para poder chatear, también en desarrollo.
    """
    if chatbox_contact_session:
        contact = await repo.resolve_contact_session(session, hash_token(chatbox_contact_session))
        if contact is not None:
            return contact
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Inicie sesión o regístrese para usar el chatbox",
    )


ContactDep = Annotated[Contact, Depends(resolve_contact)]
