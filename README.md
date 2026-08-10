# Automindz Jobs

Scrape WeWorkRemotely programming jobs (RSS + detail pages), store them in Supabase, and serve them through a small FastAPI + static website stack. Scrapes run as a Trigger.dev task that shells out to a Python scraper.

## Architecture

```
User → web/ → FastAPI (/v1/get-jobs) → Trigger.dev task
     → Python scraper (subprocess) → Supabase → API → Website
```

| Folder | Role |
|--------|------|
| `web/` | Static HTML/CSS/JS client |
| `api/` | FastAPI gateway |
| `trigger/` | Trigger.dev scrape task |
| `scraper/` | WeWorkRemotely RSS + detail scraper |
| `supabase/` | SQL schema for `jobs` |

See [CLAUDE.md](./CLAUDE.md) for a fuller map and local commands.

## Quick start

1. Copy `.env.example` → `.env` and set `SUPABASE_URL` + `SUPABASE_SECRET_KEY`.
2. Ensure the `jobs` table matches `supabase/schema.sql`.
3. Run the API (serves the website too):

```bash
cd api
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and search a job title (e.g. `python`).

### Scraper only

```bash
python scraper/run.py "python" --limit 5
```

## API surface

- `GET /v1/get-jobs?job_title=python` — scrape → upsert → return jobs
- `GET /health`
- `GET /` — web UI

## License

Private / unpublished unless otherwise noted.
