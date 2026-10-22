-- Respuestas guardadas, etiquetas y macros pueden ser de un departamento.
--
-- Hasta aquí eran de toda la empresa: el equipo de Informática veía al
-- escribir «/» las plantillas del Hotel, y podía etiquetar una avería con
-- «Check-in pendiente». Ahora cada una es de toda la empresa (nulo, como
-- todas las que ya existían) o de un departamento, y solo la ven y la usan
-- quienes atienden ese departamento.
--
-- Al borrar un departamento se van con él: dejarlas huérfanas las convertiría
-- en «de toda la empresa» y las vería todo el mundo.
--
-- Idempotente: volver a aplicarlo no cambia nada.

BEGIN;

ALTER TABLE canned_responses
    ADD COLUMN IF NOT EXISTS department_id UUID REFERENCES departments(id) ON DELETE CASCADE;
ALTER TABLE labels
    ADD COLUMN IF NOT EXISTS department_id UUID REFERENCES departments(id) ON DELETE CASCADE;
ALTER TABLE macros
    ADD COLUMN IF NOT EXISTS department_id UUID REFERENCES departments(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS ix_canned_responses_department ON canned_responses (department_id);
CREATE INDEX IF NOT EXISTS ix_labels_department ON labels (department_id);
CREATE INDEX IF NOT EXISTS ix_macros_department ON macros (department_id);

COMMIT;
