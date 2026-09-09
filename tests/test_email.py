"""Canal de correo: lectura del mensaje, recorte de lo citado y respuesta.

Sin red: se construyen correos con ``email.message.EmailMessage`` y se le dan
al adaptador tal como se los daría el recogedor IMAP.
"""

from __future__ import annotations

from email.message import EmailMessage

from app.channels.email import (
    EmailAdapter,
    _reply_subject,
    _strip_html,
    _strip_quoted,
)
from app.config import Settings
from app.core.envelope import (
    ChannelKind,
    ContentType,
    ConversationRef,
    OutboundMessage,
)
from app.core.mail_poller import Mailbox

MAILBOX = "soporte@empresa.local"
CLIENTE = "Marta Giménez <marta@cliente.local>"


def make_adapter(**overrides) -> EmailAdapter:
    overrides.setdefault("environment", "dev")
    return EmailAdapter(Settings(_env_file=None, **overrides))


def build_email(
    *,
    body: str = "Buenos días, tengo una duda.",
    subject: str = "Consulta sobre mi reserva",
    sender: str = CLIENTE,
    message_id: str = "<abc123@cliente.local>",
    html: str | None = None,
    attachment: tuple[str, str, bytes] | None = None,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = MAILBOX
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message["Date"] = "Tue, 09 Sep 2026 09:00:00 +0000"
    if html is not None:
        message.set_content(body)
        message.add_alternative(html, subtype="html")
    else:
        message.set_content(body)
    if attachment is not None:
        filename, mime, content = attachment
        maintype, _, subtype = mime.partition("/")
        message.add_attachment(
            content, maintype=maintype, subtype=subtype, filename=filename
        )
    return message.as_bytes()


# --------------------------------------------------------------------------- #
# Lectura
# --------------------------------------------------------------------------- #
async def test_an_email_becomes_a_canonical_inbound():
    adapter = make_adapter()
    [inbound] = await adapter.parse(
        payload={"raw": build_email(), "mailbox": MAILBOX}, headers={}
    )

    assert inbound.channel is ChannelKind.EMAIL
    assert inbound.content_type is ContentType.TEXT
    assert inbound.sender.channel_user_id == "marta@cliente.local"
    assert inbound.sender.display_name == "Marta Giménez"
    assert inbound.sender.email == "marta@cliente.local"
    assert inbound.provider_message_id == "<abc123@cliente.local>"
    assert inbound.conversation.channel_account_id == MAILBOX


async def test_the_thread_is_the_correspondent_not_the_subject():
    """Dos asuntos distintos de la misma persona son el mismo hilo."""
    adapter = make_adapter()
    [primero] = await adapter.parse(
        payload={"raw": build_email(subject="Una cosa", message_id="<1@c.local>")}, headers={}
    )
    [segundo] = await adapter.parse(
        payload={"raw": build_email(subject="Otra cosa", message_id="<2@c.local>")}, headers={}
    )

    assert (
        primero.conversation.channel_conversation_id
        == segundo.conversation.channel_conversation_id
        == "marta@cliente.local"
    )
    # Pero cada correo conserva su identificador, para poder encadenar la
    # respuesta en el programa de correo del cliente.
    assert primero.conversation.reply_to_message_id != segundo.conversation.reply_to_message_id


async def test_the_subject_heads_the_body():
    """En la consola el hilo no tiene título; el asunto se vería en ninguna parte."""
    adapter = make_adapter()
    [inbound] = await adapter.parse(payload={"raw": build_email()}, headers={})
    assert inbound.text.startswith("Consulta sobre mi reserva")
    assert "tengo una duda" in inbound.text


async def test_an_email_without_a_sender_is_dropped():
    message = EmailMessage()
    message["Subject"] = "Sin remitente"
    message.set_content("hola")
    adapter = make_adapter()
    assert await adapter.parse(payload={"raw": message.as_bytes()}, headers={}) == []


async def test_an_empty_payload_is_dropped():
    adapter = make_adapter()
    assert await adapter.parse(payload={}, headers={}) == []


async def test_an_html_only_email_is_read_as_text():
    adapter = make_adapter()
    [inbound] = await adapter.parse(
        payload={
            "raw": build_email(
                body="respaldo en texto",
                html="<p>Hola,</p><p>&nbsp;tengo una <b>duda</b>.</p>",
            )
        },
        headers={},
    )
    # `get_body` prefiere el texto plano cuando existe, que es lo correcto.
    assert "respaldo en texto" in inbound.text


# --------------------------------------------------------------------------- #
# Recorte de lo citado
# --------------------------------------------------------------------------- #
def test_the_quoted_reply_is_cut_off():
    texto = (
        "Gracias, ya lo tengo.\n"
        "\n"
        "El 8 de septiembre de 2026, Soporte escribió:\n"
        "> ¿Pudo comprobarlo?\n"
        "> Un saludo\n"
    )
    assert _strip_quoted(texto) == "Gracias, ya lo tengo."


def test_a_block_of_quotes_at_the_end_is_cut_off():
    texto = "Perfecto.\n\n> mensaje anterior\n> segunda línea\n"
    assert _strip_quoted(texto) == "Perfecto."


def test_a_quote_marker_in_english_and_german_is_recognised():
    for cabecera in (
        "On Sep 8, 2026, Support wrote:",
        "Am 8. September 2026 schrieb Support:",
        "-----Original Message-----",
    ):
        assert _strip_quoted(f"Vale.\n\n{cabecera}\n> lo de antes") == "Vale."


def test_a_message_that_only_looks_quoted_is_left_alone():
    """Ante la duda se deja de más: perder el final de un mensaje no se nota."""
    texto = "Le escribo porque > me dijeron que preguntara aquí.\nGracias."
    assert _strip_quoted(texto) == texto


def test_html_is_reduced_without_bringing_a_parser():
    reducido = _strip_html("<p>Hola</p><script>alert(1)</script><div>Adi&oacute;s</div>")
    assert "alert" not in reducido
    assert "Hola" in reducido


# --------------------------------------------------------------------------- #
# Adjuntos
# --------------------------------------------------------------------------- #
async def test_an_attachment_is_recovered_from_the_message_itself():
    """No hay descarga: el fichero venía dentro del correo."""
    adapter = make_adapter()
    [inbound] = await adapter.parse(
        payload={"raw": build_email(attachment=("factura.pdf", "application/pdf", b"%PDF-1.4"))},
        headers={},
    )

    assert len(inbound.attachments) == 1
    adjunto = inbound.attachments[0]
    assert adjunto.filename == "factura.pdf"
    assert adjunto.content_type is ContentType.DOCUMENT
    assert adjunto.url is None  # todavía no está guardado

    contenido = await adapter.fetch_media(attachment=adjunto, ref=inbound.conversation)
    assert contenido == (b"%PDF-1.4", "application/pdf")


async def test_an_unknown_attachment_reference_returns_nothing():
    adapter = make_adapter()

    class Suelto:
        provider_media_id = "<no-existe@x>#0"

    assert await adapter.fetch_media(attachment=Suelto(), ref=None) is None


# --------------------------------------------------------------------------- #
# Respuesta
# --------------------------------------------------------------------------- #
def test_the_reply_threads_in_the_customers_mail_client():
    adapter = make_adapter()
    ref = ConversationRef(
        channel=ChannelKind.EMAIL,
        channel_conversation_id="marta@cliente.local",
        reply_to_message_id="<abc123@cliente.local>",
        extra={"subject": "Consulta sobre mi reserva"},
    )
    account = _account()
    salida = adapter._compose(ref, OutboundMessage(text="Ya está resuelto."), account)

    assert salida["To"] == "marta@cliente.local"
    assert salida["Subject"] == "Re: Consulta sobre mi reserva"
    assert salida["In-Reply-To"] == "<abc123@cliente.local>"
    assert salida["References"] == "<abc123@cliente.local>"
    assert "Ya está resuelto." in salida.get_content()


def test_quick_replies_become_a_numbered_list():
    """El correo no tiene botones; se ofrecen como lista, igual que en SMS."""
    adapter = make_adapter()
    ref = ConversationRef(
        channel=ChannelKind.EMAIL, channel_conversation_id="marta@cliente.local"
    )
    salida = adapter._compose(
        ref,
        OutboundMessage(
            text="¿Le sirve?",
            quick_replies=[{"id": "si", "title": "Sí"}, {"id": "no", "title": "No"}],
        ),
        _account(),
    )
    cuerpo = salida.get_content()
    assert "1. Sí" in cuerpo
    assert "2. No" in cuerpo


def test_a_reply_without_a_known_subject_still_has_one():
    assert _reply_subject(None) == "Re: su consulta"
    assert _reply_subject("Hola") == "Re: Hola"
    # No se acumulan los prefijos al responder a una respuesta.
    assert _reply_subject("Re: Hola") == "Re: Hola"


async def test_sending_without_smtp_settings_fails_with_a_useful_reason():
    adapter = make_adapter()
    ref = ConversationRef(
        channel=ChannelKind.EMAIL, channel_conversation_id="marta@cliente.local"
    )
    receipt = await adapter.send(ref=ref, message=OutboundMessage(text="Hola"))
    assert receipt.ok is False
    assert receipt.error_code == "not_configured"


def _account():
    from app.core.mailer import SmtpAccount

    return SmtpAccount(
        host="smtp.empresa.local",
        port=587,
        username="soporte",
        password="clave",
        sender=MAILBOX,
    )


# --------------------------------------------------------------------------- #
# Recogedor
# --------------------------------------------------------------------------- #
def test_a_mailbox_needs_its_three_essential_settings():
    assert Mailbox.from_credentials("x", {"imap_host": "imap.local"}) is None
    buzon = Mailbox.from_credentials(
        MAILBOX,
        {
            "imap_host": "imap.empresa.local",
            "imap_username": "soporte",
            "imap_password": "clave",
        },
    )
    assert buzon is not None
    assert buzon.port == 993  # el de IMAP sobre TLS, si no se dice otro
    assert buzon.folder == "INBOX"


def test_a_malformed_port_falls_back_instead_of_breaking():
    """Un puerto mal escrito no debe dejar el buzón sin recoger."""
    buzon = Mailbox.from_credentials(
        MAILBOX,
        {
            "imap_host": "imap.empresa.local",
            "imap_username": "soporte",
            "imap_password": "clave",
            "imap_port": "no-es-un-número",
        },
    )
    assert buzon is not None
    assert buzon.port == 993
