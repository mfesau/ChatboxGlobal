"""Base de conocimiento del asistente (recuperación aumentada).

El asistente respondía solo con lo que el modelo trae de fábrica más lo que
dijera el prompt del sistema. Eso basta para saludar y derivar, pero no para
contestar cuánto cuesta la habitación superior o qué dice la política de
cancelación: son datos de la casa, y el modelo se los inventa con aplomo.

Aquí se guarda lo que la empresa escribe —normas, tarifas, preguntas
frecuentes—, troceado y con un vector de significado por trozo, para que el
asistente busque antes de responder. La búsqueda es por significado y no por
palabras: quien pregunta «¿puedo anular?» encuentra el párrafo que habla de
«cancelación» aunque no comparta ni una palabra.

**Se ofrece como herramienta, no como contexto pegado al prompt.** Así el
modelo solo paga la búsqueda cuando la necesita —un «buenos días» no la
dispara— y de la respuesta se puede saber qué trozos la fundamentaron.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import EMBEDDING_DIMENSIONS, KnowledgeChunk, KnowledgeDocument
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Modelo de vectores. Se le pide explícitamente el tamaño de salida porque
#: por omisión devuelve muchas más dimensiones de las que hace falta, y cada
#: una ocupa sitio en la tabla y en el índice.
EMBEDDING_MODEL = "gemini-embedding-001"

#: Tamaño de trozo, en caracteres. Lo bastante grande para que un párrafo
#: quepa entero y lo bastante pequeño para que el trozo recuperado no traiga
#: media página de relleno alrededor de la frase que importa.
CHUNK_SIZE = 800

#: Solape entre trozos consecutivos. Sin él, una frase partida justo por el
#: corte no aparece completa en ninguno de los dos y deja de encontrarse.
CHUNK_OVERLAP = 100

#: Cuántos trozos se devuelven por búsqueda.
DEFAULT_TOP_K = 5


class KnowledgeError(RuntimeError):
    """La base de conocimiento no se puede usar en esta instalación."""


@dataclass(slots=True)
class Passage:
    """Trozo recuperado, con el documento del que salió."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    content: str


# --------------------------------------------------------------------------- #
# Troceado
# --------------------------------------------------------------------------- #
def split(text: str, *, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Trocea el documento respetando los párrafos cuando puede.

    Se agrupan párrafos hasta llenar el trozo; el que no cabe entero abre el
    siguiente. Un párrafo más largo que el tope se corta por longitud, porque
    la alternativa —dejarlo entero— produciría trozos enormes cuya recuperación
    traería páginas en vez de respuestas.
    """
    parrafos = [p.strip() for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]
    if not parrafos:
        return []

    trozos: list[str] = []
    actual = ""
    for parrafo in parrafos:
        while len(parrafo) > size:
            if actual:
                trozos.append(actual)
                actual = ""
            trozos.append(parrafo[:size])
            parrafo = parrafo[size - overlap :]
        if not actual:
            actual = parrafo
        elif len(actual) + len(parrafo) + 2 <= size:
            actual = f"{actual}\n\n{parrafo}"
        else:
            trozos.append(actual)
            # El final del trozo anterior encabeza el siguiente: una frase
            # partida por el corte sigue apareciendo entera en alguno.
            cola = actual[-overlap:].lstrip()
            actual = f"{cola}\n\n{parrafo}" if cola else parrafo
    if actual:
        trozos.append(actual)
    return trozos


# --------------------------------------------------------------------------- #
# Vectores
# --------------------------------------------------------------------------- #
async def embed(
    texts: list[str], *, settings: Settings, client: Any | None = None, query: bool = False
) -> list[list[float]]:
    """Calcula el vector de cada texto. Lanza si no hay clave de IA.

    ``query`` distingue la pregunta del documento: el modelo admite decirle
    para qué se va a usar el vector, y la asimetría mejora bastante los
    resultados —una pregunta corta y un párrafo largo no se parecen «en
    forma», pero sí en lo que quieren decir—.
    """
    if not texts:
        return []
    if client is None:
        if settings.google_api_key is None:
            raise KnowledgeError(
                "La base de conocimiento necesita GOOGLE_API_KEY: sin ella no se "
                "pueden calcular los vectores de los documentos"
            )
        from google import genai  # importación diferida: el módulo es pesado

        client = genai.Client(api_key=settings.google_api_key.get_secret_value())

    response = await client.aio.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config={
            "output_dimensionality": EMBEDDING_DIMENSIONS,
            "task_type": "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT",
        },
    )
    return [list(item.values) for item in response.embeddings]


# --------------------------------------------------------------------------- #
# Alta y baja de documentos
# --------------------------------------------------------------------------- #
async def ingest(
    session: AsyncSession,
    *,
    settings: Settings,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID | None,
    title: str,
    text: str,
    source_type: str = "text",
    filename: str | None = None,
    created_by: str | None = None,
    client: Any | None = None,
) -> KnowledgeDocument:
    """Trocea, vectoriza y guarda un documento. Devuelve la fila creada."""
    trozos = split(text)
    if not trozos:
        raise KnowledgeError("El documento no tiene texto aprovechable")

    vectores = await embed(trozos, settings=settings, client=client)

    document = KnowledgeDocument(
        tenant_id=tenant_id,
        department_id=department_id,
        title=title.strip()[:200],
        source_type=source_type,
        filename=filename,
        created_by=created_by,
        chunk_count=len(trozos),
    )
    session.add(document)
    await session.flush()

    for orden, (contenido, vector) in enumerate(zip(trozos, vectores, strict=False)):
        session.add(
            KnowledgeChunk(
                document_id=document.id,
                tenant_id=tenant_id,
                ord=orden,
                content=contenido,
                embedding=vector,
            )
        )
    log.info(
        "knowledge_document_ingested",
        document_id=str(document.id),
        title=document.title,
        chunks=len(trozos),
    )
    return document


async def list_documents(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> list[KnowledgeDocument]:
    stmt = (
        select(KnowledgeDocument)
        .where(KnowledgeDocument.tenant_id == tenant_id)
        .order_by(KnowledgeDocument.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def get_document(
    session: AsyncSession, document_id: uuid.UUID
) -> KnowledgeDocument | None:
    return await session.get(KnowledgeDocument, document_id)


async def delete_document(session: AsyncSession, document: KnowledgeDocument) -> None:
    """Borra el documento y sus trozos."""
    await session.execute(
        delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
    )
    await session.delete(document)


# --------------------------------------------------------------------------- #
# Búsqueda
# --------------------------------------------------------------------------- #
async def search(
    session: AsyncSession,
    *,
    settings: Settings,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID | None,
    query: str,
    limit: int = DEFAULT_TOP_K,
    client: Any | None = None,
) -> list[Passage]:
    """Trozos más cercanos a la pregunta, del más al menos parecido.

    Se consultan los documentos de toda la empresa **más** los del
    departamento de la conversación: las normas generales valen en cualquier
    rama, y las de una rama no deben aparecer en las demás.
    """
    condiciones = [
        KnowledgeDocument.tenant_id == tenant_id,
        KnowledgeDocument.is_active.is_(True),
    ]
    if department_id is None:
        condiciones.append(KnowledgeDocument.department_id.is_(None))
    else:
        condiciones.append(
            or_(
                KnowledgeDocument.department_id == department_id,
                KnowledgeDocument.department_id.is_(None),
            )
        )

    dialect = session.get_bind().dialect.name
    if dialect != "postgresql":
        # SQLite no tiene la extensión de vectores. Se busca por palabras, que
        # basta para las pruebas y para una instalación de juguete; en
        # producción, que es PostgreSQL, se busca por significado.
        return await _keyword_search(session, condiciones, query, limit)

    [vector] = await embed([query], settings=settings, client=client, query=True)
    stmt = (
        select(
            KnowledgeChunk.id,
            KnowledgeChunk.document_id,
            KnowledgeDocument.title,
            KnowledgeChunk.content,
        )
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(*condiciones, KnowledgeChunk.embedding.is_not(None))
        # `cosine_distance` es el operador `<=>` de pgvector: 0 = idénticos.
        .order_by(KnowledgeChunk.embedding.cosine_distance(vector))
        .limit(limit)
    )
    return _as_passages((await session.execute(stmt)).all())


async def _keyword_search(
    session: AsyncSession, condiciones: list[Any], query: str, limit: int
) -> list[Passage]:
    """Rescate por palabras para el dialecto sin vectores.

    Existe para que las pruebas —que corren sobre SQLite— ejerciten el camino
    completo. En producción, que es PostgreSQL, la búsqueda es por significado
    y esta rama no llega a ejecutarse.
    """
    palabras = [palabra for palabra in query.lower().split() if len(palabra) > 3][:6]
    if not palabras:
        return []

    stmt = (
        select(
            KnowledgeChunk.id,
            KnowledgeChunk.document_id,
            KnowledgeDocument.title,
            KnowledgeChunk.content,
        )
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(*condiciones)
        .where(
            or_(
                *[
                    func.lower(KnowledgeChunk.content).like(f"%{palabra}%")
                    for palabra in palabras
                ]
            )
        )
        .limit(limit)
    )
    return _as_passages((await session.execute(stmt)).all())


def _as_passages(filas: list[Any]) -> list[Passage]:
    return [
        Passage(chunk_id=fila[0], document_id=fila[1], title=fila[2], content=fila[3])
        for fila in filas
    ]
