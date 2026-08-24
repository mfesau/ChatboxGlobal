-- Departamentos: acotan la cola común y las conversaciones sin dueño.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0004. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo.

BEGIN;

-- 1. Departamentos del inquilino.
CREATE TABLE IF NOT EXISTS departments (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    name VARCHAR(120) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_department_name UNIQUE (tenant_id, name),
    FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE
);

-- 2. Departamento principal del agente, más allá del rol.
ALTER TABLE agents ADD COLUMN IF NOT EXISTS department_id UUID;
ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_department_id_fkey;
ALTER TABLE agents ADD CONSTRAINT agents_department_id_fkey
    FOREIGN KEY (department_id) REFERENCES departments (id) ON DELETE SET NULL;

-- 3. Departamentos adicionales que un agente puede atender.
CREATE TABLE IF NOT EXISTS agent_departments (
    agent_id UUID NOT NULL,
    department_id UUID NOT NULL,
    PRIMARY KEY (agent_id, department_id),
    FOREIGN KEY (agent_id) REFERENCES agents (id) ON DELETE CASCADE,
    FOREIGN KEY (department_id) REFERENCES departments (id) ON DELETE CASCADE
);

-- 4. A qué departamento quedó derivada una conversación (nulo = cola común).
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS department_id UUID;
ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_department_id_fkey;
ALTER TABLE conversations ADD CONSTRAINT conversations_department_id_fkey
    FOREIGN KEY (department_id) REFERENCES departments (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_conversations_department_id ON conversations (department_id);

-- 5. Traza de las derivaciones a un departamento en vez de a una persona.
ALTER TABLE assignments ADD COLUMN IF NOT EXISTS to_department_id UUID;
ALTER TABLE assignments DROP CONSTRAINT IF EXISTS assignments_to_department_id_fkey;
ALTER TABLE assignments ADD CONSTRAINT assignments_to_department_id_fkey
    FOREIGN KEY (to_department_id) REFERENCES departments (id) ON DELETE SET NULL;

ALTER TABLE assignments DROP CONSTRAINT IF EXISTS ck_assignment_action;
ALTER TABLE assignments ADD CONSTRAINT ck_assignment_action
    CHECK (action IN ('claim', 'transfer', 'release', 'close', 'reopen', 'transfer_department'));

COMMIT;
