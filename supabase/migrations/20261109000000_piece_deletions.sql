BEGIN;
ALTER TABLE public.pdf_markers ADD COLUMN IF NOT EXISTS piece_source boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS public.piece_deletions (
    id uuid PRIMARY KEY,
    deleted_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.piece_deletions ENABLE ROW LEVEL SECURITY;
CREATE OR REPLACE FUNCTION public.skip_deleted_piece() RETURNS trigger
LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    IF EXISTS(SELECT 1 FROM public.piece_deletions WHERE id=NEW.id) THEN
        RETURN NULL;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS skip_deleted_piece ON public.saved_pieces;
CREATE TRIGGER skip_deleted_piece BEFORE INSERT OR UPDATE ON public.saved_pieces
FOR EACH ROW EXECUTE FUNCTION public.skip_deleted_piece();
COMMIT;
