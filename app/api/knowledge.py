"""Alta y baja de documentos de la base de conocimiento del asistente.

Reservado a administración: lo que se sube aquí es lo que el asistente
responderá como palabra de la casa a cualquiera que escriba.

El fichero original no se conserva. Lo que sirve para responder son sus trozos
con el vector calculado, y guardar además el PDF entero solo ocuparía sitio; el
original está donde lo tenga quien lo subió.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.api.deps import AdminDep, PrincipalDep, SessionDep, SettingsDep
from app.core import knowledge
from app.db import repositories as repo
from app.db.engine import session_scope
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api/knowledge", tags=["base de conocimiento"])

#: Tope de un documento subido. Un manual entero cabe de sobra; el límite está
#: para que nadie deje el proceso vectorizando una biblioteca.
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024

#: Extensiones admitidas y cómo se lee cada una.
SUPPORTED_EXTENSIONS = {".txt": "text", ".md": "markdown", ".pdf": "pdf"}


class DocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    source_type: str
    filename: str | None
    department_id: uuid.UUID | None
    is_active: bool
    #: ``pending`` | ``ready`` | ``failed``
    status: str
    error: str | None
    chunk_count: int
    created_by: str | None
    created_at: str


class DocumentIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)
    department_id: uuid.UUID | None = None


def _out(document: Any) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        title=document.title,
        source_type=document.source_type,
        filename=document.filename,
        department_id=document.department_id,
        is_active=document.is_active,
        status=document.status,
        error=document.error,
        chunk_count=document.chunk_count,
        created_by=document.created_by,
        created_at=document.created_at.isoformat(),
    )


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep
) -> list[DocumentOut]:
    """Documentos cargados. Visible para cualquier agente.

    No es información reservada —es lo que el asistente le cuenta a cualquiera
    que escriba— y a quien atiende le sirve saber qué sabe el bot.
    """
    tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
    documents = await knowledge.list_documents(session, tenant_id=tenant.id)
    return [_out(document) for document in documents]


@router.post("/documents", status_code=status.HTTP_201_CREATED, response_model=DocumentOut)
async def create_document(
    body: DocumentIn,
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
) -> DocumentOut:
    """Alta de un documento escrito a mano. Reservado a administración."""
    tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
    await _check_department(session, tenant.id, body.department_id)

    document = await knowledge.register(
        session,
        tenant_id=tenant.id,
        department_id=body.department_id,
        title=body.title,
        source_type="text",
        created_by=principal.audit_actor,
    )
    await _audit(session, principal, tenant.id, "knowledge_document_created", document)
    await _process_later(background, session, document, body.text, settings)
    return _out(document)


@router.post("/documents/upload", status_code=status.HTTP_201_CREATED, response_model=DocumentOut)
async def upload_document(
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    department_id: Annotated[uuid.UUID | None, Form()] = None,
) -> DocumentOut:
    """Alta desde un fichero ``.txt``, ``.md`` o ``.pdf``. Reservado a administración."""
    tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
    await _check_department(session, tenant.id, department_id)

    nombre = file.filename or "documento"
    extension = f".{nombre.rsplit('.', 1)[-1].lower()}" if "." in nombre else ""
    source_type = SUPPORTED_EXTENSIONS.get(extension)
    if source_type is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Formato no admitido: {extension or nombre}. "
            f"Admitidos: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    contenido = await file.read()
    if len(contenido) > MAX_DOCUMENT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El documento pasa de {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB",
        )

    texto = _extract_pdf(contenido) if source_type == "pdf" else _decode(contenido)
    if not texto.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "No se pudo extraer texto del fichero. Un PDF escaneado es una "
                "imagen: hay que pasarlo antes por un reconocedor de texto."
            ),
        )

    document = await knowledge.register(
        session,
        tenant_id=tenant.id,
        department_id=department_id,
        title=(title or nombre.rsplit(".", 1)[0]),
        source_type=source_type,
        filename=nombre,
        created_by=principal.audit_actor,
    )
    await _audit(session, principal, tenant.id, "knowledge_document_created", document)
    await _process_later(background, session, document, texto, settings)
    return _out(document)


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: uuid.UUID,
    session: SessionDep,
    principal: AdminDep,
) -> dict[str, str]:
    """Borra un documento y sus trozos. Reservado a administración."""
    document = await knowledge.get_document(session, document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Documento no encontrado"
        )

    await _audit(session, principal, document.tenant_id, "knowledge_document_deleted", document)
    await knowledge.delete_document(session, document)
    return {"status": "ok"}


async def _process_later(
    background: BackgroundTasks,
    session: Any,
    document: Any,
    text: str,
    settings: Any,
) -> None:
    """Encola el troceado y la vectorización para después de responder.

    Confirma antes de encolar. La tarea de fondo abre su propia transacción y
    arranca antes de que se cierre la de la petición: sin esta confirmación no
    encuentra la fila que acaba de crearse, se va sin hacer nada, y el
    documento se queda en «pendiente» para siempre.
    """
    await session.commit()
    background.add_task(_process, document.id, text, settings)


async def _process(document_id: uuid.UUID, text: str, settings: Any) -> None:
    """Procesa el documento en su propia transacción.

    La de la petición ya se cerró cuando esto corre, así que abre la suya. Si
    el proceso muere a mitad, el documento se queda en ``pending``: se ve en la
    consola y se resuelve borrándolo y volviéndolo a subir. Reintentarlo solo
    con lo guardado no se puede, porque el texto original no se conserva —lo
    que sirve para responder son los trozos con su vector—.
    """
    async with session_scope() as session:
        document = await knowledge.get_document(session, document_id)
        if document is None:
            return
        await knowledge.process(session, document, settings=settings, text=text)


async def _check_department(
    session: Any, tenant_id: uuid.UUID, department_id: uuid.UUID | None
) -> None:
    if department_id is None:
        return
    department = await repo.get_department(session, department_id)
    if department is None or department.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
        )


async def _audit(
    session: Any, principal: Any, tenant_id: uuid.UUID, action: str, document: Any
) -> None:
    await repo.record_audit(
        session,
        tenant_id=tenant_id,
        actor=principal.audit_actor,
        action=action,
        subject_type="knowledge_document",
        subject_id=str(document.id),
        detail={"title": document.title, "chunks": document.chunk_count},
    )


def _decode(content: bytes) -> str:
    """Texto plano, tolerante con la codificación del fichero."""
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", "replace")


def _extract_pdf(content: bytes) -> str:
    """Texto de un PDF. Devuelve cadena vacía si no se puede leer.

    ``pypdf`` es opcional a propósito: quien solo suba texto y markdown no
    tiene por qué cargar con la dependencia, y el mensaje de error explica qué
    instalar en lugar de fallar con un ``ImportError`` a medio camino.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Para subir PDF hace falta el paquete «pypdf» en el servidor",
        ) from exc

    import io

    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # noqa: BLE001 - PDF cifrado, roto o no estándar
        log.warning("pdf_extract_failed", error=str(exc)[:200])
        return ""
