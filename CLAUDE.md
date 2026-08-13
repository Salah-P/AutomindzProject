# Automindz — CLAUDE context

> Living map of the repo. Prefer README.md for full runbooks.

## Flow

```
Browser (public/) → FastAPI app.main (Vercel)
  → cache hit (Supabase, ≤ CACHE_TTL_HOURS) → return jobs
  → miss / stale / refresh → Trigger.dev scrape-jobs
       → python trigger/scraper/run.py → upsert jobs (unique job_url)
  → UI polls ?poll=true until ready

CV match:
  Upload PDF/DOCX → parse → Hugging Face LLM (profile + titles)
    → scrape/cache jobs per title → LLM score/rank → UI
```

Board: WeWorkRemotely programming RSS + detail pages.

## Layout

| Path | Role |
|------|------|
| `public/` | Static UI (served by FastAPI / Vercel) |
| `app/` | FastAPI (`app.main:app`) — cache, trigger, static routes |
| `scraper/` | Source scraper (`wwr_scraper.py`, `run.py`, optional `persist_demo.py`) |
| `trigger/` | Trigger.dev task + bundled copy `trigger/scraper/` for cloud |
| `trigger.config.ts` | Root Trigger config (GitHub App / production deploys) |
| `trigger/trigger.config.ts` | Nested config for `cd trigger && npm run dev` |
| `supabase/schema.sql` | `jobs` table (`UNIQUE(job_url)`) |

## Local

```bash
cp .env.example .env   # SUPABASE_*, TRIGGER_SECRET_KEY=tr_dev_…

# terminal A
cd trigger && npm install && npm run dev

# terminal B (repo root)
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Standalone scrape: `python scraper/run.py "python"`

## Deploy

- **Vercel:** FastAPI via `pyproject.toml` `[tool.vercel] entrypoint = "app.main:app"`; force `framework: fastapi` in `vercel.json` (root `package.json` is for Trigger only).
- **Trigger:** GitHub App uses root `trigger.config.ts` + `trigger.python.requirements.txt`. Task id: `scrape-jobs`. Use `tr_prod_…` on Vercel.

## Conventions

- Dedup key: `job_url`
- After editing `scraper/`, copy `run.py` + `wwr_scraper.py` into `trigger/scraper/` before relying on cloud
- Never put service-role keys in `public/`
