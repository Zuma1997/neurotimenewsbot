-- Enrichment config table — stores user-defined keywords for daily search
create table if not exists enrichment_config (
    id bigint primary key generated always as identity,
    keyword text unique not null,
    active boolean default true,
    created_at timestamp with time zone default now(),
    last_run_at timestamp with time zone
);

-- Insert default keywords
insert into enrichment_config (keyword, active) values
    ('AccessBank Azərbaycan', true),
    ('Mərkəzi Bank faiz', true),
    ('SOCAR neft', true),
    ('Azərbaycan iqtisadiyyat', true),
    ('maliyyə kredit bank', true)
on conflict (keyword) do nothing;
