"""Envío de correo saliente (SMTP).

Cubre dos usos con distinto remitente. Las **notificaciones de cuenta**
—confirmación de registro, invitación a un agente— salen de la cuenta global
de ``.env`` mediante :func:`send_email`, que nunca lanza: un fallo de correo no
debe tumbar un registro que ya quedó guardado en la base.

El **canal de correo** (``app/channels/email.py``) usa :class:`SmtpAccount` y
:func:`deliver`, con las credenciales propias de cada buzón dado de alta en la
consola. Ahí sí interesa la excepción: quien la recibe es la cola de salida,
que sabe reintentar.
"""

from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from app.config import Settings
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Tiempo máximo de espera de una conexión SMTP, en segundos.
SMTP_TIMEOUT_S = 20


@dataclass(slots=True)
class SmtpAccount:
    """Servidor de salida concreto, con sus credenciales.

    Existe para que el canal de correo no tenga que fabricarse un ``Settings``
    falso con los valores de un buzón: cada cuenta dada de alta trae los suyos.
    """

    host: str
    port: int
    username: str
    password: str
    sender: str
    use_tls: bool = True

    @classmethod
    def from_credentials(cls, credentials: dict[str, str]) -> SmtpAccount | None:
        """Construye la cuenta desde lo guardado cifrado, o ``None`` si falta algo."""
        host = credentials.get("smtp_host")
        username = credentials.get("smtp_username")
        password = credentials.get("smtp_password")
        if not (host and username and password):
            return None
        try:
            port = int(credentials.get("smtp_port") or 587)
        except ValueError:
            port = 587
        return cls(
            host=host,
            port=port,
            username=username,
            password=password,
            sender=credentials.get("from_email") or username,
            use_tls=str(credentials.get("smtp_use_tls", "true")).lower() != "false",
        )


def _deliver_sync(account: SmtpAccount, message: EmailMessage) -> None:
    with smtplib.SMTP(account.host, account.port, timeout=SMTP_TIMEOUT_S) as smtp:
        if account.use_tls:
            smtp.starttls()
        smtp.login(account.username, account.password)
        smtp.send_message(message)


async def deliver(account: SmtpAccount, message: EmailMessage) -> None:
    """Entrega un mensaje ya compuesto. **Lanza** si el envío falla.

    A diferencia de :func:`send_email`, aquí la excepción es lo útil: quien
    llama es la cola de salida, que distingue un fallo pasajero de uno
    definitivo y reintenta con retroceso.

    Corre en un hilo aparte porque ``smtplib`` es bloqueante y, si no, una
    conexión lenta congelaría el bucle de eventos entero.
    """
    await asyncio.to_thread(_deliver_sync, account, message)


def _send_sync(*, settings: Settings, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_sender
    message["To"] = to
    message.set_content(body)

    account = SmtpAccount(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password.get_secret_value(),
        sender=settings.smtp_sender,
        use_tls=settings.smtp_use_tls,
    )
    _deliver_sync(account, message)


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
