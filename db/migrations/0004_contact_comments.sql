-- Ficha de contacto: historial de comentarios de supervisión.
--
-- Aplique este fichero sobre una base que ya tenga 0001_init.sql, 0002 y 0003.
-- En una instalación nueva basta con 0001_init.sql regenerado desde el modelo.

BEGIN;

CREATE TABLE IF NOT EXISTS contact_comments (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    contact_id UUID NOT NULL,
    agent_id UUID,
    body TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
    FOREIGN KEY (contact_id) REFERENCES contacts (id) ON DELETE CASCADE,
    FOREIGN KEY (agent_id) REFERENCES agents (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_contact_comments_contact ON contact_comments (contact_id, created_at);

COMMIT;
