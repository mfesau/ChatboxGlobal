"""Adaptador de Twilio: firma, lectura del formulario y envío.

La firma se calcula de verdad, con ``hmac``, igual que en las pruebas de
WhatsApp: comprobarla contra un valor escrito a mano no verificaría nada.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import urlencode

import pytest

from app.channels.base import SignatureError
from app.channels.twilio import TEXT_LIMIT, TwilioAdapter, _build_payloads, _signed_payload
from app.config import Settings
from app.core.envelope import (
    Attachment,
    ChannelKind,
    ContentType,
    ConversationRef,
    OutboundMessage,
)

ACCOUNT_SID = "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
AUTH_TOKEN = "un-token-de-autenticacion"
NUESTRO_NUMERO = "+34600111222"
SU_NUMERO = "+34699888777"
URL = "https://chat.empresa.local/webhooks/sms"


def make_adapter(**overrides) -> TwilioAdapter:
    overrides.setdefault("environment", "dev")
    return TwilioAdapter(Settings(_env_file=None, **overrides))


def form(**fields) -> dict[str, str]:
    """Un formulario de mensaje entrante, como el que manda Twilio."""
    return {
        "MessageSid": "SM1234567890",
        "AccountSid": ACCOUNT_SID,
        "From": SU_NUMERO,
        "To": NUESTRO_NUMERO,
        "Body": "Hola, ¿siguen abiertos?",
        "NumMedia": "0",
        **fields,
    }


def sign(fields: dict[str, str], *, url: str = URL, token: str = AUTH_TOKEN) -> tuple[bytes, str]:
    """Devuelve el cuerpo codificado y su firma, calculada de verdad."""
    body = urlencode(fields).encode()
    firmado = _signed_payload(url, body)
    digest = hmac.new(token.encode(), firmado.encode("utf-8"), hashlib.sha1).digest()
    return body, base64.b64encode(digest).decode()


def ref() -> ConversationRef:
    return ConversationRef(
        channel=ChannelKind.SMS,
        channel_conversation_id=SU_NUMERO,
        extra={
            "credentials": {
                "account_sid": ACCOUNT_SID,
                "auth_token": AUTH_TOKEN,
                "from_number": NUESTRO_NUMERO,
            }
        },
    )


# --------------------------------------------------------------------------- #
# Firma
# --------------------------------------------------------------------------- #
async def test_a_correctly_signed_request_is_accepted():
    adapter = make_adapter()
    campos = form()
    body, firma = sign(campos)

    await adapter.verify_request(
        headers={"x-twilio-signature": firma},
        body=body,
        credentials=[{"auth_token": AUTH_TOKEN}],
        url=URL,
    )


async def test_the_url_is_part_of_the_signature():
    """Twilio firma la dirección además del cuerpo; por eso hace falta `url`."""
    adapter = make_adapter()
    campos = form()
    body, firma = sign(campos)

    with pytest.raises(SignatureError):
        await adapter.verify_request(
            headers={"x-twilio-signature": firma},
            body=body,
            credentials=[{"auth_token": AUTH_TOKEN}],
            url="https://otro.sitio.local/webhooks/sms",
        )


async def test_a_tampered_body_is_rejected():
    adapter = make_adapter()
    _, firma = sign(form())
    alterado = urlencode(form(Body="Transfiera 1000 euros")).encode()

    with pytest.raises(SignatureError):
        await adapter.verify_request(
            headers={"x-twilio-signature": firma},
            body=alterado,
            credentials=[{"auth_token": AUTH_TOKEN}],
            url=URL,
        )


async def test_a_missing_signature_header_is_rejected():
    adapter = make_adapter()
    body, _ = sign(form())
    with pytest.raises(SignatureError):
        await adapter.verify_request(
            headers={}, body=body, credentials=[{"auth_token": AUTH_TOKEN}], url=URL
        )


async def test_without_the_url_the_signature_cannot_be_checked():
    adapter = make_adapter()
    body, firma = sign(form())
    with pytest.raises(SignatureError):
        await adapter.verify_request(
            headers={"x-twilio-signature": firma},
            body=body,
            credentials=[{"auth_token": AUTH_TOKEN}],
            url=None,
        )


async def test_any_registered_account_token_is_accepted():
    adapter = make_adapter()
    body, firma = sign(form(), token="el-del-segundo")
    await adapter.verify_request(
        headers={"x-twilio-signature": firma},
        body=body,
        credentials=[{"auth_token": "el-del-primero"}, {"auth_token": "el-del-segundo"}],
        url=URL,
    )


async def test_production_refuses_an_account_without_a_token():
    adapter = make_adapter(environment="prod")
    with pytest.raises(SignatureError):
        await adapter.verify_request(headers={}, body=b"", credentials=[], url=URL)

    await make_adapter().verify_request(headers={}, body=b"", credentials=[], url=URL)


def test_the_signed_string_glues_the_sorted_fields_to_the_url():
    """Formato peculiar y documentado: cualquier desviación da otra firma."""
    body = urlencode({"B": "2", "A": "1"}).encode()
    assert _signed_payload("https://x.local/w", body) == "https://x.local/wA1B2"


# --------------------------------------------------------------------------- #
# Lectura
# --------------------------------------------------------------------------- #
async def test_an_sms_becomes_a_canonical_inbound():
    adapter = make_adapter()
    [inbound] = await adapter.parse(payload=form(), headers={})

    assert inbound.channel is ChannelKind.SMS
    assert inbound.text == "Hola, ¿siguen abiertos?"
    assert inbound.content_type is ContentType.TEXT
    assert inbound.conversation.channel_conversation_id == SU_NUMERO
    # El número propio identifica la cuenta, como el phone_number_id en WhatsApp.
    assert inbound.conversation.channel_account_id == NUESTRO_NUMERO
    assert inbound.sender.phone == SU_NUMERO


async def test_an_mms_carries_its_pictures():
    adapter = make_adapter()
    [inbound] = await adapter.parse(
        payload=form(
            NumMedia="2",
            MediaUrl0="https://api.twilio.com/m/1",
            MediaContentType0="image/jpeg",
            MediaUrl1="https://api.twilio.com/m/2",
            MediaContentType1="application/pdf",
        ),
        headers={},
    )
    assert [a.content_type for a in inbound.attachments] == [
        ContentType.IMAGE,
        ContentType.DOCUMENT,
    ]


async def test_a_delivery_receipt_arrives_as_a_system_event():
    adapter = make_adapter()
    [inbound] = await adapter.parse(
        payload={
            "MessageSid": "SM99",
            "MessageStatus": "delivered",
            "To": SU_NUMERO,
            "From": NUESTRO_NUMERO,
        },
        headers={},
    )
    assert inbound.content_type is ContentType.SYSTEM
    assert inbound.action["target_provider_message_id"] == "SM99"
    assert inbound.action["status"] == "delivered"


async def test_a_payload_without_a_sid_is_ignored():
    adapter = make_adapter()
    assert await adapter.parse(payload={"Body": "suelto"}, headers={}) == []


# --------------------------------------------------------------------------- #
# Envío
# --------------------------------------------------------------------------- #
def test_a_long_reply_is_split_before_twilio_refuses_it():
    largo = "línea de texto\n" * 300
    payloads = _build_payloads(SU_NUMERO, NUESTRO_NUMERO, OutboundMessage(text=largo))

    assert len(payloads) > 1
    assert all(len(p["Body"]) <= TEXT_LIMIT for p in payloads)
    assert all(p["From"] == NUESTRO_NUMERO and p["To"] == SU_NUMERO for p in payloads)


def test_quick_replies_become_a_numbered_list():
    """Un SMS no tiene botones."""
    payloads = _build_payloads(
        SU_NUMERO,
        NUESTRO_NUMERO,
        OutboundMessage(
            text="¿Le sirve?",
            quick_replies=[{"id": "si", "title": "Sí"}, {"id": "no", "title": "No"}],
        ),
    )
    assert "1. Sí" in payloads[0]["Body"]
    assert "2. No" in payloads[0]["Body"]


def test_the_first_attachment_travels_with_the_text():
    """Twilio admite un MediaUrl por llamada; agrupar evita un SMS vacío."""
    payloads = _build_payloads(
        SU_NUMERO,
        NUESTRO_NUMERO,
        OutboundMessage(
            text="Aquí lo tiene",
            attachments=[
                Attachment(content_type=ContentType.IMAGE, url="https://x.local/1.jpg"),
                Attachment(content_type=ContentType.IMAGE, url="https://x.local/2.jpg"),
            ],
        ),
    )
    assert len(payloads) == 2
    assert payloads[0]["Body"] == "Aquí lo tiene"
    assert payloads[0]["MediaUrl"] == "https://x.local/1.jpg"
    assert "Body" not in payloads[1]
    assert payloads[1]["MediaUrl"] == "https://x.local/2.jpg"


async def test_sending_without_credentials_fails_with_a_useful_reason():
    adapter = make_adapter()
    sin_datos = ConversationRef(
        channel=ChannelKind.SMS, channel_conversation_id=SU_NUMERO
    )
    receipt = await adapter.send(ref=sin_datos, message=OutboundMessage(text="Hola"))
    assert receipt.ok is False
    assert receipt.error_code == "not_configured"


# --------------------------------------------------------------------------- #
# De extremo a extremo
# --------------------------------------------------------------------------- #
async def test_a_signed_sms_reaches_the_inbox(anonymous, as_agent, team, monkeypatch):
    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(
        settings, "secret_encryption_key", SecretStr(Fernet.generate_key().decode())
    )
    # La firma abarca la URL pública, no la interna que ve la aplicación.
    monkeypatch.setattr(settings, "public_base_url", "http://pruebas")

    admin = await as_agent(team["admin"]["email"])
    alta = await admin.post(
        "/api/channel-accounts",
        json={
            "channel": "sms",
            "external_id": NUESTRO_NUMERO,
            "credentials": {
                "account_sid": ACCOUNT_SID,
                "auth_token": AUTH_TOKEN,
                "from_number": NUESTRO_NUMERO,
            },
        },
    )
    assert alta.status_code == 201, alta.text

    campos = form()
    body, firma = sign(campos, url="http://pruebas/webhooks/sms")

    rechazado = await anonymous.post(
        "/webhooks/sms",
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert rechazado.status_code == 401

    aceptado = await anonymous.post(
        "/webhooks/sms",
        content=body,
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": firma,
        },
    )
    assert aceptado.status_code == 200, aceptado.text
    assert aceptado.json()["processed"] == 1

    listado = (await admin.get("/api/conversations?scope=unassigned")).json()
    assert any(row["channel"] == "sms" for row in listado)
