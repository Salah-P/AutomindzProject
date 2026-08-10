-- Jobs scraped from WeWorkRemotely (and future boards).
-- Dedup key: job_url

create table if not exists jobs (
  id uuid primary key default gen_random_uuid(),
  job_url text not null unique,        -- dedup key
  job_title text not null,
  company_name text not null,
  job_description text not null,
  search_query text not null,
  scraped_at timestamptz not null default now()
);

create index if not exists idx_jobs_search_query on jobs (search_query);
