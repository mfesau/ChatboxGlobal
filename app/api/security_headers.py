"""Cabeceras de seguridad comunes a todas las respuestas.

La consola y el chatbox no cargan nada de fuera: ni tipografías, ni bibliotecas
de un CDN, ni imágenes remotas. Eso permite una política de contenido estricta,
que es la defensa real contra el guion inyectado —por ejemplo, en el nombre que
un cliente elige para sí mismo o en el cuerpo de un mensaje entrante—: aunque
llegase a colarse en el HTML, el navegador se negaría a ejecutarlo.

``style-src`` sí admite ``'unsafe-inline'``, y no por descuido: el color de
marca del inquilino se sirve dentro de un ``<style>`` en la propia página (ver
``render_branded_page`` en ``app/main.py``). El riesgo de un estilo inyectado es
de otro orden que el de un guion.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

#: Sin comillas ni saltos: va tal cual en la cabecera.
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "form-action 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
    )
)


def install_security_headers(application: FastAPI, *, hsts: bool) -> None:
    """Añade las cabeceras a toda respuesta, incluidas las de error.

    ``hsts`` se activa solo cuando la instalación sirve por HTTPS —el mismo
    interruptor que marca la cookie como ``Secure``—. Anunciarlo desde una
    instalación por HTTP dejaría el navegador incapaz de volver a entrar: la
    cabecera se recuerda durante meses aunque el servidor deje de enviarla.
    """

    @application.middleware("http")
    async def _security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault("X-Frame-Options", "DENY")
        # El navegador no necesita ninguna de las tres en esta aplicación.
        headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        if hsts:
            headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
