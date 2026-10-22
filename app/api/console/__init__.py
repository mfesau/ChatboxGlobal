"""Consola de equipo: cola común, cartera propia, derivaciones y supervisión.

Reglas de visibilidad, aplicadas en SQL y no en memoria:

* La **cola común** —conversaciones sin responsable— es el punto único por el que
  entra todo, sea de WhatsApp, de Microsoft Teams o del chatbox web. Cualquier
  agente la ve y puede tomar trabajo de ella.
* Un **agente** ve además su propia cartera. No ve la de sus compañeros.
* **Supervisión** ve la totalidad de las conversaciones del inquilino.

Derivar no mueve ni copia nada: la conversación es la misma fila y conserva su
historial íntegro. Solo cambia el responsable, y el cambio queda anotado en
``assignments`` con autor, destinatario y motivo.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.console import (
    channels,
    configuration,
    contacts,
    conversations,
    departments,
    hotel,
    team,
)

#: Un solo router para ``app/main.py``. Los temas se incluyen en el orden en
#: que estaban en el antiguo ``console.py``, que es el que decide qué ruta
#: gana cuando dos patrones encajan con la misma URL.
router = APIRouter()
router.include_router(conversations.router)
router.include_router(contacts.router)
router.include_router(team.router)
router.include_router(departments.router)
router.include_router(hotel.router)
router.include_router(configuration.router)
router.include_router(channels.router)
