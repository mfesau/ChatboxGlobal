"""Derechos del interesado: exportación y borrado de los datos de un contacto.

El RGPD concede a quien escribe dos cosas que hasta ahora no tenían camino en
la aplicación: llevarse una copia de lo que se guarda sobre él (artículo 15) y
que se borre (artículo 17).

El borrado **anonimiza, no suprime filas**. Es deliberado: quitar la
conversación entera se llevaría por delante los recuentos de los informes y la
trazabilidad de quién atendió qué, que responden a otra obligación distinta. Lo
que desaparece es el dato personal —nombre, teléfono, correo, identificadores
de canal, el texto de los mensajes y los ficheros adjuntos—; lo que queda es el
esqueleto: que hubo una conversación, por qué canal, cuándo y quién la atendió.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.storage import resolve_upload
from app.db.models import (
    Contact,
    ContactComment,
    ContactIdentity,
    ContactSession,
    Conversation,
    HotelReservation,
    InternalNote,
    Message,
)
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Lo que se deja escrito donde había un nombre.
ANONYMISED_NAME = "Contacto anonimizado"


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


# --------------------------------------------------------------------------- #
# Exportación
# --------------------------------------------------------------------------- #
async def export_contact(session: AsyncSession, contact: Contact) -> dict[str, Any]:
    """Reúne en un solo documento todo lo que se guarda sobre un contacto.

    Los adjuntos van por referencia y no incrustados: el fichero se descarga
    por su enlace, que ya comprueba permisos (``app/api/attachments.py``).
    Incrustarlos en base64 daría un documento de decenas de megabytes por una
    sola conversación con fotos, y sin ninguna ventaja.
    """
    conversations = list(
        (
            await session.execute(
                select(Conversation)
                .where(Conversation.contact_id == contact.id)
                .order_by(Conversation.created_at)
            )
        ).scalars()
    )
    conversation_ids = [row.id for row in conversations]

    messages: list[Message] = []
    notes: list[InternalNote] = []
    if conversation_ids:
        messages = list(
            (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id.in_(conversation_ids))
                    .order_by(Message.created_at)
                )
            ).scalars()
        )
        notes = list(
            (
                await session.execute(
                    select(InternalNote)
                    .where(InternalNote.conversation_id.in_(conversation_ids))
                    .order_by(InternalNote.created_at)
                )
            ).scalars()
        )

    identities = list(
        (
            await session.execute(
                select(ContactIdentity).where(ContactIdentity.contact_id == contact.id)
            )
        ).scalars()
    )
    comments = list(
        (
            await session.execute(
                select(ContactComment)
                .where(ContactComment.contact_id == contact.id)
                .order_by(ContactComment.created_at)
            )
        ).scalars()
    )
    reservations = list(
        (
            await session.execute(
                select(HotelReservation)
                .where(HotelReservation.contact_id == contact.id)
                .order_by(HotelReservation.check_in)
            )
        ).scalars()
    )

    return {
        "contact": {
            "id": str(contact.id),
            "display_name": contact.display_name,
            "primary_phone": contact.primary_phone,
            "primary_email": contact.primary_email,
            "locale": contact.locale,
            "is_blocked": contact.is_blocked,
            "attributes": contact.attributes,
            "created_at": _iso(getattr(contact, "created_at", None)),
        },
        "identities": [
            {
                "channel": str(row.channel),
                "channel_user_id": row.channel_user_id,
                "created_at": _iso(getattr(row, "created_at", None)),
            }
            for row in identities
        ],
        "conversations": [
            {
                "id": str(row.id),
                "channel": str(row.channel),
                "status": row.status,
                "control": row.control,
                "created_at": _iso(row.created_at),
                "last_message_at": _iso(row.last_message_at),
            }
            for row in conversations
        ],
        "messages": [
            {
                "id": str(row.id),
                "conversation_id": str(row.conversation_id),
                "channel": str(row.channel),
                "direction": str(row.direction),
                "content_type": str(row.content_type),
                "author_type": row.author_type,
                "text": row.text,
                "attachments": row.attachments,
                "created_at": _iso(row.created_at),
            }
            for row in messages
        ],
        # Las notas internas se incluyen porque hablan del interesado, aunque
        # nunca se le hayan mostrado: el derecho de acceso no distingue.
        "internal_notes": [
            {
                "conversation_id": str(row.conversation_id),
                "body": row.body,
                "created_at": _iso(row.created_at),
            }
            for row in notes
        ],
        "contact_comments": [
            {"body": row.body, "created_at": _iso(row.created_at)} for row in comments
        ],
        "hotel_reservations": [
            {
                "id": str(row.id),
                "guest_name": row.guest_name,
                "guest_phone": row.guest_phone,
                "guest_email": row.guest_email,
                "check_in": row.check_in.isoformat(),
                "check_out": row.check_out.isoformat(),
                "status": row.status,
            }
            for row in reservations
        ],
    }


# --------------------------------------------------------------------------- #
# Borrado
# --------------------------------------------------------------------------- #
async def anonymise_contact(
    session: AsyncSession, contact: Contact, *, settings: Settings
) -> dict[str, int]:
    """Borra el dato personal y deja el esqueleto. Devuelve qué se tocó.

    No se puede deshacer: no queda copia del texto ni del fichero borrado.
    """
    conversation_ids = list(
        (
            await session.execute(
                select(Conversation.id).where(Conversation.contact_id == contact.id)
            )
        ).scalars()
    )

    removed_files = 0
    blanked_messages = 0
    if conversation_ids:
        messages = list(
            (
                await session.execute(
                    select(Message).where(Message.conversation_id.in_(conversation_ids))
                )
            ).scalars()
        )
        for message in messages:
            removed_files += _delete_attachments(message.attachments, settings=settings)
            message.text = None
            message.attachments = []
            message.action = None
            blanked_messages += 1

        # Las notas internas hablan del interesado aunque nunca las viera.
        await session.execute(
            update(InternalNote)
            .where(InternalNote.conversation_id.in_(conversation_ids))
            .values(body="[anonimizado]")
        )

    # Los identificadores de canal no pueden quedar nulos —hay una restricción
    # de unicidad por canal—, así que se sustituyen por un seudónimo estable,
    # derivado de la propia fila y sin relación con el número o la cuenta.
    identities = list(
        (
            await session.execute(
                select(ContactIdentity).where(ContactIdentity.contact_id == contact.id)
            )
        ).scalars()
    )
    for identity in identities:
        identity.channel_user_id = f"anon-{identity.id.hex}"

    await session.execute(
        update(HotelReservation)
        .where(HotelReservation.contact_id == contact.id)
        .values(guest_name=ANONYMISED_NAME, guest_phone=None, guest_email=None)
    )
    await session.execute(delete(ContactComment).where(ContactComment.contact_id == contact.id))
    await session.execute(delete(ContactSession).where(ContactSession.contact_id == contact.id))

    contact.display_name = ANONYMISED_NAME
    contact.primary_phone = None
    contact.primary_email = None
    contact.locale = None
    contact.attributes = {}
    # Sin contraseña no se puede volver a entrar con esa cuenta, que es lo que
    # corresponde: la identidad que había detrás ya no está.
    contact.password_hash = None
    contact.failed_login_count = 0
    contact.locked_until = None

    summary = {
        "conversations": len(conversation_ids),
        "messages": blanked_messages,
        "identities": len(identities),
        "files": removed_files,
    }
    log.warning("contact_anonymised", contact_id=str(contact.id), **summary)
    return summary


# --------------------------------------------------------------------------- #
# Caducidad del contenido
# --------------------------------------------------------------------------- #
async def purge_expired_messages(
    session: AsyncSession, *, older_than: datetime, settings: Settings
) -> dict[str, int]:
    """Vacía los mensajes anteriores a esa fecha, ficheros incluidos.

    La fila se conserva porque de ella salen los recuentos de los informes; lo
    que caduca es el contenido. Vive aquí y no en ``repositories.py`` porque
    hay que borrar también del disco, y eso necesita ``Settings`` y el mismo
    cuidado con las rutas que el borrado por petición del interesado.

    Solo toca lo que aún tiene algo dentro, de modo que pasar la escoba dos
    veces no vuelve a escribir nada.
    """
    caducados = list(
        (
            await session.execute(
                select(Message).where(
                    Message.created_at < older_than,
                    or_(Message.text.is_not(None), Message.attachments != []),
                )
            )
        ).scalars()
    )

    ficheros = 0
    for message in caducados:
        ficheros += _delete_attachments(message.attachments, settings=settings)
        message.text = None
        message.attachments = []
        message.action = None

    if caducados:
        log.info("messages_expired", messages=len(caducados), files=ficheros)
    return {"messages": len(caducados), "files": ficheros}


def _delete_attachments(attachments: Any, *, settings: Settings) -> int:
    """Borra del disco los ficheros de un mensaje. Devuelve cuántos se fueron.

    Tolerante a propósito: un adjunto ya borrado a mano, o una entrada sin
    ``url`` porque el canal no lo alojaba, no debe abortar el borrado entero.
    """
    if not isinstance(attachments, list):
        return 0
    removed = 0
    for entry in attachments:
        if not isinstance(entry, dict):
            continue
        fichero = resolve_upload(entry.get("url"), settings=settings)
        if fichero is None:
            continue
        try:
            fichero.unlink()
            removed += 1
        except OSError as error:  # pragma: no cover - depende del sistema de ficheros
            log.warning("attachment_delete_failed", path=str(fichero), error=str(error))
    return removed
