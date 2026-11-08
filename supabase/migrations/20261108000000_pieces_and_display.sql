BEGIN;
ALTER TABLE public.pdf_plans ADD COLUMN IF NOT EXISTS display_settings jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.pdf_markers ADD COLUMN IF NOT EXISTS area_shape text NOT NULL DEFAULT 'ellipse';
ALTER TABLE public.pdf_markers ADD COLUMN IF NOT EXISTS button_color text NOT NULL DEFAULT '';
ALTER TABLE public.pdf_markers ADD COLUMN IF NOT EXISTS show_description boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS public.saved_pieces (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    category_id uuid REFERENCES public.button_categories(id) ON DELETE SET NULL,
    category_name text NOT NULL,
    drive_file_id text NOT NULL,
    sheet_name text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(drive_file_id, sheet_name)
);
ALTER TABLE public.saved_pieces ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS public.piece_library_registry (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    drive_file_id text NOT NULL DEFAULT '',
    lease uuid,
    lease_until timestamptz
);
ALTER TABLE public.piece_library_registry ENABLE ROW LEVEL SECURITY;
COMMIT;
