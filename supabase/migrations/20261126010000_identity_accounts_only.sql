BEGIN;
CREATE SCHEMA IF NOT EXISTS app_identity;
REVOKE ALL ON SCHEMA app_identity FROM PUBLIC, anon, authenticated;

CREATE TABLE IF NOT EXISTS app_identity.accounts (
    id uuid PRIMARY KEY,
    username varchar(150) NOT NULL UNIQUE,
    rut varchar(10) NOT NULL,
    first_name varchar(150) NOT NULL,
    last_name varchar(150) NOT NULL,
    password_hash varchar(256) NOT NULL,
    role varchar(20) NOT NULL CHECK (role IN ('viewer', 'worker', 'administrator')),
    is_active boolean NOT NULL DEFAULT true,
    revision bigint NOT NULL DEFAULT 1
);
ALTER TABLE app_identity.accounts DROP CONSTRAINT IF EXISTS accounts_rut_key;
CREATE UNIQUE INDEX IF NOT EXISTS active_account_rut ON app_identity.accounts(rut) WHERE is_active;
ALTER TABLE app_identity.accounts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON app_identity.accounts FROM PUBLIC, anon, authenticated;

DROP FUNCTION IF EXISTS app_identity.submit_registration(uuid, text, text, text, text, timestamp with time zone);
DROP FUNCTION IF EXISTS app_identity.review_registration(uuid, text, text, text);
COMMIT;
