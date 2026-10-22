-- El hotel cobra en guaraníes, no en dólares.
--
-- La moneda por omisión de tarifas y reservas era USD, y la consola, el
-- chatbox y el asistente mostraban «$» o «USD». Se cambia a PYG, que se
-- muestra «Gs. 350.000», sin decimales, como se escribe en Paraguay.
--
-- Las tarifas y reservas que ya existían pasan a PYG **sin convertir el
-- importe**: no hay un tipo de cambio que aplicar con fundamento, y los
-- importes guardados eran de prueba. Hay que revisarlos en la consola.
--
-- Idempotente: volver a aplicarlo no cambia nada.

BEGIN;

ALTER TABLE hotel_rate_plans ALTER COLUMN currency SET DEFAULT 'PYG';
ALTER TABLE hotel_reservations ALTER COLUMN currency SET DEFAULT 'PYG';

UPDATE hotel_rate_plans SET currency = 'PYG' WHERE currency = 'USD';
UPDATE hotel_reservations SET currency = 'PYG' WHERE currency = 'USD';

COMMIT;
