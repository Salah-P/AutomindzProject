"""FastAPI: Trigger.dev scrape → Supabase → web UI."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

sys.path.insert(0, str(ROOT / "api"))

from supabase_client import get_jobs_by_query, upsert_jobs  # noqa: E402
from trigger_client import TriggerError, trigger_and_wait_scrape_jobs  # noqa: E402

WEB_DIR = ROOT / "web"

app = FastAPI(title="Automindz Jobs API", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class JobOut(BaseModel):
    id: str
    job_url: str
    job_title: str
    company_name: str
    job_description: str
    search_query: str
    scraped_at: str


class GetJobsResponse(BaseModel):
    job_title: str
    count: int
    jobs: list[JobOut] = Field(default_factory=list)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/get-jobs", response_model=GetJobsResponse)
def get_jobs(
    job_title: str = Query(..., min_length=1, description="Title filter / search query"),
) -> GetJobsResponse:
    """Trigger Trigger.dev scrape-jobs, upsert to Supabase, return rows."""
    query = job_title.strip()
    if not query:
        raise HTTPException(status_code=422, detail="job_title must not be empty")

    try:
        scraped = trigger_and_wait_scrape_jobs(query)
    except TriggerError as exc:
        raise HTTPException(status_code=502, detail=f"Scraper failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Scraper failed: {exc}") from exc

    try:
        if scraped:
            upsert_jobs(scraped, search_query=query)
        rows = get_jobs_by_query(query)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Database error: {exc}") from exc

    return GetJobsResponse(
        job_title=query,
        count=len(rows),
        jobs=[JobOut(**_normalize_job(row)) for row in rows],
    )


def _normalize_job(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "job_url": row["job_url"],
        "job_title": row["job_title"],
        "company_name": row["company_name"],
        "job_description": row["job_description"],
        "search_query": row["search_query"],
        "scraped_at": str(row["scraped_at"]),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/styles.css")
def styles() -> FileResponse:
    return FileResponse(WEB_DIR / "styles.css", media_type="text/css")


@app.get("/app.js")
def app_js() -> FileResponse:
    return FileResponse(WEB_DIR / "app.js", media_type="application/javascript")
