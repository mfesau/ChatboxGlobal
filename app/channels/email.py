"""Adaptador de correo corriente (IMAP de entrada, SMTP de salida).

El único canal sin webhook: nadie avisa cuando llega un correo, hay que ir a
mirar el buzón. De eso se encarga ``app/core/mail_poller.py``, que consulta por
IMAP cada pocos minutos y entrega aquí el mensaje en bruto. Este adaptador solo
traduce, como cualquier otro.

**Un hilo por corresponsal, no por asunto.** El identificador de conversación
es la dirección de quien escribe, igual que en WhatsApp es el número. Encadenar
por ``References`` daría un hilo nuevo cada vez que alguien escribe «otra
consulta más» en vez de responder, y en un buzón de soporte eso reparte a la
misma persona entre varios agentes. El ``Message-ID`` original sí se conserva
en ``reply_to_message_id``: es lo que hace que la respuesta aparezca encadenada
en el programa de correo del cliente.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr, parsedate_to_datetime
from typing import Any

from app.channels.base import ChannelAdapter, register_channel
from app.config import Settings
from app.core.envelope import (
    Attachment,
    ChannelKind,
    ContentType,
    ConversationRef,
    DeliveryReceipt,
    InboundMessage,
    OutboundMessage,
    Party,
)
from app.core.mailer import SmtpAccount, deliver
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Cuántos mensajes recién leídos se guardan para poder extraer sus adjuntos.
#: El orquestador llama a :meth:`fetch_media` justo después de :meth:`parse`,
#: así que basta con muy pocos; el tope solo evita que la memoria crezca sin
#: fin si algo se queda a medias.
MEDIA_CACHE_SIZE = 32

#: Comienzos de línea que separan la respuesta de lo citado. Se comparan sobre
#: la línea ya recortada, en minúsculas.
_QUOTE_MARKERS = (
    re.compile(r"^-{2,}\s*(mensaje original|original message|urspr[üu]ngliche nachricht)"),
    re.compile(r"^_{5,}$"),
    # «El 8 de septiembre, Soporte escribió:», «On Sep 8, Support wrote:»,
    # «Am 8. September schrieb Support:». El alemán pone el nombre detrás del
    # verbo, así que se admite cola después de él.
    re.compile(r"^(el|on|am)\b.{0,160}\b(escribi[óo]|wrote|schrieb)\b.{0,80}:?\s*$"),
    re.compile(r"^de\s*:\s.+"),
    re.compile(r"^from\s*:\s.+"),
    re.compile(r"^von\s*:\s.+"),
)

#: Tipos que no son un adjunto de verdad, sino la firma o el cuerpo alterno.
_SKIP_DISPOSITIONS = frozenset({"inline"})


@register_channel(ChannelKind.EMAIL)
class EmailAdapter(ChannelAdapter):
    """Traductor entre un buzón de correo y el formato canónico."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        #: ``message_id -> mensaje analizado``, para :meth:`fetch_media`. El
        #: correo trae el fichero dentro, no una dirección de la que bajarlo,
        #: así que hay que conservarlo entre el análisis y el guardado.
        self._recent: dict[str, EmailMessage] = {}

    # --------------------------------------------------------------- entrada
    async def parse(
        self, *, payload: dict[str, Any], headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        """Convierte un correo en bruto en un entrante canónico.

        ``payload`` trae ``raw`` con los bytes tal cual los devolvió el
        servidor IMAP: no se reserializa nada, para no perder codificaciones ni
        cabeceras por el camino.
        """
        raw = payload.get("raw")
        if isinstance(raw, str):
            raw = raw.encode("utf-8", "surrogateescape")
        if not raw:
            return []

        message: EmailMessage = message_from_bytes(raw, policy=policy.default)
        sender_name, sender_address = parseaddr(str(message.get("From", "")))
        if not sender_address:
            log.warning("email_without_sender", subject=str(message.get("Subject", "")))
            return []

        message_id = str(message.get("Message-ID", "")).strip() or make_msgid()
        self._remember(message_id, message)

        body = _plain_body(message)
        subject = str(message.get("Subject", "")).strip()
        attachments = _attachments(message, message_id)

        content_type = ContentType.TEXT if body else ContentType.UNKNOWN
        if attachments and not body:
            content_type = attachments[0].content_type

        return [
            InboundMessage(
                channel=ChannelKind.EMAIL,
                conversation=ConversationRef(
                    channel=ChannelKind.EMAIL,
                    channel_conversation_id=sender_address.lower(),
                    channel_account_id=payload.get("mailbox"),
                    reply_to_message_id=message_id,
                    extra={"subject": subject},
                ),
                sender=Party(
                    channel_user_id=sender_address.lower(),
                    display_name=sender_name or None,
                    email=sender_address.lower(),
                ),
                provider_message_id=message_id,
                content_type=content_type,
                # El asunto encabeza el cuerpo: en un correo suele llevar la
                # mitad de la información, y en la consola no se ve por otro
                # sitio —el hilo no tiene título—.
                text=f"{subject}\n\n{body}".strip() if subject else body,
                attachments=attachments,
                timestamp=_received_at(message),
                raw={"subject": subject, "from": sender_address},
            )
        ]

    def _remember(self, message_id: str, message: EmailMessage) -> None:
        if len(self._recent) >= MEDIA_CACHE_SIZE:
            # Se descarta el más antiguo; los diccionarios conservan el orden
            # de inserción desde Python 3.7.
            self._recent.pop(next(iter(self._recent)), None)
        self._recent[message_id] = message

    async def fetch_media(
        self, *, attachment: Any, ref: Any
    ) -> tuple[bytes, str | None] | None:
        """Saca del correo ya leído el contenido de un adjunto.

        No hay descarga: el fichero venía dentro del propio mensaje. Lo que
        hace falta es recuperarlo de donde :meth:`parse` lo dejó.
        """
        token = getattr(attachment, "provider_media_id", None) or ""
        message_id, _, index = token.rpartition("#")
        message = self._recent.get(message_id)
        if message is None or not index.isdigit():
            return None

        for position, part in enumerate(_attachment_parts(message)):
            if position == int(index):
                return part.get_payload(decode=True), part.get_content_type()
        return None

    # ---------------------------------------------------------------- salida
    async def send(
        self, *, ref: ConversationRef, message: OutboundMessage
    ) -> DeliveryReceipt:
        credentials = ref.extra.get("credentials", {})
        account = SmtpAccount.from_credentials(credentials)
        if account is None:
            return DeliveryReceipt.failed(
                "not_configured",
                "Faltan los datos de SMTP del buzón; complételos en «Cuentas de canal»",
            )

        outgoing = self._compose(ref, message, account)
        try:
            await deliver(account, outgoing)
        except Exception as exc:  # noqa: BLE001 - red, credenciales o rechazo del servidor
            # Casi todo fallo de SMTP es pasajero (servidor caído, límite de
            # envío); los definitivos —credenciales mal, buzón inexistente— se
            # agotan en los reintentos y acaban en `dead`, que es visible.
            return DeliveryReceipt.failed("smtp_error", str(exc)[:500], retryable=True)

        return DeliveryReceipt.sent(outgoing["Message-ID"])

    def _compose(
        self, ref: ConversationRef, message: OutboundMessage, account: SmtpAccount
    ) -> EmailMessage:
        outgoing = EmailMessage()
        outgoing["Message-ID"] = make_msgid()
        outgoing["From"] = formataddr((self.settings.app_name, account.sender))
        outgoing["To"] = ref.channel_conversation_id
        outgoing["Subject"] = _reply_subject(ref.extra.get("subject"))

        # Encadena la respuesta en el programa de correo del cliente. Sin estas
        # dos cabeceras cada respuesta abre un hilo suelto en su bandeja.
        if ref.reply_to_message_id:
            outgoing["In-Reply-To"] = ref.reply_to_message_id
            outgoing["References"] = ref.reply_to_message_id

        text = (message.text or "").strip()
        if message.quick_replies:
            # El correo no tiene botones: se ofrecen como una lista numerada,
            # igual que WhatsApp hace cuando se pasa de tres.
            opciones = "\n".join(
                f"{index}. {option.get('title', '')}"
                for index, option in enumerate(message.quick_replies, start=1)
            )
            text = f"{text}\n\n{opciones}".strip()
        outgoing.set_content(text or " ")
        return outgoing


# --------------------------------------------------------------------------- #
# Análisis del mensaje
# --------------------------------------------------------------------------- #
def _plain_body(message: EmailMessage) -> str:
    """Texto de la respuesta, sin lo citado del mensaje anterior."""
    try:
        part = message.get_body(preferencelist=("plain", "html"))
    except Exception:  # noqa: BLE001 - correo mal formado
        part = None
    if part is None:
        return ""

    content = part.get_content()
    if part.get_content_type() == "text/html":
        content = _strip_html(content)
    return _strip_quoted(content).strip()


def _strip_html(html: str) -> str:
    """Reducción deliberadamente tosca de HTML a texto.

    No se trae un analizador entero para esto: el objetivo no es reproducir el
    correo con fidelidad, sino que quien lo atienda pueda leerlo en la consola.
    El original queda íntegro en el buzón.
    """
    without_blocks = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    with_breaks = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", without_blocks)
    text = re.sub(r"(?s)<[^>]+>", "", with_breaks)
    for entity, char in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
    ):
        text = text.replace(entity, char)
    return re.sub(r"\n{3,}", "\n\n", text)


def _strip_quoted(text: str) -> str:
    """Corta el mensaje en cuanto empieza lo citado.

    Conservador a propósito: ante la duda se deja de más. Perder el final de un
    mensaje legítimo es peor que arrastrar unas líneas citadas, porque lo
    primero no se nota y lo segundo sí.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip().lower()
        if not stripped:
            continue
        if any(marker.match(stripped) for marker in _QUOTE_MARKERS):
            return "\n".join(lines[:index]).rstrip()
        # Un bloque de citas «>» que llega hasta el final del mensaje.
        if stripped.startswith(">") and all(
            not rest.strip() or rest.lstrip().startswith(">") for rest in lines[index:]
        ):
            return "\n".join(lines[:index]).rstrip()
    return text


def _attachment_parts(message: EmailMessage) -> list[Any]:
    """Partes que son un fichero adjunto de verdad, en orden."""
    parts = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get_content_disposition() or "").lower()
        con_nombre = part.get_filename() and disposition not in _SKIP_DISPOSITIONS
        if disposition == "attachment" or con_nombre:
            parts.append(part)
    return parts


def _attachments(message: EmailMessage, message_id: str) -> list[Attachment]:
    resultado = []
    for index, part in enumerate(_attachment_parts(message)):
        mime = part.get_content_type()
        resultado.append(
            Attachment(
                content_type=_content_type_for(mime),
                # El fichero viene dentro del correo, no en un servidor del que
                # bajarlo: el identificador apunta a la parte, y `fetch_media`
                # la recupera del mensaje que `parse` guardó.
                provider_media_id=f"{message_id}#{index}",
                mime_type=mime,
                filename=part.get_filename(),
            )
        )
    return resultado


def _content_type_for(mime: str) -> ContentType:
    if mime.startswith("image/"):
        return ContentType.IMAGE
    if mime.startswith("video/"):
        return ContentType.VIDEO
    if mime.startswith("audio/"):
        return ContentType.AUDIO
    return ContentType.DOCUMENT


def _reply_subject(subject: str | None) -> str:
    limpio = (subject or "").strip()
    if not limpio:
        return "Re: su consulta"
    return limpio if limpio.lower().startswith("re:") else f"Re: {limpio}"


def _received_at(message: EmailMessage) -> datetime:
    try:
        fecha = parsedate_to_datetime(str(message.get("Date", "")))
    except (TypeError, ValueError):
        return datetime.now(UTC)
    if fecha is None:
        return datetime.now(UTC)
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)
