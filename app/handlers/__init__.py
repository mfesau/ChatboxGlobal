"""Handlers de la lógica de negocio y construcción de la cadena por omisión."""

from __future__ import annotations

from app.config import Settings
from app.core.pipeline import Pipeline
from app.handlers.ai import AIHandler
from app.handlers.builtin import (
    BusinessHoursHandler,
    CommandHandler,
    FallbackHandler,
    FirstResponseSlaHandler,
    HumanControlHandler,
    RateLimitHandler,
    UnsupportedContentHandler,
)

__all__ = ["build_default_pipeline"]


def build_default_pipeline(settings: Settings) -> Pipeline:
    """Cadena por omisión, del eslabón más externo al más interno.

    El orden es deliberado: los guardias que pueden cortocircuitar el turno se
    evalúan antes de gastar una llamada al modelo, y ``FallbackHandler`` envuelve
    al resto para garantizar respuesta.
    """
    return Pipeline(
        [
            RateLimitHandler(settings.inbound_rate_limit_per_minute),
            HumanControlHandler(),
            BusinessHoursHandler(),
            FirstResponseSlaHandler(),
            FallbackHandler(),
            CommandHandler(),
            UnsupportedContentHandler(),
            AIHandler(settings),
        ]
    )
