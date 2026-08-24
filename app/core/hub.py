"""Bus de difusión en memoria para clientes WebSocket.

El chatbox web y la consola de agentes se suscriben a claves de tema; el
adaptador web y el orquestador publican en ellas. En una implementación de
varios procesos, sustituya esta clase por Redis Pub/Sub o por
``LISTEN``/``NOTIFY`` de PostgreSQL conservando la misma interfaz.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from app.logging_setup import get_logger

log = get_logger(__name__)

Subscriber = Callable[[dict[str, Any]], Awaitable[None]]


class Hub:
    """Publicación y suscripción por tema, con entrega en el mejor esfuerzo."""

    def __init__(self) -> None:
        self._topics: dict[str, set[Subscriber]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, topic: str, subscriber: Subscriber) -> None:
        async with self._lock:
            self._topics[topic].add(subscriber)

    async def unsubscribe(self, topic: str, subscriber: Subscriber) -> None:
        async with self._lock:
            self._topics[topic].discard(subscriber)
            if not self._topics[topic]:
                self._topics.pop(topic, None)

    def subscriber_count(self, topic: str) -> int:
        return len(self._topics.get(topic, ()))

    async def publish(self, topic: str, event: dict[str, Any]) -> int:
        """Difunde el evento y devuelve el número de entregas correctas."""
        async with self._lock:
            subscribers = list(self._topics.get(topic, ()))
        if not subscribers:
            return 0

        results = await asyncio.gather(
            *(subscriber(event) for subscriber in subscribers), return_exceptions=True
        )
        delivered = 0
        for subscriber, result in zip(subscribers, results, strict=True):
            if isinstance(result, Exception):
                log.debug("hub_delivery_failed", topic=topic, error=str(result))
                await self.unsubscribe(topic, subscriber)
            else:
                delivered += 1
        return delivered


#: Instancia compartida por el proceso.
hub = Hub()


def conversation_topic(conversation_id: str) -> str:
    return f"conversation:{conversation_id}"


def inbox_topic(tenant_slug: str) -> str:
    """Cola común: todo lo que entra sin responsable asignado."""
    return f"inbox:{tenant_slug}"


def agent_topic(agent_id: str) -> str:
    """Avisos dirigidos a una sola persona: derivaciones recibidas y menciones."""
    return f"agent:{agent_id}"


def presence_topic(tenant_slug: str) -> str:
    """Altas, bajas y cambios de disponibilidad del equipo."""
    return f"presence:{tenant_slug}"
