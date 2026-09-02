-- Trabajo en equipo: credenciales de agente, sesiones de consola,
-- derivaciones y notas internas.
--
-- Aplique este fichero sobre una base ya creada con 0001_init.sql.
-- En una instalación nueva basta con 0001_init.sql, que ya incluye todo.

BEGIN;

-- 1. Credenciales y presencia de los agentes.
ALTER TABLE agents ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS presence VARCHAR(16) NOT NULL DEFAULT 'offline';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agent_role;
ALTER TABLE agents ADD CONSTRAINT ck_agent_role
    CHECK (role IN ('agent', 'supervisor', 'admin'));

CREATE INDEX IF NOT EXISTS ix_agents_tenant_role ON agents (tenant_id, role);

-- 2. Sesiones de consola. Se guarda el resumen del token, nunca el token.
CREATE TABLE IF NOT EXISTS agent_sessions (
    token_hash VARCHAR(64) NOT NULL,
    agent_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_used_at TIMESTAMP WITH TIME ZONE,
    client_ip VARCHAR(64),
    user_agent VARCHAR(255),
    PRIMARY KEY (token_hash),
    FOREIGN KEY (agent_id) REFERENCES agents (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_agent_sessions_agent ON agent_sessions (agent_id, expires_at);

-- 3. Derivaciones. Registro inmutable: la conversación no se mueve ni se copia,
--    solo cambia de responsable, y aquí queda la traza de cada cambio.
CREATE TABLE IF NOT EXISTS assignments (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    conversation_id UUID NOT NULL,
    action VARCHAR(16) NOT NULL,
    from_agent_id UUID,
    to_agent_id UUID,
    by_agent_id UUID,
    note TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_assignment_action
        CHECK (action IN ('claim', 'transfer', 'release', 'close', 'reopen')),
    FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
    FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE,
    FOREIGN KEY (from_agent_id) REFERENCES agents (id) ON DELETE SET NULL,
    FOREIGN KEY (to_agent_id) REFERENCES agents (id) ON DELETE SET NULL,
    FOREIGN KEY (by_agent_id) REFERENCES agents (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_assignments_conversation
    ON assignments (conversation_id, created_at);
CREATE INDEX IF NOT EXISTS ix_assignments_to_agent ON assignments (to_agent_id, created_at);

-- 4. Notas internas. En tabla propia y no en `messages`, para que ningún
--    adaptador de canal pueda enviarlas al cliente por descuido.
CREATE TABLE IF NOT EXISTS internal_notes (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    conversation_id UUID NOT NULL,
    agent_id UUID,
    body TEXT NOT NULL,
    mentions JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
    FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE,
    FOREIGN KEY (agent_id) REFERENCES agents (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_internal_notes_conversation
    ON internal_notes (conversation_id, created_at);

COMMIT;
