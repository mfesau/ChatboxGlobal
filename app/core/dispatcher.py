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

from app.channels.base import ChannelRegistry
from app.config import Settings
from app.core.envelope import ConversationRef, OutboundMessage
from app.db import repositories as repo
from app.db.engine import session_scope
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Un envío bloqueado más tiempo que esto se considera huérfano y se reencola.
STALE_LOCK = timedelta(minutes=5)
#: Antigüedad a partir de la cual se purgan las claves de idempotencia.
DEDUPE_RETENTION = timedelta(days=3)
JANITOR_INTERVAL_S = 300


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
                    ConversationRef.from_dict(item.payload["ref"]),
                    OutboundMessage.from_dict(item.payload["message"]),
                )
                for item in items
            ]

        for item_id, channel, ref, message in claimed:
            await self._deliver(item_id, channel, ref, message)
        return len(claimed)

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
        while self._running:
            await asyncio.sleep(JANITOR_INTERVAL_S)
            try:
                async with session_scope() as session:
                    requeued = await repo.requeue_stale_outbox(session, older_than=STALE_LOCK)
                    purged = await repo.purge_dedupe_keys(session, DEDUPE_RETENTION)
                if requeued or purged:
                    log.info("janitor_pass", requeued=requeued, purged_dedupe_keys=purged)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("janitor_error")
