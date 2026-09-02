"""Pruebas del adaptador del Bot Framework: *Activities* y validación del JWT."""

from __future__ import annotations

import pytest

from app.channels.base import SignatureError
from app.channels.msbot import MicrosoftBotAdapter, _strip_mentions
from app.config import Settings
from app.core.envelope import ChannelKind, ContentType, ConversationRef, OutboundMessage

APP_ID = "11111111-2222-3333-4444-555555555555"


def make_adapter(**overrides) -> MicrosoftBotAdapter:
    options = {
        "microsoft_app_id": APP_ID,
        "microsoft_app_password": "secreto",
        "microsoft_validate_jwt": False,
    } | overrides
    return MicrosoftBotAdapter(Settings(_env_file=None, **options))


def teams_activity(**overrides) -> dict:
    activity = {
        "type": "message",
        "id": "1740000000001",
        "timestamp": "2026-02-19T10:15:00.000Z",
        "serviceUrl": "https://smba.trafficmanager.net/emea/",
        "channelId": "msteams",
        "from": {
            "id": "29:1abcDEF",
            "name": "Ana Rodríguez",
            "aadObjectId": "99999999-8888-7777-6666-555555555555",
        },
        "conversation": {"id": "19:meeting@thread.v2", "conversationType": "personal"},
        "recipient": {"id": f"28:{APP_ID}", "name": "Asistente"},
        "text": "Necesito el informe mensual",
        "locale": "es-ES",
        "channelData": {"tenant": {"id": "aaaa-bbbb"}},
    }
    activity.update(overrides)
    return activity


# --------------------------------------------------------------------------- #
# Normalización de entrada
# --------------------------------------------------------------------------- #
async def test_parse_message_activity():
    adapter = make_adapter()

    [message] = await adapter.parse(payload=teams_activity(), headers={})

    assert message.channel is ChannelKind.MSBOT
    assert message.content_type is ContentType.TEXT
    assert message.text == "Necesito el informe mensual"
    assert message.sender.display_name == "Ana Rodríguez"
    assert message.sender.aad_object_id == "99999999-8888-7777-6666-555555555555"
    assert message.conversation.service_url == "https://smba.trafficmanager.net/emea/"
    assert message.conversation.extra["aad_tenant_id"] == "aaaa-bbbb"
    assert message.conversation.channel_account_id == f"28:{APP_ID}"


async def test_parse_in_a_team_channel_uses_the_team_as_the_account():
    """Distingue de qué equipo de Teams vino el mensaje, no solo el bot —
    para poder conectar tantos equipos como se quiera por departamento."""
    adapter = make_adapter()
    activity = teams_activity(
        conversation={"id": "19:canal@thread.tacv2", "conversationType": "channel"},
        channelData={
            "tenant": {"id": "aaaa-bbbb"},
            "team": {"id": "19:equipo-ventas@thread.tacv2", "name": "Ventas"},
        },
    )

    [message] = await adapter.parse(payload=activity, headers={})

    assert message.conversation.channel_account_id == "19:equipo-ventas@thread.tacv2"


async def test_parse_a_personal_chat_still_falls_back_to_the_bot():
    """Sin equipo (chat 1:1, Web Chat, Direct Line), nada cambia."""
    adapter = make_adapter()

    [message] = await adapter.parse(payload=teams_activity(), headers={})

    assert message.conversation.channel_account_id == f"28:{APP_ID}"


async def test_parse_strips_bot_mention():
    """En Teams el texto llega con la mención al bot incrustada."""
    adapter = make_adapter()
    activity = teams_activity(
        text="<at>Asistente</at> ¿cuál es el saldo?",
        entities=[
            {
                "type": "mention",
                "text": "<at>Asistente</at>",
                "mentioned": {"id": f"28:{APP_ID}", "name": "Asistente"},
            }
        ],
    )

    [message] = await adapter.parse(payload=activity, headers={})

    assert message.text == "¿cuál es el saldo?"


async def test_parse_conversation_update_is_system_event():
    adapter = make_adapter()
    activity = teams_activity(type="conversationUpdate", text=None)
    del activity["text"]

    [event] = await adapter.parse(payload=activity, headers={})

    assert event.content_type is ContentType.SYSTEM
    assert event.is_actionable is False
    assert event.action["kind"] == "conversationUpdate"


async def test_parse_invoke_activity_carries_value():
    adapter = make_adapter()
    activity = teams_activity(
        type="invoke", name="adaptiveCard/action", value={"action": {"id": "aprobar"}}
    )
    del activity["text"]

    [event] = await adapter.parse(payload=activity, headers={})

    assert event.content_type is ContentType.INTERACTIVE
    assert event.action["name"] == "adaptiveCard/action"
    assert event.action["value"] == {"action": {"id": "aprobar"}}


async def test_parse_typing_activity_is_ignored():
    adapter = make_adapter()
    activity = teams_activity(type="typing")
    del activity["text"]

    assert await adapter.parse(payload=activity, headers={}) == []


async def test_parse_without_conversation_id_returns_nothing():
    adapter = make_adapter()
    activity = teams_activity(conversation={})

    assert await adapter.parse(payload=activity, headers={}) == []


def test_strip_mentions_leaves_other_mentions_intact():
    payload = {
        "text": "<at>Asistente</at> avisa a <at>Luis</at>",
        "recipient": {"id": "bot-1"},
        "entities": [
            {"type": "mention", "text": "<at>Asistente</at>", "mentioned": {"id": "bot-1"}},
            {"type": "mention", "text": "<at>Luis</at>", "mentioned": {"id": "user-9"}},
        ],
    }

    assert _strip_mentions(payload) == "avisa a <at>Luis</at>"


# --------------------------------------------------------------------------- #
# Autenticidad
# --------------------------------------------------------------------------- #
async def test_jwt_validation_requires_bearer_header():
    adapter = make_adapter(microsoft_validate_jwt=True)

    with pytest.raises(SignatureError, match="Authorization"):
        await adapter.verify_request(headers={}, body=b"{}")


async def test_jwt_validation_rejects_malformed_token():
    adapter = make_adapter(microsoft_validate_jwt=True)

    with pytest.raises(SignatureError):
        await adapter.verify_request(
            headers={"authorization": "Bearer no-es-un-jwt"}, body=b"{}"
        )


# --------------------------------------------------------------------------- #
# Composición de salida
# --------------------------------------------------------------------------- #
def test_activity_includes_suggested_actions_and_reply_id():
    adapter = make_adapter()
    ref = ConversationRef(
        channel=ChannelKind.MSBOT,
        channel_conversation_id="19:meeting@thread.v2",
        service_url="https://smba.trafficmanager.net/emea/",
        reply_to_message_id="1740000000001",
        extra={"channel_id": "msteams", "bot": {"id": f"28:{APP_ID}"}, "locale": "es-ES"},
    )
    message = OutboundMessage(
        text="Aquí tiene el informe.",
        quick_replies=[{"id": "descargar", "title": "Descargar"}],
    )

    activity = adapter._build_activity(ref, message)

    assert activity["type"] == "message"
    assert activity["replyToId"] == "1740000000001"
    assert activity["channelId"] == "msteams"
    assert activity["locale"] == "es-ES"
    assert activity["suggestedActions"]["actions"][0] == {
        "type": "imBack",
        "title": "Descargar",
        "value": "descargar",
    }


def test_activity_attaches_adaptive_card_from_channel_data():
    adapter = make_adapter()
    ref = ConversationRef(
        channel=ChannelKind.MSBOT,
        channel_conversation_id="19:thread",
        service_url="https://smba.trafficmanager.net/emea/",
    )
    card = {"type": "AdaptiveCard", "version": "1.5", "body": []}

    activity = adapter._build_activity(ref, OutboundMessage(channel_data={"adaptive_card": card}))

    assert activity["attachments"][0]["contentType"] == (
        "application/vnd.microsoft.card.adaptive"
    )
    assert activity["attachments"][0]["content"] == card


async def test_send_without_service_url_fails_fast():
    adapter = make_adapter()

    receipt = await adapter.send(
        ref=ConversationRef(channel=ChannelKind.MSBOT, channel_conversation_id="19:thread"),
        message=OutboundMessage(text="hola"),
    )

    assert receipt.ok is False
    assert receipt.error_code == "missing_service_url"
    await adapter.aclose()
