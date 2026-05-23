"""
apply_schema.py
---------------
Applies the Supabase schema via the Management API (runs SQL directly).
"""
import urllib.request, urllib.error, json, sys

PROJECT_REF = "sfznxlueuafgavcextgn"
SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNmem54bHVldWFmZ2F2Y2V4dGduIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3OTUxMDg2NywiZXhwIjoyMDk1MDg2ODY3fQ.knQMbk8SFGo_8Y_tv0898p2gpSPrI1VkvVAS5QufEhA"

SQL = """
create extension if not exists vector;

create table if not exists articles (
    id bigint primary key generated always as identity,
    url text unique not null,
    title text,
    content text,
    category text,
    source text,
    created_at timestamp with time zone,
    embedding vector(1536),
    is_enriched boolean default false,
    sentiment text,
    summary_az text
);

create index if not exists articles_embedding_idx
    on articles using ivfflat (embedding vector_cosine_ops)
    with (lists = 100);

create or replace function search_news(
    query_embedding vector(1536),
    date_from timestamp with time zone default null,
    date_to timestamp with time zone default null,
    match_count int default 15
)
returns table (
    id bigint,
    title text,
    content text,
    url text,
    category text,
    source text,
    created_at timestamp with time zone,
    is_enriched boolean,
    sentiment text,
    summary_az text,
    similarity float
)
language plpgsql
as $$
begin
    return query
    select
        a.id, a.title, a.content, a.url, a.category, a.source,
        a.created_at, a.is_enriched, a.sentiment, a.summary_az,
        1 - (a.embedding <=> query_embedding) as similarity
    from articles a
    where
        (date_from is null or a.created_at >= date_from)
        and
        (date_to is null or a.created_at <= (date_to + interval '1 day' - interval '1 second'))
    order by a.embedding <=> query_embedding
    limit match_count;
end;
$$;
"""

url = f"https://sfznxlueuafgavcextgn.supabase.co/rest/v1/rpc/exec_sql"

# Use the pg endpoint via Management API
mgmt_url = f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query"

payload = json.dumps({"query": SQL}).encode()
req = urllib.request.Request(
    mgmt_url,
    data=payload,
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SERVICE_KEY}",
    },
    method="POST"
)
try:
    resp = urllib.request.urlopen(req)
    print("Schema applied OK:", resp.status)
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print("HTTP Error:", e.code, body)
    sys.exit(1)
