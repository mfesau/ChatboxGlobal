"""Página pública de la encuesta de satisfacción.

Solo la usan el correo y el SMS, que no tienen botones. El testigo del enlace
es la única credencial: no hay sesión, y no la puede haber —quien recibe un SMS
no tiene por qué tener cuenta en el chatbox—.

Por eso el testigo es largo y aleatorio, y la página no revela nada de la
conversación: ni el texto, ni quién atendió, ni con quién se habló. Solo
pregunta una nota. Un enlace filtrado o adivinado permite, como mucho, dejar
una puntuación falsa en una conversación concreta.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, status
from fastapi.responses import HTMLResponse

from app.api.deps import SessionDep
from app.core import csat
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["encuesta"])


def _page(title: str, body: str, accent: str = "#2f5bd7") -> str:
    """Página mínima y autónoma.

    Deliberadamente sin hoja de estilos ni JavaScript propios: se abre desde el
    cliente de correo de cualquiera, muchas veces en un móvil, y todo lo que
    cargue de fuera es una cosa más que puede no llegar.
    """
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
          margin: 0; padding: 32px 20px; background: #f6f7f9; color: #1b1d21; }}
  main {{ max-width: 520px; margin: 0 auto; background: #fff; border-radius: 12px;
          padding: 28px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  h1 {{ font-size: 20px; margin: 0 0 16px; }}
  .scores {{ display: flex; gap: 8px; margin: 20px 0; }}
  .scores button {{ flex: 1; padding: 14px 0; font-size: 18px; cursor: pointer;
                    border: 1px solid #d6d9de; border-radius: 8px; background: #fff; }}
  .scores button:hover {{ border-color: {accent}; color: {accent}; }}
  textarea {{ width: 100%; box-sizing: border-box; min-height: 84px; padding: 10px;
              border: 1px solid #d6d9de; border-radius: 8px; font: inherit; }}
  p.muted {{ color: #5c626b; font-size: 14px; }}
</style>
</head>
<body><main>{body}</main></body>
</html>
"""


@router.get("/csat/{token}", include_in_schema=False)
async def survey_page(token: str, session: SessionDep) -> HTMLResponse:
    """Formulario de la encuesta. Sin sesión: el testigo es la credencial."""
    survey = await csat.survey_by_token(session, token)
    if survey is None:
        # 404 sin más detalle: distinguir «no existe» de «ya caducó» no ayuda a
        # nadie y confirmaría qué testigos son válidos.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Encuesta no encontrada"
        )

    if survey.responded_at is not None:
        return HTMLResponse(
            _page(
                "Gracias",
                "<h1>Ya recibimos su valoración</h1>"
                "<p class='muted'>Gracias por dedicarnos un momento.</p>",
            )
        )

    botones = "".join(
        f'<button type="submit" name="score" value="{nota}">{nota}</button>'
        for nota in range(1, 6)
    )
    cuerpo = f"""
      <h1>¿Cómo valora la atención recibida?</h1>
      <p class="muted">1 es «muy mal» y 5, «muy bien».</p>
      <form method="post" action="/csat/{token}">
        <div class="scores">{botones}</div>
        <label for="comment" class="muted">¿Quiere contarnos algo más? (opcional)</label>
        <textarea id="comment" name="comment" maxlength="2000"></textarea>
      </form>
    """
    return HTMLResponse(_page("Su opinión", cuerpo))


@router.post("/csat/{token}", include_in_schema=False)
async def submit_survey(
    token: str,
    session: SessionDep,
    score: Annotated[int, Form()],
    comment: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """Guarda la nota. Contestar dos veces no sobrescribe la primera."""
    survey = await csat.survey_by_token(session, token)
    if survey is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Encuesta no encontrada"
        )
    if not 1 <= score <= 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="La nota va del 1 al 5"
        )

    if survey.responded_at is None:
        await csat.record_score(session, survey, score=score, comment=comment)

    return HTMLResponse(
        _page(
            "Gracias",
            "<h1>Gracias por su valoración</h1>"
            "<p class='muted'>Nos ayuda a mejorar la atención.</p>",
        )
    )
