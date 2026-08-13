# Automindz Jobs

Search remote programming jobs from [WeWorkRemotely](https://weworkremotely.com), store them in Supabase, and serve them from a small FastAPI app + static UI.

A search hits the API; if results are not cached, the API kicks a **Trigger.dev** task. That task runs a **Python scraper** as a subprocess (RSS feed + per-job detail page), upserts into Supabase (deduped on `job_url`), and the UI shows results as they land.

**Live:** [https://automindz-jobs.vercel.app](https://automindz-jobs.vercel.app)

```bash
curl "https://automindz-jobs.vercel.app/health"
curl "https://automindz-jobs.vercel.app/v1/get-jobs?job_title=python"
# Force a fresh scrape (returns quickly with status=scraping; poll the same URL without refresh=true)
curl "https://automindz-jobs.vercel.app/v1/get-jobs?job_title=python&refresh=true"
```

---

## Architecture

```
┌──────────┐     GET /v1/get-jobs      ┌─────────────┐
│  Browser │ ─────────────────────────▶│   FastAPI   │  (Vercel)
│  (UI)    │ ◀──── jobs / scraping ────│  app/main   │
└──────────┘                           └──────┬──────┘
       │                                      │
       │ poll until ready                     │ trigger task
       │                                      ▼
       │                               ┌─────────────┐
       │                               │ Trigger.dev │  task: scrape-jobs
       │                               └──────┬──────┘
       │                                      │ subprocess
       │                                      ▼
       │                               ┌─────────────┐
       │                               │   Python    │  scraper/run.py
       │                               │   scraper   │  (RSS + detail pages)
       │                               └──────┬──────┘
       │                                      │ upsert (job_url unique)
       ▼                                      ▼
                              ┌──────────────────────────┐
                              │        Supabase          │
                              │   jobs table (Postgres)  │
                              └──────────────────────────┘
```

| Path | Role |
|------|------|
| `public/` | Static HTML/CSS/JS UI |
| `app/` | FastAPI gateway (`app.main:app`) — cache read, trigger scrape, serve UI |
| `trigger/` | Trigger.dev task `scrape-jobs` — runs the scraper, upserts to Supabase |
| `scraper/` | WeWorkRemotely scraper (`wwr_scraper.py`, CLI `run.py`) |
| `supabase/schema.sql` | `jobs` table (unique on `job_url`) |
| `trigger.config.ts` | Trigger.dev config at repo root (GitHub / prod deploys) |

**Request flow**

1. UI calls `GET /v1/get-jobs?job_title=…`.
2. If Supabase already has **fresh** rows for that query (within `CACHE_TTL_HOURS`, default 24h) → return them (`status: ready`, `cached: true`).
3. Else (miss, stale, or `refresh=true`) → trigger `scrape-jobs` on Trigger.dev and return `status: scraping`.
4. The task runs `python scraper/run.py "<job_title>"`, word-boundary-matches title + description, upserts rows (updates `scraped_at`).
5. UI polls `GET /v1/get-jobs?job_title=…&poll=true` until `status: ready` (poll never starts another scrape).

---

## Environment variables

Copy `.env.example` → `.env` at the repo root (used by FastAPI locally). Trigger.dev also needs Supabase vars in **its** dashboard / `trigger/.env` for deploys.

| Variable | Where | Purpose |
|----------|--------|---------|
| `SUPABASE_URL` | API + Trigger | Project URL |
| `SUPABASE_SECRET_KEY` | API + Trigger | Secret key (`sb_secret_…`; legacy `SUPABASE_SERVICE_ROLE_KEY` also accepted in code) |
| `TRIGGER_SECRET_KEY` | API | `tr_dev_…` locally; `tr_prod_…` on Vercel |
| `TRIGGER_SCRAPE_TASK_ID` | API | Task id (default `scrape-jobs`) |
| `SCRAPER_REQUEST_DELAY` | Scraper / Trigger | Seconds between detail fetches (e.g. `0.25`) |
| `SCRAPER_USER_AGENT` | Scraper / Trigger | Descriptive User-Agent for WWR requests |
| `WWR_RSS_URL` | Optional | Override RSS URL |
| `PYTHON_BIN` | Trigger (local) | `python` on Windows if `python3` is missing |
| `AUTOMINDZ_ROOT` | Trigger (local) | Monorepo root so the task can find `scraper/` |
| `CACHE_TTL_HOURS` | API | Re-scrape when cache for a query is older than this (default `24`) |
| `LLM_PROVIDER` | API | `openrouter` (default), `ollama`, or `hf` |
| `OPENROUTER_API_KEY` | API | OpenRouter key when provider is `openrouter` |
| `OPENROUTER_MODEL` | API | Default `openai/gpt-4o` |
| `LLM_MAX_TOKENS` | API | Cap completion size (default `1200`) |
| `OLLAMA_BASE_URL` | API | Default `http://127.0.0.1:11434/v1` |
| `OLLAMA_MODEL` | API | Default `llama3:latest` |
| `HF_TOKEN` | API | Hugging Face token when `LLM_PROVIDER=hf` |
| `MATCH_SCRAPE_WAIT_SECONDS` | API | Brief wait for scrapes during CV match (default `8`) |

### CV match flow

1. Upload PDF/DOCX via the UI (`POST /v1/match-cv`).
2. API extracts text → local **Ollama** (or HF) builds a profile + search titles.
3. For each title, jobs are loaded from Supabase (Trigger scrape kicked if cache miss/stale).
4. LLM scores each job and returns a ranked shortlist with reasons (score badge in UI).

Apply the latest `supabase/schema.sql` (adds `cv_uploads` + Storage bucket `cvs`) in the Supabase SQL editor before relying on upload persistence.

Vercel Production should use **`tr_prod_…`**. Local `.env` should stay on **`tr_dev_…`** so the API talks to `trigger.dev dev`, not cloud.

---

## Run locally

### 1. Prerequisites

- Python 3.12+
- Node 20+
- Supabase project with `supabase/schema.sql` applied
- Trigger.dev project (`proj_…` in `trigger/trigger.config.ts`)

```bash
cp .env.example .env
# fill SUPABASE_* and TRIGGER_SECRET_KEY=tr_dev_...
```

### 2. Trigger.dev worker (terminal A)

```bash
cd trigger
npm install
# trigger/.env: TRIGGER_SECRET_KEY=tr_dev_..., SUPABASE_*, AUTOMINDZ_ROOT, PYTHON_BIN=python
npm run dev
```

Leave this running. It executes `scrape-jobs` when the API triggers it.

### 3. FastAPI + UI (terminal B)

```bash
# from repo root
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

### 4. Scraper only (optional)

```bash
python scraper/run.py "python"
python scraper/run.py "AI" --limit 10   # limit = max RSS items to enrich before filtering
```

Prints a JSON array of `{ job_url, job_title, company_name, job_description }` to stdout.

---

## Deployed URL

| | |
|--|--|
| UI + API | https://automindz-jobs.vercel.app |
| Health | https://automindz-jobs.vercel.app/health |
| Jobs | https://automindz-jobs.vercel.app/v1/get-jobs?job_title=python |

```bash
curl -s "https://automindz-jobs.vercel.app/health"
curl -s "https://automindz-jobs.vercel.app/v1/get-jobs?job_title=python"
```

Background scrapes run on Trigger.dev cloud (`scrape-jobs`). Redeploy Trigger from a path **without spaces** in the folder name (Windows path limits), e.g. a copy of `trigger/`:

```bash
cd /path/without/spaces/trigger
npm install
npx trigger.dev@4.5.10 deploy --env-file .env
```

### Trigger.dev + GitHub

GitHub App auto-deploy expects config at the **repo root**. This repo has:

- `trigger.config.ts` (paths into `trigger/`)
- root `package.json` (so `npm install` works after clone)

That matches the failed Deploys you saw (“clone succeeds, then build fails”) — those happened when only `trigger/trigger.config.ts` existed.

Optional: `.github/workflows/deploy-trigger.yml` also deploys from root. Add repo secret `TRIGGER_ACCESS_TOKEN` (https://cloud.trigger.dev/account/tokens), plus `SUPABASE_URL` / `SUPABASE_SECRET_KEY` if you want env sync at build time. Use either the GitHub App **or** the Action to avoid double deploys.

---

## Design Decisions

### Why WeWorkRemotely’s RSS feed instead of raw HTML scraping?

WWR already publishes a structured programming-jobs feed. Using the RSS listing gives stable URLs, titles, and companies without reverse-engineering listing pages that change CSS and layout. We still fetch each job’s detail page for the full description—that content is what users read and what word-boundary matching needs—but discovery stays on a deliberate, bot-friendly interface. Fewer selectors to break, clearer rate-limiting with delays and a descriptive User-Agent, and a cleaner contract for the scraper.

### Why Trigger.dev + Python subprocess, not a separate scraper microservice?

Scraping is bursty, slow, and failure-prone; HTTP APIs should not do it inline. Trigger.dev gives us queues, retries, run history, and a cloud runner without standing up another always-on service, container fleet, or custom worker protocol. Keeping the scraper as plain Python (`run.py` → JSON on stdout) means the hard logic stays in one language and one file tree: Trigger is only the orchestrator. A subprocess boundary is enough isolation for this workload and avoids a second deployable, its own HTTP API, and duplicated env/config.

### Why a synchronous trigger-and-wait mindset instead of a fully async job API?

Callers should think “ask for jobs, get jobs (or a clear in-progress state)”—not “create job id, register webhook, correlate later.” Trigger-and-wait (or the same idea with a short fire + poll on the **same** GET endpoint) keeps orchestration inside one product surface: one route, one response shape (`ready` / `scraping`), errors in one place. We avoid inventing a second public job API and a status table just to watch scrapes. On Vercel Hobby the HTTP function cannot wait minutes, so production **triggers** the run and returns `scraping` while the UI polls that same endpoint until Supabase fills—same mental model, adapted to a 10s serverless ceiling.

### Why dedup lives in the database (`UNIQUE(job_url)`)?

The same listing can appear across searches, refreshes, and retries. Application-level “check then insert” races under concurrent runs. A unique constraint on `job_url` makes uniqueness a property of the data, not of whoever remembered to dedupe. Upserts become safe and idempotent: re-scrape refreshes title/description without duplicate rows. That is the right place for an invariant every writer (Trigger task, scripts, future boards) must obey.

---

## License

Private / unpublished unless otherwise noted.
