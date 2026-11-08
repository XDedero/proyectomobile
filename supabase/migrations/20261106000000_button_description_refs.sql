begin;

create table if not exists public.workbook_button_description_refs (
    workbook_id integer not null references public.workbook_documents(id) on delete cascade,
    button_index integer not null check (button_index >= 0),
    description_ref varchar(220) not null,
    primary key (workbook_id, button_index)
);

alter table public.workbook_button_description_refs enable row level security;
revoke all on public.workbook_button_description_refs from anon, authenticated;
grant select, insert, update, delete on public.workbook_button_description_refs to service_role;

commit;
