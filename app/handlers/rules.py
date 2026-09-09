"""Aplicación de las reglas de automatización dentro del turno.

Va después de los comandos —«/agente» debe seguir funcionando pase lo que
pase— y antes del asistente, para que una conversación derivada por regla
llegue a quien corresponde sin que el modelo gaste un turno contestando algo
genérico por el camino.

Un fallo aquí no corta la conversación: la regla que falla se anota en el log y
el turno sigue. Una regla mal escrita es un problema de configuración, no una
razón para dejar sin respuesta a quien acaba de escribir.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from app.core import rules
from app.core.pipeline import Handler, NextFn, TurnContext
from app.db import repositories as repo
from app.logging_setup import get_logger

log = get_logger(__name__)


class RulesHandler(Handler):
    """Evalúa las reglas activas y ejecuta las que se cumplan."""

    name: ClassVar[str] = "rules"

    async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
        texto = ctx.text
        if not texto and not ctx.inbound.action:
            await next_()
            return

        aplicables = await rules.active_rules(
            ctx.session,
            tenant_id=ctx.tenant.id,
            department_id=ctx.conversation.department_id,
        )
        if not aplicables:
            await next_()
            return

        # «Primer mensaje» es el que llega a una conversación sin historial
        # previo: el que acaba de guardarse es el único que hay.
        es_primero = len(ctx.history) <= 1

        outcome = rules.Outcome()
        for rule in aplicables:
            if not rules.matches(
                rule, text=texto, channel=ctx.conversation.channel, is_first=es_primero
            ):
                continue
            try:
                await self._apply(ctx, rule, outcome)
            except Exception:
                # Una regla rota no debe dejar el turno sin respuesta.
                log.exception("rule_failed", rule=rule.name, rule_id=str(rule.id))
                continue
            if outcome.stop:
                break

        if outcome.applied:
            log.info(
                "rules_applied",
                conversation=str(ctx.conversation.id),
                rules=outcome.applied,
                stop=outcome.stop,
            )
            ctx.scratch["rules_applied"] = outcome.applied

        if outcome.stop:
            # La regla ya dijo lo que había que decir; el asistente no añade
            # nada. Si además no dejó respuesta, el turno queda en silencio a
            # propósito: es lo que pide una regla de «esto lo ve una persona».
            return

        await next_()

    async def _apply(
        self, ctx: TurnContext, rule: Any, outcome: rules.Outcome
    ) -> None:
        """Ejecuta las acciones de una regla, en orden."""
        for accion in rule.actions or []:
            nombre = (accion or {}).get("action")

            if nombre == "add_label":
                await self._add_label(ctx, accion["label_id"])
            elif nombre == "assign_department":
                await self._assign_department(ctx, rule, accion["department_id"])
            elif nombre == "reply":
                ctx.reply(accion["body"])
            elif nombre == "note":
                await repo.add_internal_note(
                    ctx.session,
                    conversation=ctx.conversation,
                    # Sin agente: la nota la deja la regla, no una persona.
                    agent_id=None,
                    body=f"[{rule.name}] {accion['body']}",
                )
            elif nombre == "stop":
                outcome.stop = True
            else:
                log.warning("rule_action_unknown", rule=rule.name, action=nombre)
                continue

        outcome.applied.append(rule.name)

    async def _add_label(self, ctx: TurnContext, label_id: str) -> None:
        etiqueta = uuid.UUID(str(label_id))
        await ctx.session.refresh(ctx.conversation, attribute_names=["labels"])
        actuales = [row.id for row in ctx.conversation.labels]
        # Que la regla se cumpla dos veces no debe duplicar la etiqueta.
        if etiqueta in actuales:
            return
        await repo.set_conversation_labels(
            ctx.session, conversation=ctx.conversation, label_ids=[*actuales, etiqueta]
        )

    async def _assign_department(
        self, ctx: TurnContext, rule: Any, department_id: str
    ) -> None:
        destino = uuid.UUID(str(department_id))
        if ctx.conversation.department_id == destino:
            return

        department = await repo.get_department(ctx.session, destino)
        if department is None or department.tenant_id != ctx.tenant.id:
            log.warning("rule_department_missing", rule=rule.name, department=str(destino))
            return

        # La misma operación que usa la consola al derivar a mano: queda su
        # apunte en el historial de derivaciones, con el motivo.
        await repo.transfer_to_department(
            ctx.session,
            conversation=ctx.conversation,
            department_id=department.id,
            by_agent_id=None,
            note=f"Regla «{rule.name}»",
        )
