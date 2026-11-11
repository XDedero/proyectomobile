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
    role varchar(20) NOT NULL CHECK (role IN ('viewer','worker','administrator')),
    is_active boolean NOT NULL DEFAULT true,
    revision bigint NOT NULL DEFAULT 1
);
ALTER TABLE app_identity.accounts DROP CONSTRAINT IF EXISTS accounts_rut_key;
CREATE UNIQUE INDEX IF NOT EXISTS active_account_rut
    ON app_identity.accounts(rut) WHERE is_active;
CREATE TABLE IF NOT EXISTS app_identity.registration_requests (
    id uuid PRIMARY KEY,
    rut varchar(10) NOT NULL,
    first_name varchar(150) NOT NULL,
    last_name varchar(150) NOT NULL,
    password_hash varchar(256) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    status varchar(12) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    account_id uuid REFERENCES app_identity.accounts(id),
    reviewed_by varchar(150),
    reviewed_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS registration_pending_rut
    ON app_identity.registration_requests(rut) WHERE status='pending';

CREATE OR REPLACE FUNCTION app_identity.submit_registration(
    request_id uuid, request_rut text, given_name text, family_name text, credential text,
    submitted_at timestamptz
) RETURNS jsonb LANGUAGE plpgsql SET search_path=app_identity,pg_temp AS $$
DECLARE existing registration_requests;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(request_rut,0));
    SELECT * INTO existing FROM registration_requests WHERE id=request_id;
    IF FOUND THEN RETURN jsonb_build_object('status',existing.status); END IF;
    IF EXISTS(SELECT 1 FROM accounts WHERE rut=request_rut AND is_active) THEN
        RETURN jsonb_build_object('error','Ya existe un usuario con ese RUT.');
    END IF;
    IF EXISTS(SELECT 1 FROM registration_requests WHERE rut=request_rut AND status='pending') THEN
        RETURN jsonb_build_object('error','Ya hay una solicitud pendiente para ese RUT.');
    END IF;
    INSERT INTO registration_requests(id,rut,first_name,last_name,password_hash,created_at)
        VALUES(request_id,request_rut,given_name,family_name,credential,submitted_at);
    RETURN jsonb_build_object('status','pending');
END;
$$;

CREATE OR REPLACE FUNCTION app_identity.review_registration(
    request_id uuid, decision text, account_role text, reviewer text
) RETURNS jsonb LANGUAGE plpgsql SET search_path=app_identity,pg_temp AS $$
DECLARE item registration_requests;
DECLARE account_row accounts;
BEGIN
    IF decision NOT IN ('approve','reject') OR account_role NOT IN ('viewer','worker','administrator') THEN
        RAISE EXCEPTION 'Invalid registration decision';
    END IF;
    SELECT * INTO item FROM registration_requests WHERE id=request_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('error','La solicitud ya no existe.'); END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(item.rut,0));
    SELECT * INTO item FROM registration_requests WHERE id=request_id FOR UPDATE;
    IF item.status='pending' THEN
        IF decision='approve' THEN
            IF EXISTS(SELECT 1 FROM accounts WHERE rut=item.rut AND is_active) THEN
                RETURN jsonb_build_object('error','Ya existe un usuario con ese RUT.');
            END IF;
            INSERT INTO accounts(id,username,rut,first_name,last_name,password_hash,role)
                VALUES(item.id,'cuenta_'||replace(item.id::text,'-',''),item.rut,
                       item.first_name,item.last_name,item.password_hash,account_role);
        END IF;
        UPDATE registration_requests SET
            status=CASE WHEN decision='approve' THEN 'approved' ELSE 'rejected' END,
            account_id=CASE WHEN decision='approve' THEN item.id ELSE NULL END,
            password_hash='', reviewed_by=reviewer, reviewed_at=now()
            WHERE id=request_id RETURNING * INTO item;
    END IF;
    IF item.status <> (CASE WHEN decision='approve' THEN 'approved' ELSE 'rejected' END) THEN
        RETURN jsonb_build_object('error','Otro administrador ya resolvió la solicitud. Actualice la lista.');
    END IF;
    SELECT * INTO account_row FROM accounts WHERE id=item.account_id;
    RETURN jsonb_build_object('status',item.status,'account',
        CASE WHEN item.status='approved' THEN to_jsonb(account_row) ELSE NULL END);
END;
$$;

ALTER TABLE app_identity.accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_identity.registration_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA app_identity FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA app_identity FROM PUBLIC, anon, authenticated;
COMMIT;
