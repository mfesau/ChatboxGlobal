-- Encuesta de satisfacción al cerrar una conversación.
--
-- Se envía por el mismo canal por el que se atendió: donde hay botones —web,
-- Telegram, WhatsApp, Facebook— la nota se pulsa; en correo y SMS viaja un
-- enlace con un testigo, porque escribir «4» en un SMS de vuelta no llega
-- necesariamente al mismo sitio.
--
-- La configuración vive en departments.enabled_modules, que es donde ya viven
-- los módulos por rama; aquí solo se guardan las encuestas.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0017. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo.

BEGIN;

CREATE TABLE IF NOT EXISTS csat_surveys (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    contact_id      UUID REFERENCES contacts(id) ON DELETE SET NULL,
    -- Quién atendía al cerrarse. Se copia en lugar de mirarse después: una
    -- reasignación posterior no debe reescribir a quién puntuaron.
    agent_id        UUID REFERENCES agents(id) ON DELETE SET NULL,
    department_id   UUID REFERENCES departments(id) ON DELETE SET NULL,
    -- Testigo del enlace público para correo y SMS. Único y aleatorio: es la
    -- única credencial de esa página, que no tiene sesión.
    token           VARCHAR(64) NOT NULL UNIQUE,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    responded_at    TIMESTAMPTZ,
    score           SMALLINT,
    comment         TEXT,
    CONSTRAINT ck_csat_score CHECK (score IS NULL OR (score BETWEEN 1 AND 5))
);

-- Una encuesta viva por conversación: si se cierra, se reabre y se vuelve a
-- cerrar, se reutiliza la que había en vez de acumular preguntas.
CREATE UNIQUE INDEX IF NOT EXISTS ux_csat_conversation
    ON csat_surveys (conversation_id);

CREATE INDEX IF NOT EXISTS ix_csat_tenant_time
    ON csat_surveys (tenant_id, sent_at);

COMMIT;
