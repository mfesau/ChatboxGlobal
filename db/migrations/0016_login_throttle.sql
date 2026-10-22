-- Freno a la prueba de contraseñas por fuerza bruta.
--
-- El login no llevaba cuenta de los intentos: una lista de contraseñas
-- comunes contra un correo conocido se podía recorrer entera. Estas dos
-- columnas registran los fallos seguidos y, pasado el tope, dejan la cuenta
-- en espera unos minutos. Un acceso correcto pone el contador a cero.
--
-- Se aplica igual a los agentes de la consola y a los clientes del chatbox:
-- las dos tablas tienen contraseña propia.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0015. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo.

BEGIN;

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS failed_login_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS failed_login_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;

COMMIT;
