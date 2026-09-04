-- Logo por departamento.
--
-- Solo administración lo cambia, desde la consola. Se guarda la ruta al
-- archivo en disco (bajo settings.uploads_dir), no una URL: la URL pública
-- la compone el servidor a partir del id del departamento (ver
-- GET /api/departments/{id}/logo en app/api/console.py), así que cambiar de
-- dominio o de esquema de rutas no obliga a reescribir filas.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0013. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo.

BEGIN;

ALTER TABLE departments ADD COLUMN IF NOT EXISTS logo_path VARCHAR(500);

COMMIT;
