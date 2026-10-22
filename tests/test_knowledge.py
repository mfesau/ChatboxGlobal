"""Base de conocimiento: troceado, alta, búsqueda y uso desde el asistente.

Los vectores se calculan con un cliente falso: llamar al modelo de verdad haría
las pruebas lentas, caras y dependientes de la red. Lo que se comprueba aquí es
el troceado, la persistencia y el camino completo hasta la herramienta del
asistente; que Gemini devuelva buenos vectores no es cosa de esta batería.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from app.config import get_settings
from app.core import knowledge
from app.db import repositories as repo
from app.db.engine import session_scope

DOCUMENTO = """Política de cancelación

Las reservas se pueden anular sin coste hasta 48 horas antes de la entrada.
Pasado ese plazo se cobra la primera noche.

Horario de recepción

La recepción atiende de 7:00 a 23:00. Fuera de ese horario hay un teléfono de
guardia indicado en la puerta.

Mascotas

Se admiten perros de hasta 10 kg con un suplemento de 15 euros por noche.
"""


@dataclass
class _RespuestaFalsa:
    embeddings: list


@dataclass
class _VectorFalso:
    values: list[float]


class ClienteFalso:
    """Devuelve vectores deterministas, sin salir a la red."""

    def __init__(self) -> None:
        self.llamadas: list[dict] = []
        self.aio = self

    @property
    def models(self):
        return self

    async def embed_content(self, *, model, contents, config):
        self.llamadas.append({"model": model, "contents": contents, "config": config})
        return _RespuestaFalsa(
            embeddings=[_VectorFalso(values=_vector_de(texto)) for texto in contents]
        )


def _vector_de(texto: str) -> list[float]:
    """Vector reproducible a partir del texto, del tamaño que espera la columna."""
    base = [((ord(c) % 17) / 17) for c in texto[:64]]
    relleno = (base * 100)[: knowledge.EMBEDDING_DIMENSIONS]
    return relleno + [0.0] * (knowledge.EMBEDDING_DIMENSIONS - len(relleno))


# --------------------------------------------------------------------------- #
# Troceado
# --------------------------------------------------------------------------- #
def test_the_document_is_split_at_paragraph_boundaries():
    trozos = knowledge.split(DOCUMENTO, size=200, overlap=40)

    assert len(trozos) > 1
    assert all(len(t) <= 200 + 40 for t in trozos)
    # Ninguna sección se pierde por el camino.
    entero = " ".join(trozos)
    for frase in ("48 horas", "7:00 a 23:00", "10 kg"):
        assert frase in entero


def test_a_paragraph_longer_than_the_chunk_is_cut_by_length():
    largo = "x" * 2_000
    trozos = knowledge.split(largo, size=500, overlap=50)
    assert len(trozos) >= 4
    assert all(len(t) <= 500 for t in trozos)


def test_consecutive_chunks_overlap():
    """Sin solape, una frase partida por el corte deja de encontrarse."""
    parrafos = [f"Párrafo número {i} con bastante texto dentro." for i in range(20)]
    trozos = knowledge.split("\n\n".join(parrafos), size=120, overlap=40)

    assert len(trozos) > 2
    # El final de un trozo reaparece al principio del siguiente.
    cola = trozos[0][-30:].strip()
    assert cola and cola in trozos[1]


def test_an_empty_document_yields_nothing():
    assert knowledge.split("") == []
    assert knowledge.split("   \n\n  \n") == []


# --------------------------------------------------------------------------- #
# Alta y búsqueda
# --------------------------------------------------------------------------- #
async def test_a_document_is_stored_with_one_vector_per_chunk(team):
    cliente = ClienteFalso()
    async with session_scope() as session:
        documento = await knowledge.ingest(
            session,
            settings=get_settings(),
            tenant_id=team["tenant_id"],
            department_id=None,
            title="Normas de la casa",
            text=DOCUMENTO,
            created_by="agent:admin@empresa.local",
            client=cliente,
        )
        document_id = documento.id
        assert documento.chunk_count > 0

    async with session_scope() as session:
        documentos = await knowledge.list_documents(session, tenant_id=team["tenant_id"])
        assert [d.id for d in documentos] == [document_id]
        chunks = await knowledge.search(
            session,
            settings=get_settings(),
            tenant_id=team["tenant_id"],
            department_id=None,
            query="recepción horario guardia",
            client=ClienteFalso(),
        )
        assert chunks

    # Al vectorizar un documento se le dice al modelo para qué es el vector.
    assert cliente.llamadas[0]["config"]["task_type"] == "RETRIEVAL_DOCUMENT"
    assert cliente.llamadas[0]["config"]["output_dimensionality"] == 768


async def test_an_empty_document_is_refused(team):
    async with session_scope() as session:
        with pytest.raises(knowledge.KnowledgeError):
            await knowledge.ingest(
                session,
                settings=get_settings(),
                tenant_id=team["tenant_id"],
                department_id=None,
                title="Vacío",
                text="   ",
                client=ClienteFalso(),
            )


async def test_without_an_api_key_the_reason_is_explicit(team):
    """El fallo debe decir qué falta, no reventar dentro del cliente."""
    async with session_scope() as session:
        with pytest.raises(knowledge.KnowledgeError, match="GOOGLE_API_KEY"):
            await knowledge.ingest(
                session,
                settings=get_settings(),  # sin clave: conftest la deja en blanco
                tenant_id=team["tenant_id"],
                department_id=None,
                title="Normas",
                text=DOCUMENTO,
            )


async def test_the_search_finds_the_relevant_passage(team):
    async with session_scope() as session:
        await knowledge.ingest(
            session,
            settings=get_settings(),
            tenant_id=team["tenant_id"],
            department_id=None,
            title="Normas de la casa",
            text=DOCUMENTO,
            client=ClienteFalso(),
        )

    async with session_scope() as session:
        pasajes = await knowledge.search(
            session,
            settings=get_settings(),
            tenant_id=team["tenant_id"],
            department_id=None,
            query="¿admiten mascotas en el hotel?",
            client=ClienteFalso(),
        )
    assert pasajes
    assert any("perros" in p.content for p in pasajes)
    assert all(p.title == "Normas de la casa" for p in pasajes)


async def test_a_department_document_does_not_leak_into_another_branch(team, as_agent):
    """Las tarifas del hotel no ayudan en soporte técnico, y no deben aparecer."""
    admin = await as_agent(team["admin"]["email"])
    hotel = await admin.post("/api/departments", json={"name": "Hotel"})
    soporte = await admin.post("/api/departments", json={"name": "Soporte"})
    hotel_id = uuid.UUID(hotel.json()["id"])
    soporte_id = uuid.UUID(soporte.json()["id"])

    async with session_scope() as session:
        await knowledge.ingest(
            session,
            settings=get_settings(),
            tenant_id=team["tenant_id"],
            department_id=hotel_id,
            title="Tarifas del hotel",
            text="Tarifas\n\nLa habitación doble cuesta 90 euros la noche.",
            client=ClienteFalso(),
        )

    async with session_scope() as session:
        desde_soporte = await knowledge.search(
            session,
            settings=get_settings(),
            tenant_id=team["tenant_id"],
            department_id=soporte_id,
            query="cuesta habitación doble noche",
            client=ClienteFalso(),
        )
        desde_hotel = await knowledge.search(
            session,
            settings=get_settings(),
            tenant_id=team["tenant_id"],
            department_id=hotel_id,
            query="cuesta habitación doble noche",
            client=ClienteFalso(),
        )
    assert desde_soporte == []
    assert desde_hotel


async def test_a_company_wide_document_is_visible_from_every_branch(team, as_agent):
    admin = await as_agent(team["admin"]["email"])
    hotel = await admin.post("/api/departments", json={"name": "Hotel"})
    hotel_id = uuid.UUID(hotel.json()["id"])

    async with session_scope() as session:
        await knowledge.ingest(
            session,
            settings=get_settings(),
            tenant_id=team["tenant_id"],
            department_id=None,
            title="Normas de la casa",
            text=DOCUMENTO,
            client=ClienteFalso(),
        )

    async with session_scope() as session:
        pasajes = await knowledge.search(
            session,
            settings=get_settings(),
            tenant_id=team["tenant_id"],
            department_id=hotel_id,
            query="anular reserva cancelación plazo",
            client=ClienteFalso(),
        )
    assert pasajes


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
async def test_only_administration_uploads_documents(as_agent, team, monkeypatch):
    monkeypatch.setattr(knowledge, "embed", _make_fake_embed())
    ana = await as_agent(team["ana"]["email"])
    admin = await as_agent(team["admin"]["email"])

    cuerpo = {"title": "Normas", "text": DOCUMENTO}
    assert (await ana.post("/api/knowledge/documents", json=cuerpo)).status_code == 403

    alta = await admin.post("/api/knowledge/documents", json=cuerpo)
    assert alta.status_code == 201, alta.text

    # Cualquier agente sí puede ver qué sabe el bot.
    listado = await ana.get("/api/knowledge/documents")
    assert listado.status_code == 200
    assert [d["title"] for d in listado.json()] == ["Normas"]


async def test_the_upload_answers_before_vectorising(as_agent, team, monkeypatch):
    """Un PDF de varias páginas son decenas de llamadas al modelo.

    Esperarlas dentro de la petición la hacía agotar el plazo, así que la
    respuesta sale enseguida y el troceado ocurre después.
    """
    monkeypatch.setattr(knowledge, "embed", _make_fake_embed())
    admin = await as_agent(team["admin"]["email"])

    alta = await admin.post(
        "/api/knowledge/documents", json={"title": "Normas", "text": DOCUMENTO}
    )
    assert alta.status_code == 201
    # Nace pendiente y todavía sin trozos.
    assert alta.json()["status"] == "pending"
    assert alta.json()["chunk_count"] == 0

    # La tarea de fondo lo deja listo.
    listado = (await admin.get("/api/knowledge/documents")).json()
    assert listado[0]["status"] == "ready"
    assert listado[0]["chunk_count"] > 0
    assert listado[0]["error"] is None


async def test_a_document_that_cannot_be_vectorised_says_why(as_agent, team, monkeypatch):
    """Sin clave de IA el fallo tiene que verse, no desaparecer en el log."""
    admin = await as_agent(team["admin"]["email"])

    alta = await admin.post(
        "/api/knowledge/documents", json={"title": "Normas", "text": DOCUMENTO}
    )
    assert alta.status_code == 201

    listado = (await admin.get("/api/knowledge/documents")).json()
    assert listado[0]["status"] == "failed"
    assert "GOOGLE_API_KEY" in listado[0]["error"]


async def test_a_document_still_processing_does_not_offer_the_tool(team):
    """Ofrecerla por uno sin trozos manda al modelo a buscar en el vacío."""
    async with session_scope() as session:
        await knowledge.register(
            session,
            tenant_id=team["tenant_id"],
            department_id=None,
            title="A medias",
        )

    async with session_scope() as session:
        assert await repo.tenant_has_knowledge(session, tenant_id=team["tenant_id"]) is False


async def test_deleting_a_document_takes_its_chunks(as_agent, team, monkeypatch):
    monkeypatch.setattr(knowledge, "embed", _make_fake_embed())
    admin = await as_agent(team["admin"]["email"])
    alta = await admin.post(
        "/api/knowledge/documents", json={"title": "Normas", "text": DOCUMENTO}
    )
    document_id = alta.json()["id"]

    assert (await admin.delete(f"/api/knowledge/documents/{document_id}")).status_code == 200
    assert (await admin.get("/api/knowledge/documents")).json() == []

    # Y queda anotado en la auditoría.
    trace = await admin.get("/api/audit?action=knowledge_document_deleted")
    assert trace.json()[0]["subject_id"] == document_id


async def test_an_unsupported_file_is_refused(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    respuesta = await admin.post(
        "/api/knowledge/documents/upload",
        files={"file": ("normas.exe", b"MZ", "application/octet-stream")},
    )
    assert respuesta.status_code == 422
    assert ".txt" in respuesta.json()["detail"]


async def test_a_text_file_is_ingested(as_agent, team, monkeypatch):
    monkeypatch.setattr(knowledge, "embed", _make_fake_embed())
    admin = await as_agent(team["admin"]["email"])
    respuesta = await admin.post(
        "/api/knowledge/documents/upload",
        files={"file": ("normas.txt", DOCUMENTO.encode(), "text/plain")},
    )
    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["source_type"] == "text"
    assert respuesta.json()["title"] == "normas"


# --------------------------------------------------------------------------- #
# Uso desde el asistente
# --------------------------------------------------------------------------- #
async def test_the_tool_is_only_offered_when_there_is_something_to_search(team):
    """Ofrecerla con la base vacía invita al modelo a buscar en el vacío."""
    async with session_scope() as session:
        assert await repo.tenant_has_knowledge(session, tenant_id=team["tenant_id"]) is False
        await knowledge.ingest(
            session,
            settings=get_settings(),
            tenant_id=team["tenant_id"],
            department_id=None,
            title="Normas",
            text=DOCUMENTO,
            client=ClienteFalso(),
        )

    async with session_scope() as session:
        assert await repo.tenant_has_knowledge(session, tenant_id=team["tenant_id"]) is True


def _make_fake_embed():
    async def embed(texts, *, settings, client=None, query=False):
        return [_vector_de(texto) for texto in texts]

    return embed
