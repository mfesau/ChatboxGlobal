"""Almacenamiento local de las imágenes subidas desde el chatbox y la consola.

Sin proveedor de objetos externo: el fichero se guarda en disco, bajo un
directorio propio por inquilino, con un nombre aleatorio que evita colisiones y
adivinanzas. El control de acceso NO lo da ese nombre, sino el enrutador de
``app/api/attachments.py``, que comprueba en cada descarga si quien la pide
puede ver la conversación que contiene el adjunto.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.config import Settings
from app.core.envelope import Attachment, ContentType

#: Extensiones admitidas al **subir** desde el chatbox o la consola. Cerrado a
#: imágenes a propósito: es lo único que la interfaz sabe componer y mostrar.
_ALLOWED_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

#: Lo que puede llegar en un mensaje **entrante**. Necesariamente más amplio:
#: nadie elige lo que manda quien escribe desde WhatsApp, y un vídeo que se
#: descarta se pierde para siempre —el fichero vive en Meta unos días y luego
#: desaparece—.
_INBOUND_MIME_EXTENSIONS = {
    **_ALLOWED_MIME_EXTENSIONS,
    "video/mp4": ".mp4",
    "video/3gpp": ".3gp",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/amr": ".amr",
    "audio/wav": ".wav",
    "application/pdf": ".pdf",
}

#: La vuelta: extensión -> tipo MIME, para servir de nuevo lo que se guardó.
#: La deriva el propio módulo en vez de repetirla en el enrutador de descargas;
#: escritas por separado, una acababa admitiendo un formato que la otra no, y
#: el fichero se guardaba para luego no poder descargarse.
INBOUND_EXTENSION_MIMES = {
    extension: mime for mime, extension in _INBOUND_MIME_EXTENSIONS.items()
}


def save_incoming_media(
    body: bytes,
    *,
    mime_type: str | None,
    namespace: str,
    settings: Settings,
    filename: str | None = None,
) -> Attachment | None:
    """Guarda un adjunto que llegó de un canal externo.

    Devuelve ``None`` en vez de levantar cuando el tipo no se reconoce o el
    fichero pasa del máximo: un mensaje con un adjunto que no se puede guardar
    debe entrar igual —el texto y el hecho de que hubo un fichero importan— y
    no tumbar el webhook, que Meta reintentaría en bucle.
    """
    base = (mime_type or "").split(";")[0].strip().lower()
    extension = _INBOUND_MIME_EXTENSIONS.get(base)
    if extension is None or not body or len(body) > settings.inbound_media_max_bytes:
        return None

    stored_name = f"{uuid.uuid4().hex}{extension}"
    directory = Path(settings.uploads_dir) / namespace
    directory.mkdir(parents=True, exist_ok=True)
    (directory / stored_name).write_bytes(body)

    return Attachment(
        content_type=_content_type_for(base),
        url=f"/uploads/{namespace}/{stored_name}",
        mime_type=base,
        filename=filename,
        size_bytes=len(body),
    )


def _content_type_for(mime: str) -> ContentType:
    if mime.startswith("image/"):
        return ContentType.IMAGE
    if mime.startswith("video/"):
        return ContentType.VIDEO
    if mime.startswith("audio/"):
        return ContentType.AUDIO
    return ContentType.DOCUMENT


async def save_upload(file: UploadFile, *, namespace: str, settings: Settings) -> Attachment:
    """Guarda una imagen subida y devuelve su ``Attachment`` normalizado.

    Rechaza cualquier tipo que no sea una de las imágenes admitidas y cualquier
    cuerpo por encima de ``settings.upload_max_bytes``, para no dejar que un
    envío arbitrario agote el disco.
    """
    extension = _ALLOWED_MIME_EXTENSIONS.get(file.content_type or "")
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Solo se admiten imágenes JPEG, PNG, WEBP o GIF",
        )

    body = await file.read()
    if len(body) > settings.upload_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"La imagen supera el máximo de {settings.upload_max_bytes} bytes",
        )
    if not body:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Archivo vacío"
        )

    # Nombre aleatorio: evita colisiones, ataques de recorrido de ruta y que el
    # nombre original del cliente aparezca en la URL. La autorización se aplica
    # al descargar, no aquí.
    stored_name = f"{uuid.uuid4().hex}{extension}"
    directory = Path(settings.uploads_dir) / namespace
    directory.mkdir(parents=True, exist_ok=True)
    (directory / stored_name).write_bytes(body)

    return Attachment(
        content_type=ContentType.IMAGE,
        url=f"/uploads/{namespace}/{stored_name}",
        mime_type=file.content_type,
        filename=file.filename,
        size_bytes=len(body),
    )
