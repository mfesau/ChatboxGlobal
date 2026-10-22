-- Reglas de automatización y mensajes programados.
--
-- Lo que hasta ahora había que hacer a mano en cada conversación —etiquetar
-- las que hablan de facturación, mandarlas al departamento que corresponde,
-- contestar lo mismo de siempre— pasa a poder describirse una vez.
--
-- Las reglas se evalúan en la cadena de handlers, delante del asistente: una
-- conversación derivada por regla llega a la persona correcta antes de que el
-- modelo gaste un turno contestando algo genérico.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0018. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo.

BEGIN;

CREATE TABLE IF NOT EXISTS automation_rules (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    -- Nulo = la regla rige en toda la empresa. Con departamento, solo en esa
    -- rama, igual que los documentos de la base de conocimiento.
    department_id  UUID REFERENCES departments(id) ON DELETE CASCADE,
    name           VARCHAR(120) NOT NULL,
    is_active      BOOLEAN NOT NULL DEFAULT true,
    -- Menor primero. Con el mismo valor, decide el orden de creación.
    priority       INTEGER NOT NULL DEFAULT 100,
    -- {"keywords": [...], "regex": "...", "channels": [...],
    --  "only_first_message": true, "outside_business_hours": false}
    conditions     JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- [{"action": "add_label", "label_id": "..."}, {"action": "stop"}, ...]
    actions        JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by     VARCHAR(160),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_automation_rules_tenant
    ON automation_rules (tenant_id, is_active, priority);

CREATE TABLE IF NOT EXISTS scheduled_messages (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    -- OutboundMessage serializado, el mismo formato que viaja en la cola de
    -- salida: al vencer se copia tal cual, sin traducir nada.
    payload         JSONB NOT NULL,
    send_at         TIMESTAMPTZ NOT NULL,
    -- pending | sent | cancelled
    status          VARCHAR(16) NOT NULL DEFAULT 'pending',
    created_by      VARCHAR(160),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ,
    CONSTRAINT ck_scheduled_status CHECK (status IN ('pending', 'sent', 'cancelled'))
);

-- El vaciado periódico busca lo pendiente ya vencido; el índice va por ahí.
CREATE INDEX IF NOT EXISTS ix_scheduled_due
    ON scheduled_messages (status, send_at);

COMMIT;
