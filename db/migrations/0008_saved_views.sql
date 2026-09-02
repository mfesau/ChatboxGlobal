-- Vistas guardadas: combinaciones de filtros de la bandeja, reutilizables.
--
-- Sustituyen al almacenamiento del navegador, que no viajaba de un equipo a
-- otro. ``owner_agent_id`` decide el alcance: con agente es personal y esa
-- persona la ve desde cualquier equipo; en nulo es del equipo y la ve todo
-- el inquilino.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0007. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo.

BEGIN;

CREATE TABLE IF NOT EXISTS saved_views (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    owner_agent_id UUID,
    name VARCHAR(40) NOT NULL,
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
    FOREIGN KEY (owner_agent_id) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_saved_views_tenant_owner
    ON saved_views (tenant_id, owner_agent_id);
CREATE INDEX IF NOT EXISTS ix_saved_views_created_at ON saved_views (created_at);

COMMIT;
