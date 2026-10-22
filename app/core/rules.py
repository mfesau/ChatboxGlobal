"""Reglas de automatización: qué se comprueba y qué se hace.

Deliberadamente pequeño. Nada de un lenguaje de condiciones anidadas ni de un
constructor visual de flujos: un puñado de comprobaciones que se cumplen todas
—una «y», no una «o»— y una lista de acciones que se ejecutan en orden. Con eso
se cubre lo que de verdad se repite a mano en un buzón de atención, y se puede
leer la regla entera de un vistazo.

Las acciones no inventan nada: usan las mismas operaciones del repositorio que
usa la consola cuando una persona hace lo mismo a mano, de modo que una
conversación derivada por regla queda exactamente igual —con su apunte en el
historial de derivaciones— que una derivada por alguien.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.envelope import ChannelKind
from app.db.models import AutomationRule
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Acciones que sabe ejecutar. Cualquier otra cosa se ignora con un aviso, en
#: lugar de romper el turno: una regla mal escrita no debe dejar sin respuesta
#: a quien acaba de escribir.
KNOWN_ACTIONS = frozenset(
    {"add_label", "assign_department", "reply", "note", "stop"}
)

#: Tope de la expresión regular que se admite. Una suficientemente retorcida
#: puede tardar segundos en fallar sobre un texto corriente, y la evalúa el
#: propio turno del cliente.
MAX_REGEX_LENGTH = 200


class RuleError(ValueError):
    """La regla no se puede guardar tal como está escrita."""


@dataclass(slots=True)
class Outcome:
    """Resultado de aplicar las reglas a un turno."""

    applied: list[str] = field(default_factory=list)
    #: Cierto cuando alguna regla pidió parar: el asistente no contesta.
    stop: bool = False


# --------------------------------------------------------------------------- #
# Validación
# --------------------------------------------------------------------------- #
def validate(conditions: dict[str, Any], actions: list[Any]) -> None:
    """Comprueba una regla antes de guardarla. Lanza :class:`RuleError`."""
    if not actions:
        raise RuleError("La regla no hace nada: añada al menos una acción")

    patron = conditions.get("regex")
    if patron:
        if len(patron) > MAX_REGEX_LENGTH:
            raise RuleError(
                f"La expresión regular no puede pasar de {MAX_REGEX_LENGTH} caracteres"
            )
        try:
            re.compile(patron, re.IGNORECASE)
        except re.error as exc:
            raise RuleError(f"Expresión regular no válida: {exc}") from exc

    for canal in conditions.get("channels") or []:
        try:
            ChannelKind(canal)
        except ValueError as exc:
            raise RuleError(f"Canal desconocido: {canal}") from exc

    for accion in actions:
        nombre = (accion or {}).get("action")
        if nombre not in KNOWN_ACTIONS:
            raise RuleError(
                f"Acción desconocida: {nombre!r}. Admitidas: {', '.join(sorted(KNOWN_ACTIONS))}"
            )
        if nombre == "add_label" and not accion.get("label_id"):
            raise RuleError("«add_label» necesita una etiqueta")
        if nombre == "assign_department" and not accion.get("department_id"):
            raise RuleError("«assign_department» necesita un departamento")
        if nombre in {"reply", "note"} and not (accion.get("body") or "").strip():
            raise RuleError(f"«{nombre}» necesita un texto")


# --------------------------------------------------------------------------- #
# Selección
# --------------------------------------------------------------------------- #
async def active_rules(
    session: AsyncSession, *, tenant_id: uuid.UUID, department_id: uuid.UUID | None
) -> list[AutomationRule]:
    """Reglas que rigen en esa rama, de mayor a menor prioridad.

    Se traen las de toda la empresa además de las de la rama: una regla general
    —«todo lo que diga "factura" lleva etiqueta»— no debería repetirse en cada
    departamento.
    """
    stmt = select(AutomationRule).where(
        AutomationRule.tenant_id == tenant_id,
        AutomationRule.is_active.is_(True),
    )
    filas = list((await session.execute(stmt)).scalars())
    return sorted(
        (
            fila
            for fila in filas
            if fila.department_id is None or fila.department_id == department_id
        ),
        key=lambda fila: (fila.priority, fila.created_at),
    )


def matches(rule: AutomationRule, *, text: str, channel: Any, is_first: bool) -> bool:
    """Si el turno cumple **todas** las condiciones de la regla.

    Una regla sin condiciones se aplica siempre. Es intencionado: sirve para la
    respuesta de bienvenida, que no depende de lo que digan.
    """
    conditions = rule.conditions or {}
    minusculas = text.lower()

    palabras = conditions.get("keywords") or []
    if palabras and not any(str(palabra).lower() in minusculas for palabra in palabras):
        return False

    patron = conditions.get("regex")
    if patron and not re.search(patron, text, re.IGNORECASE):
        return False

    canales = conditions.get("channels") or []
    if canales and str(channel) not in {str(canal) for canal in canales}:
        return False

    return not (conditions.get("only_first_message") and not is_first)
