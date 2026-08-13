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

-- Candidate CV uploads (files live in Storage bucket "cvs")
create table if not exists cv_uploads (
  id uuid primary key default gen_random_uuid(),
  original_filename text not null,
  storage_path text not null unique,
  content_type text not null,
  size_bytes integer not null check (size_bytes > 0),
  uploaded_at timestamptz not null default now()
);

-- Private bucket for CVs (service role bypasses RLS for API uploads)
insert into storage.buckets (id, name, public)
values ('cvs', 'cvs', false)
on conflict (id) do nothing;
