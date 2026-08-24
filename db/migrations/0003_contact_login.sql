-- Login obligatorio del chatbox público: cuenta y sesión de cliente.
--
-- Aplique este fichero sobre una base que ya tenga 0001_init.sql y
-- 0002_teamwork.sql. En una instalación nueva basta con 0001_init.sql
-- regenerado a partir del modelo actual.

BEGIN;

-- 1. Credencial del cliente sobre la tabla de contactos existente.
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);

ALTER TABLE contacts DROP CONSTRAINT IF EXISTS uq_contact_email;
ALTER TABLE contacts ADD CONSTRAINT uq_contact_email UNIQUE (tenant_id, primary_email);

-- 2. Sesiones del chatbox. Se guarda el resumen del token, nunca el token.
CREATE TABLE IF NOT EXISTS contact_sessions (
    token_hash VARCHAR(64) NOT NULL,
    contact_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_used_at TIMESTAMP WITH TIME ZONE,
    client_ip VARCHAR(64),
    user_agent VARCHAR(255),
    PRIMARY KEY (token_hash),
    FOREIGN KEY (contact_id) REFERENCES contacts (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_contact_sessions_contact ON contact_sessions (contact_id, expires_at);

COMMIT;
