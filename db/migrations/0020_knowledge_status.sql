-- Estado del documento de la base de conocimiento.
--
-- Trocear y vectorizar un PDF grande lleva su tiempo: se hacía dentro de la
-- propia petición, que se quedaba esperando y podía agotar el plazo. Ahora
-- ocurre en segundo plano, y esta columna es lo que permite enseñar en la
-- consola si el documento ya se puede consultar, si todavía se está
-- procesando, o si falló y por qué.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0019. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo.

BEGIN;

ALTER TABLE kb_documents
    ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'ready',
    ADD COLUMN IF NOT EXISTS error TEXT;

-- Los que ya estaban se procesaron dentro de la petición, así que están listos.
UPDATE kb_documents SET status = 'ready' WHERE status IS NULL;

COMMIT;
