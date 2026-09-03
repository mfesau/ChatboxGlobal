-- Módulo Hotel: habitaciones y reservas, activable por departamento.
--
-- La Cooperativa es multinegocio: el departamento Hotel es una rama entre
-- varias, y el resto no debe verse afectado por estas tablas. Por eso todo
-- queda acotado por `department_id` además de `tenant_id`, y la activación
-- se guarda en `departments.enabled_modules` — vacío por omisión, así que
-- ningún departamento existente cambia de comportamiento al aplicar esto.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0012. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo,
-- seguido de la sección de PostgreSQL al final de este fichero (la
-- restricción de exclusión no se genera desde los modelos; ver el
-- comentario en `app/db/models.py` sobre el módulo Hotel).

BEGIN;

-- 1. Activación del módulo por departamento.
ALTER TABLE departments ADD COLUMN IF NOT EXISTS enabled_modules JSONB NOT NULL DEFAULT '{}'::jsonb;

-- 2. Categorías de habitación (Individual, Doble, Suite…).
CREATE TABLE IF NOT EXISTS hotel_room_types (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    department_id UUID NOT NULL,
    name VARCHAR(120) NOT NULL,
    description TEXT,
    capacity INTEGER NOT NULL DEFAULT 2,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_hotel_room_type_name UNIQUE (department_id, name),
    FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
    FOREIGN KEY (department_id) REFERENCES departments (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_hotel_room_types_department_id ON hotel_room_types (department_id);

-- 3. Habitaciones físicas.
CREATE TABLE IF NOT EXISTS hotel_rooms (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    department_id UUID NOT NULL,
    room_type_id UUID NOT NULL,
    code VARCHAR(20) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'available',
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_hotel_room_code UNIQUE (department_id, code),
    CONSTRAINT ck_hotel_room_status CHECK (status IN ('available', 'maintenance', 'out_of_service')),
    FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
    FOREIGN KEY (department_id) REFERENCES departments (id) ON DELETE CASCADE,
    FOREIGN KEY (room_type_id) REFERENCES hotel_room_types (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_hotel_rooms_department_id ON hotel_rooms (department_id);

-- 4. Tarifas por categoría y temporada. Fechas nulas = tarifa por omisión.
CREATE TABLE IF NOT EXISTS hotel_rate_plans (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    department_id UUID NOT NULL,
    room_type_id UUID NOT NULL,
    name VARCHAR(120) NOT NULL,
    starts_on DATE,
    ends_on DATE,
    nightly_price_cents INTEGER NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
    FOREIGN KEY (department_id) REFERENCES departments (id) ON DELETE CASCADE,
    FOREIGN KEY (room_type_id) REFERENCES hotel_room_types (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_hotel_rate_plans_department_id ON hotel_rate_plans (department_id);

-- 5. Reservas. El huésped se guarda también en texto plano porque quien se
--    aloja no siempre es el contacto que escribió por el canal.
CREATE TABLE IF NOT EXISTS hotel_reservations (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    department_id UUID NOT NULL,
    room_id UUID NOT NULL,
    contact_id UUID,
    conversation_id UUID,
    created_by_agent_id UUID,
    guest_name VARCHAR(160) NOT NULL,
    guest_phone VARCHAR(32),
    guest_email VARCHAR(254),
    check_in DATE NOT NULL,
    check_out DATE NOT NULL,
    guests INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    nightly_price_cents INTEGER,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_hotel_reservation_dates CHECK (check_out > check_in),
    CONSTRAINT ck_hotel_reservation_status
        CHECK (status IN ('pending', 'confirmed', 'checked_in', 'checked_out', 'cancelled', 'no_show')),
    FOREIGN KEY (tenant_id) REFERENCES tenants (id) ON DELETE CASCADE,
    FOREIGN KEY (department_id) REFERENCES departments (id) ON DELETE CASCADE,
    -- Sin ON DELETE: una habitación con reservas no se borra, se desactiva.
    FOREIGN KEY (room_id) REFERENCES hotel_rooms (id),
    FOREIGN KEY (contact_id) REFERENCES contacts (id) ON DELETE SET NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE SET NULL,
    FOREIGN KEY (created_by_agent_id) REFERENCES agents (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_hotel_reservations_department_id ON hotel_reservations (department_id);
CREATE INDEX IF NOT EXISTS ix_hotel_reservations_room_dates ON hotel_reservations (room_id, check_in, check_out);
CREATE INDEX IF NOT EXISTS ix_hotel_reservations_department_status ON hotel_reservations (department_id, status);

-- 6. Red de seguridad contra la doble reserva, a nivel de base de datos: aunque
--    el bot y un agente humano escriban a la vez, dos reservas activas no
--    pueden solaparse en la misma habitación. Las canceladas o "no show" no
--    cuentan, así que liberan la fecha para volver a reservarla.
--    `btree_gist` es necesaria porque GiST no admite `=` sobre UUID por sí solo.
CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE hotel_reservations DROP CONSTRAINT IF EXISTS ex_hotel_reservation_no_overlap;
ALTER TABLE hotel_reservations ADD CONSTRAINT ex_hotel_reservation_no_overlap
    EXCLUDE USING gist (
        room_id WITH =,
        daterange(check_in, check_out) WITH &&
    )
    WHERE (status NOT IN ('cancelled', 'no_show'));

COMMIT;
