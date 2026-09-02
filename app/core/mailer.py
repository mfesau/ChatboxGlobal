"""Envío de correo saliente (SMTP), para las notificaciones de cuenta.

Un fallo de correo —credenciales vencidas, proveedor caído, SMTP sin
configurar— nunca debe impedir un registro o el alta de un agente que ya
quedaron guardados en la base: por eso ``send_email`` no lanza excepciones,
solo registra el resultado y devuelve si el envío se realizó.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from app.config import Settings
from app.logging_setup import get_logger

log = get_logger(__name__)


def _send_sync(*, settings: Settings, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_sender
    message["To"] = to
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        smtp.send_message(message)


async def send_email(*, settings: Settings, to: str, subject: str, body: str) -> bool:
    """Envía un correo de texto plano. Devuelve si el envío se realizó.

    Se ejecuta en un hilo aparte porque ``smtplib`` es bloqueante y, de lo
    contrario, una conexión SMTP lenta congelaría el bucle de eventos para
    todas las peticiones concurrentes.
    """
    if not settings.smtp_enabled:
        log.warning("email_not_sent_smtp_disabled", to=to, subject=subject)
        return False
    try:
        await asyncio.to_thread(_send_sync, settings=settings, to=to, subject=subject, body=body)
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de red o credenciales
        log.error("email_send_failed", to=to, subject=subject, error=str(exc))
        return False
    log.info("email_sent", to=to, subject=subject)
    return True


def welcome_email_body(*, display_name: str | None, app_name: str, base_url: str) -> str:
    """Confirmación enviada a un cliente que acaba de registrarse en el chatbox."""
    name = display_name or "cliente"
    return (
        f"Hola {name},\n\n"
        f"Su cuenta en {app_name} se creó correctamente. Ya puede iniciar sesión "
        f"en {base_url} con su correo y contraseña para continuar la conversación.\n\n"
        "Si usted no solicitó esta cuenta, puede ignorar este mensaje.\n"
    )


def invitation_email_body(
    *,
    display_name: str | None,
    role_label: str,
    email: str,
    temporary_password: str,
    app_name: str,
    base_url: str,
) -> str:
    """Invitación enviada a un supervisor o administrador recién dado de alta."""
    name = display_name or email
    return (
        f"Hola {name},\n\n"
        f"Se creó su cuenta de {role_label} en {app_name}.\n\n"
        f"Correo:      {email}\n"
        f"Contraseña:  {temporary_password}\n\n"
        f"Ingrese en {base_url} y le recomendamos cambiar la contraseña en "
        "cuanto acceda.\n"
    )
