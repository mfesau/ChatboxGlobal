"""Capa central de orquestación.

Secuencia de un turno:

1. Recepción del evento del canal (webhook de WhatsApp, *Activity* del Bot
   Framework o mensaje del chatbox web).
2. Normalización al formato interno común mediante el adaptador del canal.
3. Aplicación de la lógica de negocio y de IA a través de la cadena de handlers.
4. Envío de la respuesta por el canal de origen, con garantía de entrega
   mediante la cola transaccional de salida.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from app.channels.base import ChannelAdapter, ChannelRegistry
from app.config import Settings
from app.core.envelope import (
    ChannelKind,
    ContentType,
    ConversationRef,
    DeliveryStatus,
    InboundMessage,
    OutboundMessage,
)
from app.core.hub import conversation_topic, hub, inbox_topic
from app.core.pipeline import Pipeline, TurnContext
from app.db import repositories as repo
from app.db.engine import session_scope
from app.logging_setup import get_logger

log = get_logger(__name__)


class Orchestrator:
    """Punto único de entrada para todo mensaje entrante."""

    def __init__(
        self,
        *,
        settings: Settings,
        registry: ChannelRegistry,
        pipeline: Pipeline,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.pipeline = pipeline
        #: Lo inyecta el ciclo de vida de la aplicación para acelerar el envío.
        self.on_enqueued: Any = None

    # ------------------------------------------------------------------ (1)
    async def handle_event(
        self,
        channel: ChannelKind | str,
        *,
        payload: dict[str, Any],
        headers: Mapping[str, str],
        raw_body: bytes = b"",
        verify: bool = True,
    ) -> dict[str, Any]:
        """Recibe la carga de un canal y procesa cuantos mensajes contenga."""
        adapter = self.registry.get(channel)
        if verify:
            await adapter.verify_request(headers=headers, body=raw_body)

        inbound_messages = await adapter.parse(payload=payload, headers=headers)
        processed = 0
        skipped = 0
        for inbound in inbound_messages:
            if await self.process_inbound(inbound, adapter=adapter):
                processed += 1
            else:
                skipped += 1

        log.info(
            "event_handled",
            channel=str(adapter.kind),
            parsed=len(inbound_messages),
            processed=processed,
            skipped=skipped,
        )
        return {"parsed": len(inbound_messages), "processed": processed, "skipped": skipped}

    # ------------------------------------------------------------ (2) y (3)
    async def process_inbound(
        self, inbound: InboundMessage, *, adapter: ChannelAdapter | None = None
    ) -> bool:
        """Procesa un mensaje ya normalizado. Devuelve ``False`` si se descartó."""
        adapter = adapter or self.registry.get(inbound.channel)
        enqueued: list[uuid.UUID] = []

        async with session_scope() as session:
            assert inbound.dedupe_key is not None
            if not await repo.claim_dedupe_key(session, inbound.dedupe_key, inbound.channel):
                log.info("duplicate_dropped", dedupe_key=inbound.dedupe_key)
                return False

            tenant = await repo.get_or_create_tenant(
                session, inbound.tenant_slug or self.settings.default_tenant_slug
            )

            # Los eventos de sistema no crean turno de conversación.
            if inbound.content_type is ContentType.SYSTEM:
                await self._apply_system_event(session, inbound)
                return True

            channel_account = None
            if inbound.conversation.channel_account_id:
                channel_account = await repo.get_or_create_channel_account(
                    session,
                    tenant_id=tenant.id,
                    channel=inbound.channel,
                    external_id=inbound.conversation.channel_account_id,
                )

            contact = await repo.resolve_contact(
                session,
                tenant_id=tenant.id,
                channel=inbound.channel,
                party=inbound.sender,
            )
            if contact.is_blocked:
                log.info("blocked_contact_dropped", contact_id=str(contact.id))
                return False

            conversation = await repo.resolve_conversation(
                session,
                tenant_id=tenant.id,
                ref=inbound.conversation,
                contact_id=contact.id,
                channel_account_id=channel_account.id if channel_account else None,
            )
            stored = await repo.record_inbound(
                session,
                conversation=conversation,
                contact_id=contact.id,
                inbound=inbound,
            )
            history = await repo.recent_messages(
                session, conversation.id, limit=self.settings.ai_history_turns * 2
            )

            await self._broadcast_inbound(tenant.slug, conversation, inbound)

            ctx = TurnContext(
                inbound=inbound,
                session=session,
                settings=self.settings,
                tenant=tenant,
                conversation=conversation,
                contact=contact,
                stored_message=stored,
                history=history,
            )
            await self.pipeline.run(ctx)

            if ctx.suppress_reply:
                return True

            ref = ConversationRef.from_dict(conversation.conversation_ref)
            for reply in ctx.replies:
                # Persistencia y encolado en la misma transacción que el entrante:
                # o se guardan ambos, o ninguno.
                message = await repo.record_outbound(
                    session, conversation=conversation, outbound=reply
                )
                item = await repo.enqueue_outbound(
                    session,
                    conversation=conversation,
                    message=message,
                    ref=ref,
                    outbound=reply,
                )
                enqueued.append(item.id)

        if enqueued and self.on_enqueued is not None:
            await self.on_enqueued()
        return True

    async def _apply_system_event(self, session: Any, inbound: InboundMessage) -> None:
        """Aplica acuses de recibo y avisos del canal."""
        action = inbound.action or {}
        if action.get("kind") != "delivery_status":
            log.debug("system_event", channel=str(inbound.channel), action=action)
            return

        target = action.get("target_provider_message_id")
        if not target:
            return
        await repo.apply_delivery_update(
            session,
            channel=inbound.channel,
            provider_message_id=target,
            status=DeliveryStatus(action.get("status", "sent")),
            provider_status=action.get("provider_status"),
            error_code=action.get("error_code"),
            error_detail=action.get("error_detail"),
            payload=inbound.raw,
        )

    async def _broadcast_inbound(
        self, tenant_slug: str, conversation: Any, inbound: InboundMessage
    ) -> None:
        """Notifica a la consola de agentes y al propio chatbox."""
        event = {
            "type": "message",
            "direction": "inbound",
            "conversation_id": str(conversation.id),
            "channel": str(inbound.channel),
            "text": inbound.text,
            "content_type": str(inbound.content_type),
            "sender": inbound.sender.display_name,
            "timestamp": inbound.timestamp.isoformat(),
        }
        await hub.publish(inbox_topic(tenant_slug), event)
        await hub.publish(conversation_topic(conversation.channel_conversation_id), event)

    # ------------------------------------------------------------------ (4)
    async def send_from_agent(
        self,
        *,
        conversation_id: uuid.UUID,
        outbound: OutboundMessage,
        agent_id: uuid.UUID | None = None,
        session: Any = None,
    ) -> uuid.UUID | None:
        """Envía un mensaje redactado por un agente humano desde la consola.

        Cuando quien llama ya tiene una transacción abierta —el caso de un
        endpoint HTTP— debe pasarla en ``session``. Abrir aquí una segunda
        transacción mientras la primera mantiene una escritura pendiente provoca
        un interbloqueo: la nueva espera un candado que solo se libera al
        confirmar la primera, y esa confirmación aguarda el retorno de esta
        llamada.
        """
        if session is not None:
            item_id = await self._queue_agent_reply(
                session, conversation_id=conversation_id, outbound=outbound, agent_id=agent_id
            )
        else:
            async with session_scope() as own_session:
                item_id = await self._queue_agent_reply(
                    own_session,
                    conversation_id=conversation_id,
                    outbound=outbound,
                    agent_id=agent_id,
                )

        if item_id is not None and self.on_enqueued is not None:
            # El aviso puede adelantarse a la confirmación de la transacción de
            # quien llama; el trabajador reintenta en su siguiente ciclo, de modo
            # que el mensaje sale igualmente y sin pérdida.
            await self.on_enqueued()
        return item_id

    async def _queue_agent_reply(
        self,
        session: Any,
        *,
        conversation_id: uuid.UUID,
        outbound: OutboundMessage,
        agent_id: uuid.UUID | None,
    ) -> uuid.UUID | None:
        conversation = await repo.get_conversation(session, conversation_id)
        if conversation is None:
            return None
        message = await repo.record_outbound(
            session,
            conversation=conversation,
            outbound=outbound,
            author_type="agent",
            author_agent_id=agent_id,
        )
        item = await repo.enqueue_outbound(
            session,
            conversation=conversation,
            message=message,
            ref=ConversationRef.from_dict(conversation.conversation_ref),
            outbound=outbound,
        )
        return item.id
