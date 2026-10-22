"""Encuesta de satisfacción al cerrar una conversación.

Se pregunta por el mismo canal por el que se atendió, y de la forma que ese
canal admite. Donde hay botones —el chatbox web, Telegram, WhatsApp,
Messenger— la nota se pulsa y vuelve como una interacción, que el
``CsatHandler`` reconoce. En correo y en SMS no hay botones: viaja un enlace
con un testigo a una página pública, porque una respuesta escrita a un SMS
puede llegar días después, desde otro número o con un «4 gracias!!» que no hay
que hacerle interpretar a nadie.

Se configura por departamento, en ``enabled_modules``, igual que el módulo de
hotel: una rama puede querer medirse y otra no.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.envelope import ChannelKind, OutboundMessage
from app.db.models import Conversation, CsatSurvey, Department
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Clave del módulo dentro de ``Department.enabled_modules``.
MODULE_KEY = "csat"

#: Pregunta por omisión cuando el departamento no escribe la suya.
DEFAULT_QUESTION = (
    "Antes de cerrar: ¿cómo valora la atención recibida, del 1 al 5? "
    "Su respuesta nos ayuda a mejorar."
)

#: Clave del estado de la conversación mientras se espera la nota.
STATE_KEY = "awaiting_csat"

#: Canales que saben enseñar botones. En los demás va un enlace.
CHANNELS_WITH_BUTTONS = frozenset(
    {ChannelKind.WEB, ChannelKind.TELEGRAM, ChannelKind.WHATSAPP, ChannelKind.FACEBOOK}
)


def enabled(department: Department | None) -> bool:
    """Si esa rama mide la satisfacción. Sin departamento, no se pregunta."""
    if department is None:
        return False
    return bool((department.enabled_modules or {}).get(MODULE_KEY, {}).get("enabled"))


def question(department: Department | None) -> str:
    configuracion = (department.enabled_modules or {}).get(MODULE_KEY, {}) if department else {}
    return (configuracion.get("question") or "").strip() or DEFAULT_QUESTION


async def set_enabled(
    session: AsyncSession, *, department: Department, enabled: bool, question: str | None = None
) -> Department:
    """Activa o desactiva la encuesta en una rama, y fija su pregunta."""
    modules = dict(department.enabled_modules or {})
    actual = dict(modules.get(MODULE_KEY, {}))
    actual["enabled"] = enabled
    if question is not None:
        actual["question"] = question.strip()[:500] or None
    modules[MODULE_KEY] = actual
    department.enabled_modules = modules
    await session.flush()
    return department


# --------------------------------------------------------------------------- #
# Envío
# --------------------------------------------------------------------------- #
async def survey_for(
    session: AsyncSession, conversation: Conversation
) -> CsatSurvey | None:
    stmt = select(CsatSurvey).where(CsatSurvey.conversation_id == conversation.id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def open_survey(
    session: AsyncSession, conversation: Conversation
) -> tuple[CsatSurvey, bool]:
    """Devuelve la encuesta de la conversación, creándola si no existía.

    El segundo valor dice si acaba de crearse: una conversación que se cierra,
    se reabre y se vuelve a cerrar reutiliza la encuesta anterior en lugar de
    volver a preguntar lo mismo.
    """
    existente = await survey_for(session, conversation)
    if existente is not None:
        return existente, False

    survey = CsatSurvey(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        contact_id=conversation.contact_id,
        agent_id=conversation.assignee_id,
        department_id=conversation.department_id,
        token=secrets.token_urlsafe(32)[:64],
    )
    session.add(survey)
    await session.flush()
    return survey, True


def build_message(
    *,
    conversation: Conversation,
    survey: CsatSurvey,
    department: Department | None,
    public_base_url: str,
) -> OutboundMessage:
    """Compone la pregunta con la forma que admita el canal."""
    texto = question(department)
    if conversation.channel in CHANNELS_WITH_BUTTONS:
        return OutboundMessage(
            text=texto,
            quick_replies=[
                {"id": f"csat:{nota}", "title": str(nota)} for nota in range(1, 6)
            ],
        )

    enlace = f"{public_base_url.rstrip('/')}/csat/{survey.token}"
    return OutboundMessage(text=f"{texto}\n\n{enlace}")


# --------------------------------------------------------------------------- #
# Respuesta
# --------------------------------------------------------------------------- #
def read_score(text: str | None, action: dict[str, Any] | None) -> int | None:
    """Interpreta la respuesta como una nota del 1 al 5, o ``None``.

    Acepta el botón pulsado y también el número escrito a mano, que es lo que
    llega de quien contesta al mensaje en vez de tocar el botón. Se exige que
    el mensaje sea solo el número: un «pues entre 3 y 4 diría» no se puntúa
    solo, y adivinarlo falsearía la medida.
    """
    if action:
        crudo = str(action.get("id") or "")
        if crudo.startswith("csat:"):
            crudo = crudo.removeprefix("csat:")
        if crudo.isdigit() and 1 <= int(crudo) <= 5:
            return int(crudo)

    limpio = (text or "").strip().rstrip(".")
    if limpio.isdigit() and 1 <= int(limpio) <= 5:
        return int(limpio)
    return None


async def record_score(
    session: AsyncSession,
    survey: CsatSurvey,
    *,
    score: int,
    comment: str | None = None,
) -> None:
    from app.core.envelope import utcnow

    survey.score = score
    survey.comment = (comment or "").strip()[:2_000] or None
    survey.responded_at = utcnow()
    log.info("csat_answered", survey_id=str(survey.id), score=score)


async def survey_by_token(session: AsyncSession, token: str) -> CsatSurvey | None:
    stmt = select(CsatSurvey).where(CsatSurvey.token == token)
    return (await session.execute(stmt)).scalar_one_or_none()


async def average(
    session: AsyncSession, *, tenant_id: uuid.UUID, since: Any, until: Any
) -> dict[str, Any]:
    """Nota media y reparto del periodo.

    El reparto importa tanto como la media: un 3 de media puede ser todo el
    mundo indiferente, o la mitad encantada y la otra mitad furiosa, y son dos
    situaciones que piden cosas distintas.
    """
    enviadas = list(
        (
            await session.execute(
                select(CsatSurvey.score).where(
                    CsatSurvey.tenant_id == tenant_id,
                    CsatSurvey.sent_at >= since,
                    CsatSurvey.sent_at <= until,
                )
            )
        ).scalars()
    )
    notas = [nota for nota in enviadas if nota is not None]

    reparto = {str(nota): 0 for nota in range(1, 6)}
    for nota in notas:
        reparto[str(nota)] += 1

    return {
        "sent": len(enviadas),
        "answered": len(notas),
        "average": round(sum(notas) / len(notas), 2) if notas else None,
        "distribution": reparto,
    }
