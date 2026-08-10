# Automindz — CLAUDE context

> Living map of the repo. Update commands and paths as we build.

## What this system does

End-to-end remote job scrape:

```
User → Website (web/) → FastAPI /v1/get-jobs (api/)
     → Trigger.dev task (trigger/) → Python scraper subprocess (scraper/)
     → Supabase jobs table → results flow back via FastAPI → Website
```

Job board today: **WeWorkRemotely** programming category RSS + per-job detail page for full description.

RSS: `https://weworkremotely.com/categories/remote-programming-jobs.rss`

## Where each concern lives

| Concern | Path | Notes |
|--------|------|--------|
| Website UI | `web/` | Static HTML/CSS/JS. Calls FastAPI. Polls while status is `scraping` / `refreshing`. |
| HTTP API | `api/` | FastAPI. `POST/GET /v1/get-jobs`. Reads Supabase; triggers scrape when cache miss or `force_refresh`. |
| Async scrape orchestration | `trigger/` | Trigger.dev v3 task `scrape-weworkremotely`. Spawns Python scraper, upserts to Supabase. |
| Scraper | `scraper/wwr_scraper.py` | `scrape_jobs(query)` — RSS → title filter → detail fetch. |
| Scraper CLI | `scraper/run.py` | Prints JSON array to stdout (Trigger.dev subprocess). |
| Supabase helpers | `api/supabase_client.py` | `upsert_jobs()` (dedup `job_url`), `get_jobs_by_query()`. |
| DB schema | `supabase/schema.sql` | Source of truth for `jobs` (dedup on `job_url`). |
| Env template | `.env.example` | Copy to repo-root `.env`. |

## Data model (`jobs`)

- `job_url` — unique dedup key
- `job_title`, `company_name`, `job_description`
- `search_query` — label for the run / user query (indexed)
- `scraped_at` — default `now()`

## Request flow (current)

1. Browser `GET /v1/get-jobs?job_title=...`
2. FastAPI `trigger_client.trigger_and_wait_scrape_jobs()` → Trigger.dev REST trigger + poll.
3. Local/cloud worker runs task `scrape-jobs` (`trigger/src/scrapeJobsTask.ts`).
4. Task spawns `python scraper/run.py "<job_title>"`, returns JSON jobs as the run output.
5. FastAPI upserts to Supabase and returns rows to the browser.

### Trigger.dev local

```bash
cd trigger
npm install
# ensure trigger/.env has TRIGGER_SECRET_KEY + AUTOMINDZ_ROOT
npm run dev
```

In another terminal:

```bash
cd api
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

### Trigger.dev cloud runner needs (for deploy)

In `trigger/trigger.config.ts` we already configure `@trigger.dev/python` `pythonExtension`:

- Installs Python in the build image
- Bundles `../scraper/**/*.py` into the deploy artifact
- Installs `../scraper/requirements.txt` (stdlib-only today)

Dashboard / deploy checklist:

1. Project ref `proj_dvfflvatqctdhlkzxcbx` in `trigger.config.ts`
2. Set env vars in Trigger.dev dashboard (prod/staging): none required for the scraper itself (stdlib + public RSS). Optional: `SCRAPER_USER_AGENT`, `SCRAPER_REQUEST_DELAY`
3. Run `cd trigger && npm run deploy`
4. Use a **prod** secret key (`tr_prod_…`) in the API’s `TRIGGER_SECRET_KEY` when pointing at deployed tasks

Local Windows note: worker uses `PYTHON_BIN=python` (see `trigger/.env`) because `python3` is often missing on Windows. Linux/macOS/cloud default to `python3`.

## Local run commands

Copy env first:

```bash
cp .env.example .env
# fill SUPABASE_*, TRIGGER_SECRET_KEY, etc.
```

### Supabase

Schema is already applied in your project (see `supabase/schema.sql` if you need to recreate).

No local Supabase CLI required for day-to-day work.

### Scraper (standalone)

```bash
# JSON to stdout (what Trigger.dev will call)
python scraper/run.py "python" --limit 3

# Scrape + upsert twice against Supabase (dedup proof)
# requires repo-root .env with SUPABASE_* and: pip install -r api/requirements.txt
python scraper/persist_demo.py "python" --limit 5 --runs 2
```

Scraper itself is stdlib-only (Python 3.11+). Persistence uses `supabase` from `api/requirements.txt`.

### FastAPI (+ web UI)

Serves the API and `web/` on one port:

```bash
cd api
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

- UI: `http://127.0.0.1:8000/`
- Jobs: `GET http://127.0.0.1:8000/v1/get-jobs?job_title=python`
- Health: `GET http://127.0.0.1:8000/health`

`GET /v1/get-jobs` scrapes WeWorkRemotely directly (no Trigger.dev yet), upserts to Supabase, and returns rows.

### Trigger.dev

```bash
cd trigger
npm install
# set TRIGGER project id in trigger.config.ts (project: "proj_...")
# ensure SUPABASE_* are available to the Trigger.dev runtime / .env
npm run dev
```

Task id: `scrape-weworkremotely` (must match `TRIGGER_SCRAPE_TASK_ID` in `.env`).

Requires `python` on PATH so the task can subprocess `scraper/wwr.py`.

### Website

Serve `web/` with any static server (examples):

```bash
# Python
cd web && python -m http.server 5173

# or VS Code / Cursor Live Preview / open index.html via a local static host
```

Open `http://127.0.0.1:5173`. API base defaults to `http://127.0.0.1:8000` (override in DevTools: `localStorage.setItem('API_BASE', '...')`).

Ensure `CORS_ORIGINS` in `.env` includes your web origin.

## Conventions

- Dedup / upsert key is always `job_url`.
- Scraper prints human summary by default; use `--json` for machine consumers (Trigger.dev).
- Prefer service-role Supabase key only in API + Trigger (never in `web/`).
- Keep board-specific scrape logic in `scraper/`; keep HTTP and caching policy in `api/`; keep orchestration in `trigger/`.

## Still to harden (as we build)

- [ ] Confirm Trigger.dev REST trigger URL/payload against the project’s dashboard / SDK version
- [ ] Richer WWR detail extraction (CSS-scoped description block vs full-page text)
- [ ] Auth / rate limits on `/v1/get-jobs`
- [ ] Webhook or realtime when scrape finishes (instead of poll-only)
- [ ] Additional job boards behind the same `jobs` schema
