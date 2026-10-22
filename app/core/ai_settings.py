"""Modelo y esfuerzo de razonamiento del asistente, elegidos desde la consola.

La clave de OpenRouter se queda en el ``.env``: es un secreto y se gestiona
como los demás. Lo que no es secreto —qué modelo contesta y cuánto piensa antes
de hacerlo— es una decisión de quien administra, que cambia con los precios y
con lo que se aprende al usarlo, y no debería exigir tocar el servidor.

Se guarda en ``Tenant.settings`` bajo ``ai``. Sin nada guardado manda
``OPENROUTER_MODEL`` del ``.env`` y el esfuerzo por omisión del modelo.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.logging_setup import get_logger

log = get_logger(__name__)

AI_SETTINGS_KEY = "ai"

#: Niveles de esfuerzo que entiende OpenRouter, de menos a más. ``default`` no
#: se envía: deja que cada modelo use el suyo, que es lo prudente cuando no se
#: sabe si el modelo razona siquiera.
EFFORTS = ("default", "none", "minimal", "low", "medium", "high", "xhigh")

#: ``proveedor/modelo`` con sufijos como ``:free`` o ``:nitro``. Tope de 64:
#: es el ancho de ``ai_runs.model``, donde queda anotado cada turno.
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._\-]+/[A-Za-z0-9._\-:]+$")
MODEL_MAX_LENGTH = 64


@dataclass(frozen=True, slots=True)
class AiChoice:
    model: str
    effort: str


def read(tenant: Any, settings: Any) -> AiChoice:
    """Lo elegido para esta instalación, con los valores del ``.env`` de reserva."""
    stored = ((getattr(tenant, "settings", None) or {}).get(AI_SETTINGS_KEY)) or {}
    if not isinstance(stored, dict):
        stored = {}
    model = stored.get("model") or settings.openrouter_model
    effort = stored.get("effort") if stored.get("effort") in EFFORTS else "default"
    return AiChoice(model=model, effort=effort)


def reasoning_payload(effort: str) -> dict[str, Any] | None:
    """Parámetro ``reasoning`` de la petición, o ``None`` para no enviarlo.

    ``exclude`` pide que el razonamiento no viaje en la respuesta: se cobra
    igual, pero no hace falta transmitirlo para luego tirarlo.
    """
    if effort == "default":
        return None
    return {"effort": effort, "exclude": True}


def is_valid_model(model: str) -> bool:
    return len(model) <= MODEL_MAX_LENGTH and bool(MODEL_PATTERN.match(model))


# --------------------------------------------------------------------------- #
# Catálogo de modelos
# --------------------------------------------------------------------------- #
#: El catálogo de OpenRouter es público y cambia poco; se pide una vez por hora
#: como mucho. Abrir la pestaña no debería costar una petición a internet.
CATALOG_TTL_S = 3600
_catalog: dict[str, Any] = {"at": 0.0, "models": None}


def _summarise(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Lo que la consola necesita de cada modelo, o ``None`` si no sirve.

    Sin herramientas no sirve: el asistente las necesita para derivar a una
    persona y para consultar la base de conocimiento.
    """
    parametros = entry.get("supported_parameters") or []
    if "tools" not in parametros:
        return None
    precios = entry.get("pricing") or {}

    def por_millon(clave: str) -> float | None:
        try:
            return round(float(precios.get(clave)) * 1_000_000, 4)
        except (TypeError, ValueError):
            return None

    return {
        "id": entry.get("id"),
        "name": entry.get("name") or entry.get("id"),
        "input_modalities": (entry.get("architecture") or {}).get("input_modalities") or [],
        "reasoning": "reasoning" in parametros,
        "prompt_per_million": por_millon("prompt"),
        "completion_per_million": por_millon("completion"),
        "context_length": entry.get("context_length"),
    }


async def fetch_catalog(
    settings: Any, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[dict[str, Any]]:
    """Modelos de OpenRouter que admiten herramientas, ordenados por nombre.

    Si OpenRouter no contesta se devuelve lista vacía y la consola deja
    escribir el identificador a mano: no poder ver el catálogo no debe
    impedir cambiar de modelo.
    """
    ahora = time.monotonic()
    if _catalog["models"] is not None and ahora - _catalog["at"] < CATALOG_TTL_S:
        return _catalog["models"]
    url = f"{settings.openrouter_base_url.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as http:
            response = await http.get(url)
            response.raise_for_status()
            entries = response.json().get("data") or []
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("openrouter_catalog_unavailable", error=str(exc)[:200])
        return _catalog["models"] or []
    models = sorted(
        (m for m in (_summarise(e) for e in entries) if m and m["id"]),
        key=lambda m: m["id"],
    )
    _catalog.update(at=ahora, models=models)
    return models


def forget_catalog() -> None:
    """Vacía la caché; para las pruebas."""
    _catalog.update(at=0.0, models=None)


# --------------------------------------------------------------------------- #
# Por departamento
# --------------------------------------------------------------------------- #
#: Clave dentro de ``Department.enabled_modules``, junto a los demás módulos
#: de la rama (hotel, encuesta). Sin nada guardado el asistente responde y no
#: hay instrucciones propias: así funcionaba antes de que existiera.
DEPARTMENT_KEY = "assistant"
INSTRUCTIONS_MAX_LENGTH = 4_000


@dataclass(frozen=True, slots=True)
class DepartmentAssistant:
    enabled: bool
    instructions: str


def for_department(department: Any) -> DepartmentAssistant:
    """Cómo atiende el asistente en ese departamento."""
    stored = ((getattr(department, "enabled_modules", None) or {}).get(DEPARTMENT_KEY)) or {}
    if not isinstance(stored, dict):
        stored = {}
    return DepartmentAssistant(
        enabled=stored.get("enabled", True) is not False,
        instructions=str(stored.get("instructions") or "").strip(),
    )


def set_for_department(department: Any, *, enabled: bool, instructions: str) -> None:
    """Guarda la configuración, reasignando el diccionario para que se note.

    Mutarlo en el sitio pasaría inadvertido para SQLAlchemy en una columna
    JSON, igual que con los demás módulos.
    """
    modules = dict(department.enabled_modules or {})
    modules[DEPARTMENT_KEY] = {"enabled": enabled, "instructions": instructions.strip()}
    department.enabled_modules = modules
