-- Traducciones de cada mensaje, para el equipo.
--
-- {"_source": "es", "de": "…", "en": null}: el idioma en que está escrito y
-- su traducción a cada idioma que se pidió (null = ya estaba en ese idioma).
-- Se guardan para no pagar dos veces la misma traducción cada vez que alguien
-- abre la conversación. En los mensajes que un agente envió traducidos, la
-- entrada de su idioma es lo que escribió él.
--
-- Idempotente: volver a aplicarlo no cambia nada.

BEGIN;

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS translations JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMIT;
