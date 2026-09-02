"""Cadena de middlewares de la lógica de negocio y de IA.

Cada eslabón recibe el contexto del turno y una función ``next_`` que continúa
la cadena. Un eslabón puede resolver el turno por sí mismo, enriquecer el
contexto y delegar, o cortocircuitar la ejecución. Este diseño mantiene el
orquestador estable: la funcionalidad crece añadiendo handlers al arranque.
"""

from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.envelope import (
    Attachment,
    ContentType,
    InboundMessage,
    OutboundMessage,
)
from app.db.models import Contact, Conversation, Message, Tenant
from app.logging_setup import get_logger

log = get_logger(__name__)

NextFn = Callable[[], Awaitable[None]]


@dataclass
class TurnContext:
    """Estado de un turno de conversación."""

    inbound: InboundMessage
    session: AsyncSession
    settings: Settings
    tenant: Tenant
    conversation: Conversation
    contact: Contact | None
    stored_message: Message
    history: list[Message] = field(default_factory=list)
    replies: list[OutboundMessage] = field(default_factory=list)
    #: Datos compartidos entre handlers dentro del mismo turno.
    scratch: dict[str, Any] = field(default_factory=dict)
    #: Cuando es cierto, el orquestador no envía respuesta automática alguna.
    suppress_reply: bool = False

    @property
    def text(self) -> str:
        """Texto normalizado del entrante: sin espacios sobrantes."""
        return (self.inbound.text or "").strip()

    @property
    def state(self) -> dict[str, Any]:
        """Estado persistente del flujo conversacional."""
        return self.conversation.state

    def set_state(self, key: str, value: Any) -> None:
        """Actualiza el estado reasignando el diccionario.

        La reasignación es necesaria para que SQLAlchemy detecte el cambio en una
        columna ``JSONB``; la mutación en el sitio pasaría inadvertida.
        """
        self.conversation.state = {**self.conversation.state, key: value}

    def clear_state(self) -> None:
        self.conversation.state = {}

    def reply(
        self,
        text: str | None = None,
        *,
        quick_replies: list[dict[str, str]] | None = None,
        attachments: list[Attachment] | None = None,
        content_type: ContentType = ContentType.TEXT,
        **channel_data: Any,
    ) -> OutboundMessage:
        """Encola una respuesta saliente y la devuelve."""
        message = OutboundMessage(
            text=text,
            quick_replies=quick_replies or [],
            attachments=attachments or [],
            content_type=content_type,
            channel_data=channel_data,
        )
        self.replies.append(message)
        return message


class Handler(abc.ABC):
    """Eslabón de la cadena de procesamiento."""

    name: ClassVar[str] = "handler"

    @abc.abstractmethod
    async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
        """Procesa el turno y decide si continuar con ``await next_()``."""


class Pipeline:
    """Composición ordenada de handlers."""

    def __init__(self, handlers: list[Handler]) -> None:
        self._handlers = handlers

    @property
    def handlers(self) -> list[Handler]:
        return list(self._handlers)

    def append(self, handler: Handler) -> None:
        self._handlers.append(handler)

    async def run(self, ctx: TurnContext) -> None:
        """Ejecuta la cadena. Un handler que falla no detiene a los anteriores."""
        await self._invoke(0, ctx)

    async def _invoke(self, index: int, ctx: TurnContext) -> None:
        if index >= len(self._handlers):
            return
        handler = self._handlers[index]

        advanced = False

        async def next_() -> None:
            # Idempotente: si el handler ya delegó y luego falla, la cola no se
            # vuelve a ejecutar.
            nonlocal advanced
            if advanced:
                return
            advanced = True
            await self._invoke(index + 1, ctx)

        try:
            await handler.handle(ctx, next_)
        except Exception:
            log.exception(
                "handler_failed",
                handler=handler.name,
                conversation_id=str(ctx.conversation.id),
            )
            # La degradación es controlada: se continúa con el resto de la cadena.
            await next_()
