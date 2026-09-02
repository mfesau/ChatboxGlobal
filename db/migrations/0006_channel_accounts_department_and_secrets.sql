-- Cuentas de canal (ChannelAccount): departamento de destino automático y
-- credenciales propias, cifradas, para conectar tantos números de WhatsApp,
-- páginas de Facebook o equipos de Teams como se quiera.
--
-- Aplique este fichero sobre una base que ya tenga 0001-0005. En una
-- instalación nueva basta con 0001_init.sql regenerado desde el modelo.

BEGIN;

-- 1. A qué departamento cae, sin derivación manual, una conversación nueva de
--    esta cuenta. Nulo = cola común, igual que hoy (compatibilidad).
ALTER TABLE channel_accounts ADD COLUMN IF NOT EXISTS department_id UUID;
ALTER TABLE channel_accounts DROP CONSTRAINT IF EXISTS channel_accounts_department_id_fkey;
ALTER TABLE channel_accounts ADD CONSTRAINT channel_accounts_department_id_fkey
    FOREIGN KEY (department_id) REFERENCES departments (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_channel_accounts_department_id ON channel_accounts (department_id);

-- 2. Credenciales propias de la cuenta (p. ej. el token de una página de
--    Facebook), cifradas con app/core/secrets.py. Nulo = usa la credencial
--    global de .env (WhatsApp) o no hace falta ninguna (Teams).
ALTER TABLE channel_accounts ADD COLUMN IF NOT EXISTS credentials_ciphertext TEXT;

COMMIT;
