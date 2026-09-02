-- Horario de atención por departamento.
--
-- Fuera de horario el asistente no contesta: se avisa una vez al cliente y el
-- hilo queda en la cola para el día siguiente. Un departamento sin horario
-- configurado atiende siempre, de modo que aplicar esta migración no cambia
-- por sí sola el comportamiento de nada.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0008. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo.

BEGIN;

-- 1. Tramos de atención por día de la semana (1 = lunes … 7 = domingo).
--    Vacío = sin horario, se atiende a toda hora.
ALTER TABLE departments ADD COLUMN IF NOT EXISTS business_hours JSONB NOT NULL DEFAULT '{}'::jsonb;

-- 2. Zona horaria (IANA) con la que se interpreta ese horario. Nulo = UTC.
ALTER TABLE departments ADD COLUMN IF NOT EXISTS timezone VARCHAR(64);

-- 3. Aviso al cliente que escribe fuera de horario. Nulo = no se avisa.
ALTER TABLE departments ADD COLUMN IF NOT EXISTS out_of_hours_message TEXT;

COMMIT;
