-- Base de conocimiento para el asistente.
--
-- Documentos que administración sube —normas de la casa, tarifas, preguntas
-- frecuentes— troceados y con su vector de significado, para que el asistente
-- responda con lo que hay escrito en lugar de con lo que le parece.
--
-- Requiere la extensión `vector` (pgvector). La imagen de PostgreSQL del
-- docker-compose pasa a ser pgvector/pgvector:pg17, que es la misma versión
-- mayor: el directorio de datos existente sigue sirviendo tal cual.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0016. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo más
-- este fichero, que la generación desde el modelo no cubre la extensión.

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS kb_documents (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    -- Nulo = vale para toda la empresa. Con departamento, solo el asistente de
    -- esa rama lo consulta: las tarifas del hotel no ayudan en soporte técnico.
    department_id   UUID REFERENCES departments(id) ON DELETE CASCADE,
    title           VARCHAR(200) NOT NULL,
    source_type     VARCHAR(16)  NOT NULL DEFAULT 'text',
    filename        VARCHAR(255),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    created_by      VARCHAR(160),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_kb_documents_tenant
    ON kb_documents (tenant_id, department_id);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id           UUID PRIMARY KEY,
    document_id  UUID NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    ord          INTEGER NOT NULL,
    content      TEXT NOT NULL,
    -- 768 dimensiones: es lo que se le pide a gemini-embedding-001 mediante
    -- output_dimensionality. Cambiar ese número obliga a volver a calcular
    -- todos los vectores, así que se fija aquí y en app/core/knowledge.py.
    embedding    vector(768),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_kb_chunks_document ON kb_chunks (document_id, ord);

-- HNSW y no IVFFlat: no necesita entrenarse con un volumen previo de datos,
-- que es justo lo que no hay cuando alguien sube su primer documento.
CREATE INDEX IF NOT EXISTS ix_kb_chunks_embedding
    ON kb_chunks USING hnsw (embedding vector_cosine_ops);

COMMIT;
