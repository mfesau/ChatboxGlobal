"""Trabajador de la cola de salida.

Separa la generación de la respuesta de su entrega: el turno confirma en base de
datos y responde al webhook de inmediato, mientras el envío efectivo se realiza
aquí, con reintentos y retroceso exponencial. Así, una caída momentánea de la
Graph API o del servicio de canales no pierde mensajes ni bloquea el webhook.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.base import ChannelRegistry
from app.config import Settings
from app.core.envelope import ChannelKind, ConversationRef, OutboundMessage, utcnow
from app.core.secrets import DecryptionError, decrypt_json
from app.db import repositories as repo
from app.db.engine import session_scope
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Un envío bloqueado más tiempo que esto se considera huérfano y se reencola.
STALE_LOCK = timedelta(minutes=5)
#: Antigüedad a partir de la cual se purgan las claves de idempotencia.
DEDUPE_RETENTION = timedelta(days=3)
JANITOR_INTERVAL_S = 300
#: La caducidad del contenido se revisa una vez al día. Es una operación que
#: recorre tablas grandes y nada cambia por hacerla cada cinco minutos.
RETENTION_INTERVAL_S = 24 * 60 * 60


class OutboxDispatcher:
    """Conjunto de trabajadores que drenan la tabla ``outbox``."""

    def __init__(self, *, settings: Settings, registry: ChannelRegistry) -> None:
        self.settings = settings
        self.registry = registry
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._worker(f"{os.getpid()}-{index}"), name=f"outbox-{index}")
            for index in range(self.settings.outbox_workers)
        ]
        self._tasks.append(asyncio.create_task(self._janitor(), name="outbox-janitor"))
        log.info("dispatcher_started", workers=self.settings.outbox_workers)

    async def stop(self) -> None:
        self._running = False
        self._wake.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        log.info("dispatcher_stopped")

    async def kick(self) -> None:
        """Despierta a los trabajadores tras encolar trabajo nuevo."""
        self._wake.set()

    # ------------------------------------------------------------------ bucle
    async def _worker(self, worker_id: str) -> None:
        while self._running:
            try:
                processed = await self._drain_once(worker_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("outbox_worker_error", worker=worker_id)
                processed = 0

            if processed:
                # Quedó trabajo pendiente: se continúa sin esperar.
                continue
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self.settings.outbox_poll_interval_s
                )

    async def _drain_once(self, worker_id: str) -> int:
        async with session_scope() as session:
            items = await repo.claim_outbox_batch(session, worker_id=worker_id, limit=10)
            claimed = [
                (
                    item.id,
                    item.channel,
                    await self._with_account_credentials(
                        session, item.channel, ConversationRef.from_dict(item.payload["ref"])
                    ),
                    OutboundMessage.from_dict(item.payload["message"]),
                )
                for item in items
            ]

        for item_id, channel, ref, message in claimed:
            await self._deliver(item_id, channel, ref, message)
        return len(claimed)

    async def _with_account_credentials(
        self, session: AsyncSession, channel: object, ref: ConversationRef
    ) -> ConversationRef:
        """Suma, si existen, las credenciales propias de la cuenta que envía.

        Los adaptadores no tocan la base de datos (ver `ChannelAdapter`); este
        es el único punto que ya tiene sesión abierta justo antes de invocar
        a `adapter.send()`, así que es donde se resuelve qué credenciales usar
        — el adaptador solo lee `ref.extra["credentials"]` si está.
        """
        if not ref.channel_account_id:
            return ref
        account = await repo.find_channel_account(
            session, channel=ChannelKind(channel), external_id=ref.channel_account_id
        )
        if account is None or not account.credentials_ciphertext:
            return ref
        try:
            credentials = decrypt_json(account.credentials_ciphertext, settings=self.settings)
        except DecryptionError:
            log.error("channel_account_credentials_undecryptable", account_id=str(account.id))
            return ref
        return ConversationRef(
            channel=ref.channel,
            channel_conversation_id=ref.channel_conversation_id,
            channel_account_id=ref.channel_account_id,
            service_url=ref.service_url,
            reply_to_message_id=ref.reply_to_message_id,
            extra={**ref.extra, "credentials": credentials},
        )

    async def _deliver(
        self,
        item_id: object,
        channel: object,
        ref: ConversationRef,
        message: OutboundMessage,
    ) -> None:
        try:
            adapter = self.registry.get(channel)  # type: ignore[arg-type]
            receipt = await adapter.send(ref=ref, message=message)
        except Exception as exc:
            log.exception("outbox_send_crashed", outbox_id=str(item_id))
            async with session_scope() as session:
                await repo.mark_outbox_failed(
                    session,
                    item_id,  # type: ignore[arg-type]
                    error=f"{type(exc).__name__}: {exc}",
                    retryable=True,
                    max_attempts=self.settings.outbox_max_attempts,
                )
            return

        async with session_scope() as session:
            if receipt.ok:
                await repo.mark_outbox_sent(
                    session, item_id, receipt.provider_message_id  # type: ignore[arg-type]
                )
            else:
                await repo.mark_outbox_failed(
                    session,
                    item_id,  # type: ignore[arg-type]
                    error=f"{receipt.error_code}: {receipt.error_detail}",
                    retryable=receipt.retryable,
                    max_attempts=self.settings.outbox_max_attempts,
                )
                log.warning(
                    "outbox_delivery_failed",
                    outbox_id=str(item_id),
                    code=receipt.error_code,
                    retryable=receipt.retryable,
                )

    # ------------------------------------------------------------ mantenimiento
    async def _janitor(self) -> None:
        """Recupera bloqueos huérfanos y purga claves de idempotencia caducadas."""
        next_retention = 0.0
        while self._running:
            await asyncio.sleep(JANITOR_INTERVAL_S)
            try:
                async with session_scope() as session:
                    requeued = await repo.requeue_stale_outbox(session, older_than=STALE_LOCK)
                    purged = await repo.purge_dedupe_keys(session, DEDUPE_RETENTION)
                    # Mismo repaso periódico: no hace falta otro temporizador
                    # para dar por incumplido un objetivo de respuesta.
                    breached = await repo.breach_overdue_first_responses(session)
                if requeued or purged or breached:
                    log.info(
                        "janitor_pass",
                        requeued=requeued,
                        purged_dedupe_keys=purged,
                        sla_breached=breached,
                    )

                await self._release_scheduled()

                now = asyncio.get_running_loop().time()
                if now >= next_retention:
                    next_retention = now + RETENTION_INTERVAL_S
                    await self._apply_retention()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("janitor_error")

    async def _release_scheduled(self) -> None:
        """Pasa a la cola de salida los mensajes programados que ya vencieron.

        El retraso máximo es el propio intervalo de esta tarea. Es de sobra
        para lo que se programa aquí —un seguimiento, un aviso de ausencia—, y
        evita un segundo temporizador solo para esto.
        """
        async with session_scope() as session:
            vencidos = await repo.due_scheduled_messages(session)
            for item in vencidos:
                conversation = await repo.get_conversation(session, item.conversation_id)
                if conversation is None:
                    item.status = "cancelled"
                    continue
                await repo.enqueue_scheduled(session, item=item, conversation=conversation)

        if vencidos:
            log.info("scheduled_released", count=len(vencidos))
            self.kick()

    async def _apply_retention(self) -> None:
        """Aplica los plazos de conservación configurados.

        Sin plazos —lo que viene puesto— no toca nada: quien no los configure
        conserva el histórico entero, que es lo que hacía la aplicación antes
        de existir esta tarea.
        """
        messages_days = self.settings.retention_days_messages
        audit_days = self.settings.retention_days_audit
        if messages_days <= 0 and audit_days <= 0:
            return

        async with session_scope() as session:
            blanked = 0
            discarded = 0
            if messages_days > 0:
                blanked = await repo.purge_message_bodies(
                    session, older_than=utcnow() - timedelta(days=messages_days)
                )
            if audit_days > 0:
                discarded = await repo.purge_audit(
                    session, older_than=utcnow() - timedelta(days=audit_days)
                )
        if blanked or discarded:
            log.info("retention_pass", blanked_messages=blanked, discarded_audit=discarded)
