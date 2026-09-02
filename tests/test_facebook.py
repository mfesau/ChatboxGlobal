"""Pruebas del adaptador de Facebook Messenger: normalización, firma y salida."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.channels.base import SignatureError
from app.channels.facebook import FacebookAdapter, _split_text
from app.config import Settings
from app.core.envelope import ChannelKind, ContentType, ConversationRef, OutboundMessage

APP_SECRET = "secreto-de-prueba"
PAGE_ID = "9988776655"


def make_adapter(**overrides) -> FacebookAdapter:
    settings = Settings(
        _env_file=None,
        facebook_app_secret=APP_SECRET,
        facebook_verify_token="token-de-alta",
        **overrides,
    )
    return FacebookAdapter(settings)


def webhook(*, messaging: list[dict]) -> dict:
    return {"object": "page", "entry": [{"id": PAGE_ID, "messaging": messaging}]}


# --------------------------------------------------------------------------- #
# Normalización de entrada
# --------------------------------------------------------------------------- #
async def test_parse_text_message():
    adapter = make_adapter()
    payload = webhook(
        messaging=[
            {
                "sender": {"id": "psid-1"},
                "recipient": {"id": PAGE_ID},
                "timestamp": 1740000000000,
                "message": {"mid": "mid.ABC", "text": "Hola, ¿tienen stock?"},
            }
        ]
    )

    [message] = await adapter.parse(payload=payload, headers={})

    assert message.channel is ChannelKind.FACEBOOK
    assert message.content_type is ContentType.TEXT
    assert message.text == "Hola, ¿tienen stock?"
    assert message.conversation.channel_conversation_id == "psid-1"
    assert message.conversation.channel_account_id == PAGE_ID
    assert message.provider_message_id == "mid.ABC"


async def test_parse_ignores_the_page_own_echo():
    adapter = make_adapter()
    payload = webhook(
        messaging=[
            {
                "sender": {"id": PAGE_ID},
                "recipient": {"id": "psid-1"},
                "timestamp": 1740000000000,
                "message": {"mid": "mid.ECHO", "text": "respuesta", "is_echo": True},
            }
        ]
    )

    assert await adapter.parse(payload=payload, headers={}) == []


async def test_parse_postback_exposes_action():
    adapter = make_adapter()
    payload = webhook(
        messaging=[
            {
                "sender": {"id": "psid-1"},
                "recipient": {"id": PAGE_ID},
                "timestamp": 1740000000000,
                "postback": {"title": "Ver catálogo", "payload": "VER_CATALOGO"},
            }
        ]
    )

    [message] = await adapter.parse(payload=payload, headers={})

    assert message.content_type is ContentType.INTERACTIVE
    assert message.action == {"id": "VER_CATALOGO", "title": "Ver catálogo"}


async def test_parse_ignores_a_non_page_object():
    adapter = make_adapter()
    assert await adapter.parse(payload={"object": "instagram", "entry": []}, headers={}) == []


# --------------------------------------------------------------------------- #
# Autenticidad
# --------------------------------------------------------------------------- #
async def test_signature_accepts_valid_digest():
    adapter = make_adapter()
    body = json.dumps(webhook(messaging=[])).encode()
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
# Salida: cada página exige su propio token, sin respaldo global
# --------------------------------------------------------------------------- #
async def test_send_without_a_page_token_reports_configuration_error():
    adapter = make_adapter()
    receipt = await adapter.send(
        ref=ConversationRef(channel=ChannelKind.FACEBOOK, channel_conversation_id="psid-1"),
        message=OutboundMessage(text="hola"),
    )

    assert receipt.ok is False
    assert receipt.error_code == "not_configured"
    await adapter.aclose()


def test_build_payloads_uses_the_account_credentials_recipient():
    adapter = make_adapter()
    message = OutboundMessage(text="Gracias por escribir")

    [payload] = adapter._build_payloads("psid-1", message)

    assert payload["recipient"] == {"id": "psid-1"}
    assert payload["message"]["text"] == "Gracias por escribir"


def test_split_text_preserves_all_content():
    text = "\n".join(f"renglón {index}" for index in range(600))
    chunks = _split_text(text, 500)

    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")
