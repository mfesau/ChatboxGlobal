-- Estado «en proceso» para las conversaciones.
--
-- El equipo marca a mano en qué punto está cada una: pendiente (``open``),
-- en proceso (``in_progress``) o solucionada (``closed``). Antes solo existían
-- los dos extremos, y una conversación que alguien ya estaba resolviendo era
-- indistinguible de otra que nadie había mirado.
--
-- Los nombres de los dos extremos no cambian: renombrarlos obligaría a
-- reescribir todas las filas y todas las consultas, sin ganar nada.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0011. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo.

BEGIN;

ALTER TABLE conversations DROP CONSTRAINT IF EXISTS ck_conversation_status;
ALTER TABLE conversations ADD CONSTRAINT ck_conversation_status
    CHECK (status IN ('open', 'in_progress', 'snoozed', 'closed'));

COMMIT;
