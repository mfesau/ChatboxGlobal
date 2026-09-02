-- Respuestas guardadas y etiquetas: datos de referencia por inquilino, del
-- mismo tipo que los departamentos (ver 0005), pero sin nada que dependa de
-- ellos por clave foránea, así que no llevan "is_active": se borran sin más.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0006. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo.

BEGIN;

-- 1. Plantillas de texto con atajo, para insertar en el composer.
CREATE TABLE IF NOT EXISTS canned_responses (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    shortcode VARCHAR(40) NOT NULL,
    title VARCHAR(160) NOT NULL,
    body TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_canned_response_shortcode UNIQUE (tenant_id, shortcode),
    FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE
);

-- 2. Etiquetas del inquilino, reutilizables entre conversaciones.
CREATE TABLE IF NOT EXISTS labels (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    name VARCHAR(80) NOT NULL,
    color VARCHAR(20) NOT NULL DEFAULT '#6b7280',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_label_name UNIQUE (tenant_id, name),
    FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE
);

-- 3. Etiquetas aplicadas a cada conversación.
CREATE TABLE IF NOT EXISTS conversation_labels (
    conversation_id UUID NOT NULL,
    label_id UUID NOT NULL,
    PRIMARY KEY (conversation_id, label_id),
    FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE,
    FOREIGN KEY (label_id) REFERENCES labels (id) ON DELETE CASCADE
);

COMMIT;
