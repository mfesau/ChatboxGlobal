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

#: Extensiones admitidas por tipo MIME. Cualquier otro tipo se rechaza.
_ALLOWED_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


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
