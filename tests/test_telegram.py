"""Adaptador de Telegram: lectura del webhook, firma y envío.

Mismo patrón que ``tests/test_whatsapp.py``: se construye el adaptador con
ajustes aislados del entorno y se le dan cargas de ejemplo, sin red.
"""

from __future__ import annotations

import pytest

from app.channels.base import SignatureError
from app.channels.telegram import TEXT_LIMIT, TelegramAdapter, _keyboard
from app.config import Settings
from app.core.envelope import (
    Attachment,
    ChannelKind,
    ContentType,
    ConversationRef,
    OutboundMessage,
)

CHAT_ID = 987654321
BOT_TOKEN = "7654321:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
SECRET = "un-secreto-largo-de-webhook"


def make_adapter(**overrides) -> TelegramAdapter:
    overrides.setdefault("environment", "dev")
    return TelegramAdapter(Settings(_env_file=None, **overrides))


def update(**message_fields) -> dict:
    """Un ``update`` con un mensaje corriente dentro."""
    return {
        "update_id": 1,
        "message": {
            "message_id": 42,
            "date": 1_735_000_000,
            "chat": {"id": CHAT_ID, "type": "private"},
            "from": {
                "id": 555,
                "first_name": "Marta",
                "last_name": "Giménez",
                "username": "marta",
                "language_code": "es",
            },
            **message_fields,
        },
    }


# --------------------------------------------------------------------------- #
# Lectura
# --------------------------------------------------------------------------- #
async def test_a_text_message_becomes_a_canonical_inbound():
    adapter = make_adapter()
    [inbound] = await adapter.parse(payload=update(text="Hola"), headers={})

    assert inbound.channel is ChannelKind.TELEGRAM
    assert inbound.text == "Hola"
    assert inbound.content_type is ContentType.TEXT
    assert inbound.conversation.channel_conversation_id == str(CHAT_ID)
    assert inbound.sender.channel_user_id == "555"
    assert inbound.sender.display_name == "Marta Giménez"
    assert inbound.sender.locale == "es"


async def test_the_dedupe_key_carries_the_chat():
    """El message_id de Telegram solo es único dentro de su chat."""
    adapter = make_adapter()
    [inbound] = await adapter.parse(payload=update(text="Hola"), headers={})
    assert inbound.provider_message_id == f"{CHAT_ID}:42"
    assert inbound.dedupe_key == f"telegram:{CHAT_ID}:42"


async def test_an_edited_message_does_not_collide_with_the_original():
    adapter = make_adapter()
    original = update(text="Hola")
    editado = {
        "update_id": 2,
        "edited_message": {**original["message"], "text": "Hola!", "edit_date": 1_735_000_100},
    }

    [antes] = await adapter.parse(payload=original, headers={})
    [despues] = await adapter.parse(payload=editado, headers={})
    assert antes.dedupe_key != despues.dedupe_key
    assert despues.text == "Hola!"


async def test_a_photo_keeps_only_the_largest_size():
    """Telegram manda la misma foto en varios tamaños; interesa el mayor."""
    adapter = make_adapter()
    [inbound] = await adapter.parse(
        payload=update(
            photo=[
                {"file_id": "pequena", "file_size": 100},
                {"file_id": "grande", "file_size": 9000},
            ],
            caption="Mire esto",
        ),
        headers={},
    )
    assert len(inbound.attachments) == 1
    assert inbound.attachments[0].provider_media_id == "grande"
    assert inbound.attachments[0].content_type is ContentType.IMAGE
    # Con pie de foto, el turno sigue siendo de texto: hay algo que leer.
    assert inbound.content_type is ContentType.TEXT
    assert inbound.text == "Mire esto"


async def test_a_voice_note_arrives_as_audio():
    adapter = make_adapter()
    [inbound] = await adapter.parse(
        payload=update(voice={"file_id": "voz-1", "mime_type": "audio/ogg", "file_size": 4200}),
        headers={},
    )
    assert inbound.content_type is ContentType.AUDIO
    assert inbound.attachments[0].provider_media_id == "voz-1"
    assert inbound.attachments[0].mime_type == "audio/ogg"


async def test_a_pressed_button_arrives_as_an_interaction():
    adapter = make_adapter()
    [inbound] = await adapter.parse(
        payload={
            "update_id": 3,
            "callback_query": {
                "id": "cb-1",
                "data": "si",
                "from": {"id": 555, "first_name": "Marta"},
                "message": {
                    "message_id": 43,
                    "date": 1_735_000_000,
                    "chat": {"id": CHAT_ID, "type": "private"},
                },
            },
        },
        headers={},
    )
    assert inbound.content_type is ContentType.INTERACTIVE
    assert inbound.action == {"id": "si", "title": "si"}


async def test_an_update_without_a_message_is_ignored():
    """Telegram manda avisos de cosas que no son mensajes; no deben romper."""
    adapter = make_adapter()
    assert await adapter.parse(payload={"update_id": 4}, headers={}) == []
    assert await adapter.parse(payload={"update_id": 5, "poll": {}}, headers={}) == []


# --------------------------------------------------------------------------- #
# Firma del webhook
# --------------------------------------------------------------------------- #
async def test_the_webhook_secret_is_checked():
    adapter = make_adapter()
    credenciales = [{"verify_token": SECRET}]

    await adapter.verify_request(
        headers={"x-telegram-bot-api-secret-token": SECRET},
        body=b"{}",
        credentials=credenciales,
    )

    with pytest.raises(SignatureError):
        await adapter.verify_request(
            headers={"x-telegram-bot-api-secret-token": "otro"},
            body=b"{}",
            credentials=credenciales,
        )

    with pytest.raises(SignatureError):
        await adapter.verify_request(headers={}, body=b"{}", credentials=credenciales)


async def test_any_registered_bot_secret_is_accepted():
    """La firma se comprueba antes de leer el cuerpo: aún no se sabe de qué bot viene."""
    adapter = make_adapter()
    await adapter.verify_request(
        headers={"x-telegram-bot-api-secret-token": "el-del-segundo-bot"},
        body=b"{}",
        credentials=[{"verify_token": "el-del-primero"}, {"verify_token": "el-del-segundo-bot"}],
    )


async def test_production_refuses_a_bot_without_a_secret():
    """Sin secreto, la URL del webhook es la única defensa, y es pública."""
    adapter = make_adapter(environment="prod")
    with pytest.raises(SignatureError):
        await adapter.verify_request(headers={}, body=b"{}", credentials=[])

    # En desarrollo se admite mientras se prueba.
    await make_adapter().verify_request(headers={}, body=b"{}", credentials=[])


# --------------------------------------------------------------------------- #
# Envío
# --------------------------------------------------------------------------- #
def test_a_long_reply_is_split_at_the_api_limit():
    adapter = make_adapter()
    largo = "línea\n" * 2_000
    calls = adapter._build_calls(str(CHAT_ID), OutboundMessage(text=largo))

    assert len(calls) > 1
    assert all(method == "sendMessage" for method, _ in calls)
    assert all(len(body["text"]) <= TEXT_LIMIT for _, body in calls)


def test_quick_replies_become_an_inline_keyboard_on_the_last_chunk():
    """Repetir el teclado en cada trozo dejaría varios vivos en el mismo hilo."""
    adapter = make_adapter()
    calls = adapter._build_calls(
        str(CHAT_ID),
        OutboundMessage(
            text="línea\n" * 2_000,
            quick_replies=[{"id": "si", "title": "Sí"}, {"id": "no", "title": "No"}],
        ),
    )
    assert "reply_markup" not in calls[0][1]
    assert calls[-1][1]["reply_markup"]["inline_keyboard"] == [
        [
            {"text": "Sí", "callback_data": "si"},
            {"text": "No", "callback_data": "no"},
        ]
    ]


def test_the_callback_payload_is_trimmed_to_what_telegram_accepts():
    """Pasarse de 64 bytes hace que Telegram rechace la llamada entera."""
    teclado = _keyboard([{"id": "x" * 200, "title": "y" * 200}])
    assert len(teclado[0][0]["callback_data"]) == 64
    assert len(teclado[0][0]["text"]) == 64


def test_an_attachment_picks_the_method_of_its_type():
    adapter = make_adapter()
    calls = adapter._build_calls(
        str(CHAT_ID),
        OutboundMessage(
            attachments=[
                Attachment(content_type=ContentType.IMAGE, url="https://ejemplo.local/f.jpg"),
                Attachment(content_type=ContentType.DOCUMENT, url="https://ejemplo.local/f.pdf"),
            ]
        ),
    )
    assert [method for method, _ in calls] == ["sendPhoto", "sendDocument"]
    assert calls[0][1]["photo"] == "https://ejemplo.local/f.jpg"


def test_an_attachment_without_a_url_is_skipped():
    """Telegram baja el fichero de la URL que se le pase; sin ella no hay envío."""
    adapter = make_adapter()
    calls = adapter._build_calls(
        str(CHAT_ID),
        OutboundMessage(attachments=[Attachment(content_type=ContentType.IMAGE)]),
    )
    assert calls == []


async def test_sending_without_a_token_fails_with_a_useful_reason():
    adapter = make_adapter()
    sin_token = ConversationRef(
        channel=ChannelKind.TELEGRAM, channel_conversation_id=str(CHAT_ID)
    )
    receipt = await adapter.send(ref=sin_token, message=OutboundMessage(text="Hola"))
    assert receipt.ok is False
    assert receipt.error_code == "not_configured"


# --------------------------------------------------------------------------- #
# De extremo a extremo por la ruta genérica
# --------------------------------------------------------------------------- #
async def test_a_telegram_update_reaches_the_inbox(anonymous, as_agent, team, monkeypatch):
    """No hace falta ruta propia: /webhooks/{canal} sirve a todo el registro."""
    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(
        settings, "secret_encryption_key", SecretStr(Fernet.generate_key().decode())
    )

    admin = await as_agent(team["admin"]["email"])
    alta = await admin.post(
        "/api/channel-accounts",
        json={
            "channel": "telegram",
            "external_id": "7654321",
            "display_name": "Bot de soporte",
            "credentials": {"access_token": BOT_TOKEN, "verify_token": SECRET},
        },
    )
    assert alta.status_code == 201, alta.text

    # Sin la cabecera del secreto, no entra.
    rechazado = await anonymous.post("/webhooks/telegram", json=update(text="Hola"))
    assert rechazado.status_code == 401

    aceptado = await anonymous.post(
        "/webhooks/telegram",
        json=update(text="Hola"),
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )
    assert aceptado.status_code == 200, aceptado.text
    assert aceptado.json()["processed"] == 1

    # Y el hilo aparece en la cola común, como el de cualquier otro canal.
    listado = (await admin.get("/api/conversations?scope=unassigned")).json()
    assert any(row["channel"] == "telegram" for row in listado)
