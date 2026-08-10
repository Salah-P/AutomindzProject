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

1. Copy `.env.example` → `.env` and fill Supabase + Trigger.dev values.
2. Ensure the `jobs` table matches `supabase/schema.sql`.
3. Run API, Trigger.dev, and the website (see below).

### API

```bash
cd api
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Trigger.dev

```bash
cd trigger
npm install
npm run dev
```

### Website

```bash
cd web
python -m http.server 5173
```

Open http://127.0.0.1:5173 and submit a search query. Use **Force refresh** to trigger a scrape when rows already exist.

### Scraper only

```bash
python scraper/run.py "python" --limit 5

# scrape + upsert to Supabase (needs .env)
python scraper/persist_demo.py "python" --limit 5 --runs 2
```

## API surface

- `POST /v1/get-jobs` — body: `{ "search_query": string, "force_refresh"?: boolean, "limit"?: number }`
- `GET /v1/get-jobs?search_query=...` — poll cached rows (no trigger)
- `GET /health`

## License

Private / unpublished unless otherwise noted.
