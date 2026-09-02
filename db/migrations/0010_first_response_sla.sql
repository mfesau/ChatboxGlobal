-- Objetivo de primera respuesta (SLA) por departamento.
--
-- El vencimiento se calcula en minutos HÁBILES del departamento: con el
-- horario configurado, un mensaje que entra a las 23:00 con una hora de
-- objetivo vence a las 10:00 del día siguiente, no a medianoche. Así el
-- número mide al equipo y no al reloj.
--
-- Solo cuenta la respuesta de una persona: la del asistente es inmediata y,
-- si contara, el objetivo se cumpliría siempre y la métrica no diría nada.
--
-- Un departamento sin objetivo no genera vencimientos, de modo que aplicar
-- esta migración no cambia por sí sola el comportamiento de nada.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0009. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo.

BEGIN;

-- 1. Objetivo del departamento, en minutos hábiles. Nulo = sin objetivo.
ALTER TABLE departments ADD COLUMN IF NOT EXISTS first_response_target_minutes INTEGER;

-- 2. Seguimiento por conversación.
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS first_response_due_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS first_response_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS sla_breached_at TIMESTAMP WITH TIME ZONE;

-- 3. El repaso periódico busca justo las que están a la espera y ya vencieron;
--    el índice parcial deja fuera a la enorme mayoría, que ya fue respondida.
CREATE INDEX IF NOT EXISTS ix_conversations_first_response_due
    ON conversations (first_response_due_at)
    WHERE first_response_at IS NULL AND sla_breached_at IS NULL;

COMMIT;
