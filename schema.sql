-- ============================================================================
-- ESTRUCTURA SQL MULTI-EMPRESA VINCULADA CON GOOGLE DRIVE
-- Sistema: Visor y Gestión de Planos (PDF / DXF) con Tablas (XLSX / CSV)
-- ============================================================================
-- Cuenta Google Drive vinculada: Vicente Sepúlveda (vicente.shent@gmail.com)
-- Carpetas Oficiales en Google Drive:
--   1. Carpeta "pdf"             -> ID: 1d7sf6i8zPsi52JVQJuaPu-Vv25GHHXoX
--   2. Carpeta "excel(xlsx-csv)" -> ID: 13sc_1Nt_LBWvKNlGPV42LLb6XP6Qd0-s
--
-- Características de esta arquitectura:
-- 1. Enrutamiento por Carpeta:
--    - Todo plano (.pdf) se registra apuntando a la carpeta "pdf".
--    - Toda tabla (.xlsx o .csv) se registra apuntando a "excel(xlsx-csv)".
-- 2. Asociación Automática (association_key):
--    - Al subir un plano PDF y una tabla XLSX/CSV con la misma clave de
--      proyecto (association_key), la base de datos los enlaza de manera
--      automática mediante Triggers sin intervención manual.
-- 3. Visualización sin Descarga (Streaming en Memoria):
--    - Se almacenan los IDs de Google Drive (drive_file_id) y URLs de
--      visualización en vivo (stream/preview). Los archivos se leen en memoria
--      desde Google Drive sin descargarlos al dispositivo, salvo que el
--      usuario solicite explícitamente la descarga.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. TABLA: EMPRESAS (Tenants y sus Carpetas de Google Drive)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    slug VARCHAR(100) NOT NULL UNIQUE,
    tax_id VARCHAR(50),
    contact_email VARCHAR(255),
    
    -- IDs de las carpetas en Google Drive donde se alojan sus archivos
    drive_pdf_folder_id VARCHAR(128) NOT NULL DEFAULT '1d7sf6i8zPsi52JVQJuaPu-Vv25GHHXoX',
    drive_excel_folder_id VARCHAR(128) NOT NULL DEFAULT '13sc_1Nt_LBWvKNlGPV42LLb6XP6Qd0-s',
    
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_companies_slug ON companies(slug);
CREATE INDEX IF NOT EXISTS idx_companies_active ON companies(is_active);

-- ----------------------------------------------------------------------------
-- 2. TABLA: USUARIOS (Autenticación Global)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(150) NOT NULL UNIQUE,
    email VARCHAR(254) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    first_name VARCHAR(150) DEFAULT '',
    last_name VARCHAR(150) DEFAULT '',
    is_superuser BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP NULL
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- ----------------------------------------------------------------------------
-- 3. TABLA: MEMBRESÍA Y ROLES EN EMPRESAS ('admin' vs 'normal')
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS company_users (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL DEFAULT 'normal',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT uq_company_user UNIQUE (company_id, user_id),
    CONSTRAINT chk_company_user_role CHECK (role IN ('admin', 'normal'))
);

CREATE INDEX IF NOT EXISTS idx_company_users_lookup ON company_users(company_id, user_id);
CREATE INDEX IF NOT EXISTS idx_company_users_role ON company_users(company_id, role);

-- ----------------------------------------------------------------------------
-- 4. TABLA: TABLAS EXCEL Y CSV EN GOOGLE DRIVE (workbook_documents)
--    Alojados en la carpeta Drive: "excel(xlsx-csv)" (13sc_1Nt_LBWvKNlGPV42LLb6XP6Qd0-s)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workbook_documents (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    
    -- Clave que asocia automáticamente este Excel/CSV con su Plano PDF y Proyecto
    association_key VARCHAR(120) NOT NULL,
    
    name VARCHAR(255) NOT NULL,
    file_format VARCHAR(10) NOT NULL DEFAULT 'xlsx',  -- 'xlsx' o 'csv'
    plan_mode VARCHAR(12) DEFAULT 'pdf',              -- 'pdf' o 'cad'
    
    -- Vinculación directa con la carpeta "excel(xlsx-csv)" de Google Drive
    drive_file_id VARCHAR(128) NOT NULL UNIQUE,
    drive_folder_id VARCHAR(128) NOT NULL DEFAULT '13sc_1Nt_LBWvKNlGPV42LLb6XP6Qd0-s',
    mime_type VARCHAR(150) NOT NULL DEFAULT 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    size_bytes BIGINT NOT NULL DEFAULT 0,
    
    -- URLs separadas: Lectura en memoria (sin descargar) vs Descarga explícita
    drive_stream_url VARCHAR(500) NOT NULL,           -- API stream para cargar en visor sin descargar
    drive_download_url VARCHAR(500) NULL,             -- Solo se usa si el usuario presiona "Descargar"
    
    -- Caché binario opcional (NULL por defecto porque vive en Google Drive)
    content BYTEA NULL,
    
    revision INTEGER NOT NULL DEFAULT 1,
    is_motor BOOLEAN NOT NULL DEFAULT FALSE,
    origin_uuid VARCHAR(36) NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,

    CONSTRAINT chk_workbook_format CHECK (file_format IN ('xlsx', 'csv'))
);

CREATE INDEX IF NOT EXISTS idx_workbooks_company ON workbook_documents(company_id);
CREATE INDEX IF NOT EXISTS idx_workbooks_assoc_key ON workbook_documents(company_id, association_key);
CREATE INDEX IF NOT EXISTS idx_workbooks_drive_file ON workbook_documents(drive_file_id);

-- ----------------------------------------------------------------------------
-- 5. TABLA: HOJAS DE LIBRO EXCEL / TABLAS CSV (plan_workbook_sheets)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plan_workbook_sheets (
    id SERIAL PRIMARY KEY,
    workbook_id INTEGER NOT NULL REFERENCES workbook_documents(id) ON DELETE CASCADE,
    name VARCHAR(31) NOT NULL,
    is_listing BOOLEAN NOT NULL DEFAULT FALSE,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    is_protected BOOLEAN NOT NULL DEFAULT FALSE,
    listing_order INTEGER NOT NULL DEFAULT 0,
    origin_uuid VARCHAR(36) NULL,
    created_by_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,

    CONSTRAINT uq_workbook_sheet UNIQUE (workbook_id, name)
);

CREATE INDEX IF NOT EXISTS idx_workbook_sheets ON plan_workbook_sheets(workbook_id, listing_order);

-- ----------------------------------------------------------------------------
-- 6. TABLA: PLANOS PDF EN GOOGLE DRIVE (pdf_plans)
--    Alojados en la carpeta Drive: "pdf" (1d7sf6i8zPsi52JVQJuaPu-Vv25GHHXoX)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pdf_plans (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    
    -- Clave que asocia automáticamente este PDF con su tabla XLSX/CSV en Drive
    association_key VARCHAR(120) NOT NULL,
    
    name VARCHAR(255) NOT NULL,
    
    -- Vinculación directa con la carpeta "pdf" de Google Drive
    drive_file_id VARCHAR(128) NOT NULL UNIQUE,
    drive_folder_id VARCHAR(128) NOT NULL DEFAULT '1d7sf6i8zPsi52JVQJuaPu-Vv25GHHXoX',
    size_bytes BIGINT NOT NULL DEFAULT 0,
    pages_data TEXT NOT NULL DEFAULT '[]',
    
    -- URLs separadas: Visualización sin descarga vs Descarga bajo demanda
    drive_stream_url VARCHAR(500) NOT NULL,           -- Stream directo al visor PDF.js en memoria
    drive_preview_url VARCHAR(500) NULL,              -- Vista embebida de Google Drive (/preview)
    drive_download_url VARCHAR(500) NULL,             -- Solo se usa si el usuario pide descargar el PDF
    
    imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    imported_by_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    
    -- Vínculos automáticos a la tabla XLSX/CSV de la carpeta "excel(xlsx-csv)"
    listing_id INTEGER NULL REFERENCES workbook_documents(id) ON DELETE SET NULL,
    workbook_id INTEGER NULL REFERENCES workbook_documents(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_pdf_plans_company ON pdf_plans(company_id);
CREATE INDEX IF NOT EXISTS idx_pdf_plans_assoc_key ON pdf_plans(company_id, association_key);
CREATE INDEX IF NOT EXISTS idx_pdf_plans_drive_file ON pdf_plans(drive_file_id);

-- ----------------------------------------------------------------------------
-- 7. TABLA: MARCADORES Y BOTONES EN PDF (pdf_markers)
--    Asocia cada punto (X, Y) del PDF en Drive con una hoja/fila del XLSX/CSV
--    y opcionalmente con un área de cobertura circular / elíptica.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pdf_markers (
    id SERIAL PRIMARY KEY,
    plan_id INTEGER NOT NULL REFERENCES pdf_plans(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    x DOUBLE PRECISION NOT NULL,
    y DOUBLE PRECISION NOT NULL,
    label VARCHAR(120) DEFAULT '',
    sheet_name VARCHAR(31) DEFAULT '',
    workbook_id INTEGER NULL REFERENCES workbook_documents(id) ON DELETE SET NULL,

    -- Parámetros OPCIONALES del Área de Cobertura (Circular / Elíptica)
    has_area BOOLEAN DEFAULT FALSE,                   -- Activado mediante casilla (checkbox)
    area_radius_x DOUBLE PRECISION NULL,             -- Radio horizontal (agrandar en ancho)
    area_radius_y DOUBLE PRECISION NULL,             -- Radio vertical (agrandar en alto)
    area_color VARCHAR(50) DEFAULT 'rgba(59, 130, 246, 0.25)' -- Color semitransparente
);

CREATE INDEX IF NOT EXISTS idx_pdf_markers_plan ON pdf_markers(plan_id, page);

-- ----------------------------------------------------------------------------
-- 8. TABLA: PLANOS CAD / DXF (cad_plans)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cad_plans (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    association_key VARCHAR(120) NOT NULL,
    name VARCHAR(255) NOT NULL,
    drive_file_id VARCHAR(128) UNIQUE,
    drive_folder_id VARCHAR(128) NOT NULL DEFAULT '1d7sf6i8zPsi52JVQJuaPu-Vv25GHHXoX',
    size_bytes BIGINT NOT NULL DEFAULT 0,
    svg TEXT NOT NULL,
    drive_stream_url VARCHAR(500) NULL,
    drive_download_url VARCHAR(500) NULL,
    imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    imported_by_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    listing_id INTEGER NULL REFERENCES workbook_documents(id) ON DELETE SET NULL,
    workbook_id INTEGER NULL REFERENCES workbook_documents(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_cad_plans_company ON cad_plans(company_id);
CREATE INDEX IF NOT EXISTS idx_cad_plans_assoc_key ON cad_plans(company_id, association_key);

-- ----------------------------------------------------------------------------
-- 9. TABLA: MOTORES DETECTADOS EN DXF (cad_motors)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cad_motors (
    id SERIAL PRIMARY KEY,
    plan_id INTEGER NOT NULL REFERENCES cad_plans(id) ON DELETE CASCADE,
    entity_key VARCHAR(2048) NOT NULL,
    name VARCHAR(255) NOT NULL,
    x DOUBLE PRECISION NOT NULL,
    y DOUBLE PRECISION NOT NULL,
    z DOUBLE PRECISION NOT NULL DEFAULT 0,
    svg_x DOUBLE PRECISION NOT NULL,
    svg_y DOUBLE PRECISION NOT NULL,
    svg_bounds TEXT NOT NULL DEFAULT '[]',
    sheet_name VARCHAR(31) DEFAULT '',
    workbook_id INTEGER NULL REFERENCES workbook_documents(id) ON DELETE SET NULL,

    -- Parámetros OPCIONALES del Área de Cobertura
    has_area BOOLEAN DEFAULT FALSE,
    area_radius_x DOUBLE PRECISION NULL,
    area_radius_y DOUBLE PRECISION NULL,
    area_color VARCHAR(50) DEFAULT 'rgba(59, 130, 246, 0.25)'
);

CREATE INDEX IF NOT EXISTS idx_cad_motors_plan ON cad_motors(plan_id);

-- ----------------------------------------------------------------------------
-- 10. TABLA: CATÁLOGO DE MOTORES GUARDADOS EN DRIVE (saved_motors)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS saved_motors (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    uuid VARCHAR(36) NOT NULL UNIQUE,
    name VARCHAR(120) NOT NULL,
    name_key VARCHAR(240) NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    data_fingerprint VARCHAR(64) DEFAULT '',
    workbook_id INTEGER NOT NULL UNIQUE REFERENCES workbook_documents(id) ON DELETE RESTRICT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,

    CONSTRAINT uq_company_motor_name UNIQUE (company_id, name_key),
    CONSTRAINT uq_company_motor_fingerprint UNIQUE (company_id, fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_saved_motors_company ON saved_motors(company_id);

-- ----------------------------------------------------------------------------
-- 11. TABLA: PROYECTOS DE PLANOS VINCULADOS A GOOGLE DRIVE (saved_projects)
--     Une automáticamente el PDF de la carpeta "pdf" con el XLSX/CSV de la
--     carpeta "excel(xlsx-csv)" para cargarlos en vivo sin descargarlos.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS saved_projects (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    
    -- Clave única de asociación que une el archivo de la carpeta 'pdf' con el de 'excel(xlsx-csv)'
    association_key VARCHAR(120) NOT NULL,
    
    name VARCHAR(255) NOT NULL,
    
    -- Referencias a las tablas de planos y libros
    pdf_plan_id INTEGER NULL UNIQUE REFERENCES pdf_plans(id) ON DELETE CASCADE,
    cad_plan_id INTEGER NULL UNIQUE REFERENCES cad_plans(id) ON DELETE CASCADE,
    workbook_id INTEGER NULL REFERENCES workbook_documents(id) ON DELETE SET NULL,
    
    -- IDs directos en las carpetas de Google Drive para carga instantánea en el visor
    pdf_drive_file_id VARCHAR(128) NULL,              -- Archivo en carpeta 'pdf' (1d7sf6i8zPsi52JVQJuaPu-Vv25GHHXoX)
    excel_drive_file_id VARCHAR(128) NULL,            -- Archivo en carpeta 'excel(xlsx-csv)' (13sc_1Nt_LBWvKNlGPV42LLb6XP6Qd0-s)
    
    saved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    saved_by_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,

    CONSTRAINT uq_company_project_assoc UNIQUE (company_id, association_key),
    CONSTRAINT chk_saved_project_one_plan CHECK (
        (cad_plan_id IS NOT NULL AND pdf_plan_id IS NULL) OR
        (cad_plan_id IS NULL AND pdf_plan_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_saved_projects_company ON saved_projects(company_id);
CREATE INDEX IF NOT EXISTS idx_saved_projects_assoc ON saved_projects(company_id, association_key);


-- ============================================================================
-- LÓGICA DE ASOCIACIÓN AUTOMÁTICA EN BASE DE DATOS (TRIGGERS POSTGRESQL/SUPABASE)
-- ============================================================================
-- Cuando se inserta un plano PDF en 'pdf_plans' o una tabla en 'workbook_documents'
-- que comparten la misma 'association_key', se enlazan automáticamente y se
-- crea/actualiza el registro del proyecto en 'saved_projects'.
-- ============================================================================

CREATE OR REPLACE FUNCTION fn_auto_associate_drive_files()
RETURNS TRIGGER AS $$
DECLARE
    v_workbook_id INTEGER;
    v_excel_drive_id VARCHAR(128);
    v_pdf_id INTEGER;
    v_pdf_drive_id VARCHAR(128);
    v_pdf_name VARCHAR(255);
BEGIN
    -- Caso 1: Se acaba de subir un Plano PDF a la carpeta 'pdf'
    IF TG_TABLE_NAME = 'pdf_plans' THEN
        -- Buscar si ya se subió su tabla XLSX/CSV con la misma association_key
        SELECT id, drive_file_id INTO v_workbook_id, v_excel_drive_id
        FROM workbook_documents
        WHERE company_id = NEW.company_id
          AND association_key = NEW.association_key
        ORDER BY updated_at DESC
        LIMIT 1;

        IF v_workbook_id IS NOT NULL THEN
            NEW.workbook_id := COALESCE(NEW.workbook_id, v_workbook_id);
            NEW.listing_id  := COALESCE(NEW.listing_id, v_workbook_id);
        END IF;

        RETURN NEW;
    END IF;

    -- Caso 2: Se acaba de subir una Tabla XLSX o CSV a la carpeta 'excel(xlsx-csv)'
    IF TG_TABLE_NAME = 'workbook_documents' THEN
        -- Vincular automáticamente con el Plano PDF que tenga la misma association_key
        UPDATE pdf_plans
        SET workbook_id = NEW.id,
            listing_id  = COALESCE(listing_id, NEW.id)
        WHERE company_id = NEW.company_id
          AND association_key = NEW.association_key
          AND workbook_id IS NULL
        RETURNING id, drive_file_id, name INTO v_pdf_id, v_pdf_drive_id, v_pdf_name;

        -- Actualizar también el proyecto guardado si existe
        UPDATE saved_projects
        SET workbook_id = NEW.id,
            excel_drive_file_id = NEW.drive_file_id,
            saved_at = CURRENT_TIMESTAMP
        WHERE company_id = NEW.company_id
          AND association_key = NEW.association_key;

        RETURN NEW;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pdf_plans_auto_link ON pdf_plans;
CREATE TRIGGER trg_pdf_plans_auto_link
BEFORE INSERT OR UPDATE ON pdf_plans
FOR EACH ROW
EXECUTE FUNCTION fn_auto_associate_drive_files();

DROP TRIGGER IF EXISTS trg_workbooks_auto_link ON workbook_documents;
CREATE TRIGGER trg_workbooks_auto_link
AFTER INSERT OR UPDATE ON workbook_documents
FOR EACH ROW
EXECUTE FUNCTION fn_auto_associate_drive_files();


-- Trigger para registrar automáticamente el proyecto en saved_projects al subir o renombrar un PDF
CREATE OR REPLACE FUNCTION fn_auto_register_saved_project()
RETURNS TRIGGER AS $$
DECLARE
    v_excel_drive_id VARCHAR(128);
BEGIN
    IF NEW.workbook_id IS NOT NULL THEN
        SELECT drive_file_id INTO v_excel_drive_id
        FROM workbook_documents
        WHERE id = NEW.workbook_id;
    END IF;

    -- Si ya existe un proyecto para este pdf_plan_id (ej. al renombrar el PDF), actualizarlo
    UPDATE saved_projects
    SET association_key = NEW.association_key,
        name = NEW.name,
        pdf_drive_file_id = NEW.drive_file_id,
        workbook_id = COALESCE(NEW.workbook_id, workbook_id),
        excel_drive_file_id = COALESCE(v_excel_drive_id, excel_drive_file_id),
        saved_at = CURRENT_TIMESTAMP
    WHERE pdf_plan_id = NEW.id;

    IF NOT FOUND THEN
        INSERT INTO saved_projects (
            company_id, association_key, name, pdf_plan_id, workbook_id,
            pdf_drive_file_id, excel_drive_file_id, saved_by_id
        )
        VALUES (
            NEW.company_id, NEW.association_key, NEW.name, NEW.id, NEW.workbook_id,
            NEW.drive_file_id, v_excel_drive_id, NEW.imported_by_id
        )
        ON CONFLICT (company_id, association_key) DO UPDATE
        SET pdf_plan_id = EXCLUDED.pdf_plan_id,
            name = EXCLUDED.name,
            pdf_drive_file_id = EXCLUDED.pdf_drive_file_id,
            workbook_id = COALESCE(EXCLUDED.workbook_id, saved_projects.workbook_id),
            excel_drive_file_id = COALESCE(EXCLUDED.excel_drive_file_id, saved_projects.excel_drive_file_id),
            saved_at = CURRENT_TIMESTAMP;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_pdf_create_saved_project ON pdf_plans;
CREATE TRIGGER trg_pdf_create_saved_project
AFTER INSERT OR UPDATE ON pdf_plans
FOR EACH ROW
EXECUTE FUNCTION fn_auto_register_saved_project();


-- ============================================================================
-- VISTA DE CARGA AUTOMÁTICA DESDE GOOGLE DRIVE (SIN DESCARGAR)
-- ============================================================================
-- Esta vista entrega todo lo necesario para que el frontend cargue un proyecto
-- en vivo haciendo streaming desde las dos carpetas de Google Drive.
-- Se define con security_invoker = true para respetar las políticas RLS.
-- ============================================================================
CREATE OR REPLACE VIEW v_drive_projects_live
WITH (security_invoker = true) AS
SELECT 
    sp.id AS project_id,
    sp.company_id,
    c.name AS company_name,
    sp.association_key,
    sp.name AS project_name,
    
    -- Datos del PDF en la carpeta "pdf" (1d7sf6i8zPsi52JVQJuaPu-Vv25GHHXoX)
    p.id AS pdf_plan_id,
    p.name AS pdf_file_name,
    p.drive_file_id AS pdf_drive_id,
    p.drive_folder_id AS pdf_folder_id,
    p.drive_stream_url AS pdf_live_stream_url,        -- Usar para renderizar en visor SIN descargar
    p.drive_preview_url AS pdf_embed_preview_url,     -- Usar para iframe preview SIN descargar
    p.drive_download_url AS pdf_explicit_download_url,-- Usar SOLO si el usuario pulsa "Descargar PDF"
    p.pages_data,
    
    -- Datos del XLSX/CSV en la carpeta "excel(xlsx-csv)" (13sc_1Nt_LBWvKNlGPV42LLb6XP6Qd0-s)
    w.id AS workbook_id,
    w.name AS excel_file_name,
    w.file_format AS excel_format,                    -- 'xlsx' o 'csv'
    w.drive_file_id AS excel_drive_id,
    w.drive_folder_id AS excel_folder_id,
    w.drive_stream_url AS excel_live_stream_url,      -- Usar para leer tabla en RAM SIN descargar
    w.drive_download_url AS excel_explicit_download_url, -- Usar SOLO si el usuario pulsa "Descargar Excel/CSV"
    
    sp.saved_at
FROM saved_projects sp
INNER JOIN companies c ON c.id = sp.company_id
LEFT JOIN pdf_plans p ON p.id = sp.pdf_plan_id
LEFT JOIN workbook_documents w ON w.id = COALESCE(sp.workbook_id, p.workbook_id);


-- ============================================================================
-- DATOS SEMILLA INICIALES (EMPRESAS Y USUARIOS CON LAS CARPETAS DE DRIVE)
-- ============================================================================
INSERT INTO companies (name, slug, tax_id, contact_email, drive_pdf_folder_id, drive_excel_folder_id)
VALUES 
    ('Empresa Minera Norte S.A.', 'minera-norte', '76.123.456-7', 'contacto@mineranorte.cl', '1d7sf6i8zPsi52JVQJuaPu-Vv25GHHXoX', '13sc_1Nt_LBWvKNlGPV42LLb6XP6Qd0-s'),
    ('Constructora Central SpA', 'constructora-central', '77.890.123-4', 'soporte@constructora.cl', '1d7sf6i8zPsi52JVQJuaPu-Vv25GHHXoX', '13sc_1Nt_LBWvKNlGPV42LLb6XP6Qd0-s')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO users (username, email, password_hash, first_name, last_name, is_superuser)
VALUES 
    ('vicente_admin', 'vicente.shent@gmail.com', 'pbkdf2_sha256$870000$drive_linked_admin', 'Vicente', 'Sepúlveda', TRUE),
    ('admin_minera', 'admin@mineranorte.cl', 'pbkdf2_sha256$870000$mock_hash_admin1', 'Carlos', 'Admin Minera', FALSE),
    ('operador_minera', 'operador@mineranorte.cl', 'pbkdf2_sha256$870000$mock_hash_user1', 'Juan', 'Técnico Minera', FALSE)
ON CONFLICT (username) DO NOTHING;

INSERT INTO company_users (company_id, user_id, role)
SELECT c.id, u.id, 'admin'
FROM companies c, users u
WHERE c.slug = 'minera-norte' AND u.username IN ('vicente_admin', 'admin_minera')
ON CONFLICT (company_id, user_id) DO NOTHING;

INSERT INTO company_users (company_id, user_id, role)
SELECT c.id, u.id, 'normal'
FROM companies c, users u
WHERE c.slug = 'minera-norte' AND u.username = 'operador_minera'
ON CONFLICT (company_id, user_id) DO NOTHING;
