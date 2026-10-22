-- Cambio de contraseña obligatorio para la cuenta de arranque.
--
-- Hasta ahora el arranque recreaba `admin@local` con una contraseña fija y
-- documentada, y además la reponía en cada inicio aunque alguien la hubiese
-- cambiado. Ahora la contraseña se toma de BOOTSTRAP_ADMIN_PASSWORD o se
-- genera al azar y se imprime una sola vez en el log; esta marca obliga a
-- sustituirla antes de poder usar la consola.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0014. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo.

BEGIN;

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false;

-- La cuenta que venía con la contraseña fija documentada en el README queda
-- marcada: quien la siga usando tendrá que cambiarla en el primer acceso.
UPDATE agents SET must_change_password = true WHERE email = 'admin@local';

COMMIT;
