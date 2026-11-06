begin;

create table if not exists public.button_categories (
    id uuid primary key,
    name varchar(80) not null check (length(btrim(name)) > 0)
);

create unique index if not exists unique_button_category_name on public.button_categories (lower(name));

alter table public.button_categories enable row level security;
revoke all on public.button_categories from anon, authenticated;
grant select on public.button_categories to authenticated;
grant select, insert, update, delete on public.button_categories to service_role;
drop policy if exists read_button_categories on public.button_categories;
create policy read_button_categories on public.button_categories
    for select to authenticated using (true);

insert into public.button_categories (id, name) values
    ('643ac8a7-0c92-4275-ab07-145cce6869e0', 'Motores'),
    ('f8e1774e-4bed-4195-b8d6-3841b82e095f', 'Controladores')
on conflict do nothing;

alter table public.pdf_markers add column if not exists category_id uuid
    references public.button_categories (id) on delete set null;
alter table public.cad_motors add column if not exists category_id uuid
    references public.button_categories (id) on delete set null;
create index if not exists idx_pdf_markers_category on public.pdf_markers (category_id);
create index if not exists idx_cad_motors_category on public.cad_motors (category_id);

commit;
