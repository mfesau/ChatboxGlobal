-- Macros: secuencias de acciones sobre una conversación, en un solo clic.
--
-- Etiquetar, dejar nota, responder con una plantilla, derivar y cerrar son
-- pasos que el equipo repite juntos muchas veces al día; una macro los guarda
-- en orden y los ejecuta de una vez.
--
-- Los pasos van en JSON porque son una lista ordenada y heterogénea que solo
-- se lee entera. Se ejecutan dentro de una única transacción: si uno falla,
-- no queda la conversación a medio procesar.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0010. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo.

BEGIN;

CREATE TABLE IF NOT EXISTS macros (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    name VARCHAR(80) NOT NULL,
    steps JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_macro_name UNIQUE (tenant_id, name),
    FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_macros_created_at ON macros (created_at);

COMMIT;
