"""Pruebas del adaptador de WhatsApp: normalización, firma y composición de salida."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.channels.base import SignatureError
from app.channels.whatsapp import TEXT_LIMIT, WhatsAppAdapter, _split_text
from app.config import Settings
from app.core.envelope import (
    Attachment,
    ChannelKind,
    ContentType,
    ConversationRef,
    OutboundMessage,
)

APP_SECRET = "secreto-de-prueba"


def make_adapter(**overrides) -> WhatsAppAdapter:
    settings = Settings(
        _env_file=None,
        whatsapp_app_secret=APP_SECRET,
        whatsapp_verify_token="token-de-alta",
        whatsapp_access_token="EAAG-token",
        whatsapp_phone_number_id="1234567890",
        **overrides,
    )
    return WhatsAppAdapter(settings)


def webhook(*, messages=None, statuses=None) -> dict:
    value: dict = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "595991000000", "phone_number_id": "1234567890"},
        "contacts": [{"profile": {"name": "Ana Rodríguez"}, "wa_id": "595981123456"}],
    }
    if messages:
        value["messages"] = messages
    if statuses:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": value}]}]}


# --------------------------------------------------------------------------- #
# Normalización de entrada
# --------------------------------------------------------------------------- #
async def test_parse_text_message():
    adapter = make_adapter()
    payload = webhook(
        messages=[
            {
                "from": "595981123456",
                "id": "wamid.ABC123",
                "timestamp": "1740000000",
                "type": "text",
                "text": {"body": "Buenos días, necesito una factura."},
            }
        ]
    )

    [message] = await adapter.parse(payload=payload, headers={})

    assert message.channel is ChannelKind.WHATSAPP
    assert message.content_type is ContentType.TEXT
    assert message.text == "Buenos días, necesito una factura."
    assert message.sender.display_name == "Ana Rodríguez"
    assert message.sender.phone == "+595981123456"
    assert message.conversation.channel_account_id == "1234567890"
    assert message.conversation.reply_to_message_id == "wamid.ABC123"
    assert message.dedupe_key == "whatsapp:wamid.ABC123"


async def test_parse_interactive_reply_exposes_action():
    adapter = make_adapter()
    payload = webhook(
        messages=[
            {
                "from": "595981123456",
                "id": "wamid.BTN",
                "type": "interactive",
                "interactive": {
                    "type": "button_reply",
                    "button_reply": {"id": "opcion_factura", "title": "Factura"},
                },
            }
        ]
    )

    [message] = await adapter.parse(payload=payload, headers={})

    assert message.content_type is ContentType.INTERACTIVE
    assert message.action == {
        "id": "opcion_factura",
        "title": "Factura",
        "source": "interactive",
    }


async def test_parse_media_message_keeps_media_id():
    adapter = make_adapter()
    payload = webhook(
        messages=[
            {
                "from": "595981123456",
                "id": "wamid.IMG",
                "type": "image",
                "image": {"id": "media-99", "mime_type": "image/jpeg", "caption": "El recibo"},
            }
        ]
    )

    [message] = await adapter.parse(payload=payload, headers={})

    assert message.content_type is ContentType.IMAGE
    assert message.text == "El recibo"
    assert message.attachments[0].provider_media_id == "media-99"
    assert message.attachments[0].mime_type == "image/jpeg"


async def test_parse_status_becomes_system_event():
    adapter = make_adapter()
    payload = webhook(
        statuses=[
            {
                "id": "wamid.SENT",
                "status": "delivered",
                "timestamp": "1740000100",
                "recipient_id": "595981123456",
            }
        ]
    )

    [event] = await adapter.parse(payload=payload, headers={})

    assert event.content_type is ContentType.SYSTEM
    assert event.is_actionable is False
    assert event.action["kind"] == "delivery_status"
    assert event.action["target_provider_message_id"] == "wamid.SENT"
    assert event.action["status"] == "delivered"


# --------------------------------------------------------------------------- #
# Autenticidad
# --------------------------------------------------------------------------- #
async def test_signature_accepts_valid_digest():
    adapter = make_adapter()
    body = json.dumps(webhook(messages=[])).encode()
    digest = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()

    await adapter.verify_request(headers={"x-hub-signature-256": f"sha256={digest}"}, body=body)


async def test_signature_rejects_tampered_body():
    adapter = make_adapter()
    body = b'{"entry": []}'
    digest = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()

    with pytest.raises(SignatureError):
        await adapter.verify_request(
            headers={"x-hub-signature-256": f"sha256={digest}"}, body=body + b" "
        )


async def test_signature_rejects_missing_header():
    adapter = make_adapter()
    with pytest.raises(SignatureError):
        await adapter.verify_request(headers={}, body=b"{}")


def test_subscription_challenge_requires_matching_token():
    adapter = make_adapter()

    challenge = adapter.verify_subscription(
        {"hub.mode": "subscribe", "hub.verify_token": "token-de-alta", "hub.challenge": "42"}
    )
    assert challenge == "42"

    with pytest.raises(SignatureError):
        adapter.verify_subscription(
            {"hub.mode": "subscribe", "hub.verify_token": "incorrecto", "hub.challenge": "42"}
        )


# --------------------------------------------------------------------------- #
# Composición de salida
# --------------------------------------------------------------------------- #
def test_payload_uses_buttons_up_to_three_options():
    adapter = make_adapter()
    message = OutboundMessage(
        text="¿Qué desea hacer?",
        quick_replies=[{"id": "a", "title": "Consultar"}, {"id": "b", "title": "Cancelar"}],
    )

    [payload] = adapter._build_payloads("595981123456", message)

    assert payload["type"] == "interactive"
    assert payload["interactive"]["type"] == "button"
    assert len(payload["interactive"]["action"]["buttons"]) == 2


def test_payload_switches_to_list_beyond_three_options():
    adapter = make_adapter()
    message = OutboundMessage(
        text="Elija una opción",
        quick_replies=[{"id": str(i), "title": f"Opción {i}"} for i in range(5)],
    )

    [payload] = adapter._build_payloads("595981123456", message)

    assert payload["interactive"]["type"] == "list"
    assert len(payload["interactive"]["action"]["sections"][0]["rows"]) == 5


def test_payload_splits_long_text_and_precedes_it_with_media():
    adapter = make_adapter()
    message = OutboundMessage(
        text="línea\n" * 2_000,
        attachments=[
            Attachment(
                content_type=ContentType.DOCUMENT,
                url="https://ejemplo.test/factura.pdf",
                filename="factura.pdf",
            )
        ],
    )

    payloads = adapter._build_payloads("595981123456", message)

    assert payloads[0]["type"] == "document"
    assert payloads[0]["document"]["filename"] == "factura.pdf"
    assert len(payloads) > 2
    assert all(
        len(item["text"]["body"]) <= TEXT_LIMIT for item in payloads[1:]
    )


def test_template_payload_short_circuits_composition():
    adapter = make_adapter()
    message = OutboundMessage(
        text="ignorado",
        channel_data={"template": {"name": "recordatorio", "language": {"code": "es"}}},
    )

    payloads = adapter._build_payloads("595981123456", message)

    assert len(payloads) == 1
    assert payloads[0]["type"] == "template"


def test_split_text_preserves_all_content():
    text = "\n".join(f"renglón {index}" for index in range(1_500))
    chunks = _split_text(text, 500)

    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)
    # Ninguna palabra se pierde en el troceado.
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


async def test_send_without_credentials_reports_configuration_error():
    settings = Settings(_env_file=None)
    adapter = WhatsAppAdapter(settings)
    receipt = await adapter.send(
        ref=ConversationRef(
            channel=ChannelKind.WHATSAPP, channel_conversation_id="595981123456"
        ),
        message=OutboundMessage(text="hola"),
    )

    assert receipt.ok is False
    assert receipt.error_code == "not_configured"
    await adapter.aclose()
