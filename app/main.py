"""FastAPI: Trigger.dev scrape → Supabase → web UI."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from supabase_client import get_jobs_by_query  # noqa: E402
from trigger_client import TriggerError, trigger_scrape_jobs  # noqa: E402

PUBLIC_DIR = ROOT / "public"
WEB_DIR = PUBLIC_DIR if PUBLIC_DIR.exists() else ROOT / "web"

# Soft cache: after this age, the next search re-triggers a scrape.
CACHE_TTL_HOURS = float(os.getenv("CACHE_TTL_HOURS", "24"))

app = FastAPI(title="Automindz Jobs API", version="0.4.2")
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
    status: str = "ready"  # ready | scraping
    cached: bool = False
    run_id: str | None = None
    message: str | None = None
    jobs: list[JobOut] = Field(default_factory=list)


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


def _parse_scraped_at(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Supabase may return "+00:00" or "Z".
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def cache_is_fresh(jobs: list[dict[str, Any]], *, ttl_hours: float = CACHE_TTL_HOURS) -> bool:
    """True when the newest row is newer than the TTL window."""
    if not jobs or ttl_hours <= 0:
        return False
    newest: datetime | None = None
    for row in jobs:
        scraped = _parse_scraped_at(row.get("scraped_at"))
        if scraped is None:
            continue
        if newest is None or scraped > newest:
            newest = scraped
    if newest is None:
        return False
    age = datetime.now(timezone.utc) - newest.astimezone(timezone.utc)
    return age <= timedelta(hours=ttl_hours)


@app.get("/health")
@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "env": os.getenv("VERCEL_ENV", "local")}


@app.get("/v1/get-jobs", response_model=GetJobsResponse)
@app.get("/api/v1/get-jobs", response_model=GetJobsResponse)
def get_jobs(
    job_title: str = Query(..., min_length=1, description="Title filter / search query"),
    refresh: bool = Query(
        False,
        description="If true, kick a new Trigger.dev scrape even when cache is fresh",
    ),
    poll: bool = Query(
        False,
        description="If true, only read Supabase — never start a scrape (used by the UI while waiting)",
    ),
) -> GetJobsResponse:
    """
    Return jobs for job_title.

    Fresh cache hit (within CACHE_TTL_HOURS): return immediately.
    Cache miss / stale / refresh: trigger Trigger.dev (task upserts to Supabase)
    and return status=scraping so the client can poll. Fits Vercel Hobby 10s limit.

    Use ``poll=true`` while waiting so each poll does not start another scrape.
    """
    query = job_title.strip()
    if not query:
        raise HTTPException(status_code=422, detail="job_title must not be empty")

    try:
        existing = get_jobs_by_query(query)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Database error: {exc}") from exc

    fresh = cache_is_fresh(existing)

    if poll:
        if existing and fresh:
            return GetJobsResponse(
                job_title=query,
                count=len(existing),
                status="ready",
                cached=True,
                jobs=[JobOut(**_normalize_job(row)) for row in existing],
            )
        return GetJobsResponse(
            job_title=query,
            count=len(existing),
            status="scraping",
            cached=bool(existing),
            message="Still waiting for scrape results.",
            jobs=[JobOut(**_normalize_job(row)) for row in existing],
        )

    if existing and fresh and not refresh:
        return GetJobsResponse(
            job_title=query,
            count=len(existing),
            status="ready",
            cached=True,
            jobs=[JobOut(**_normalize_job(row)) for row in existing],
        )

    try:
        run_id = trigger_scrape_jobs(query)
    except TriggerError as exc:
        raise HTTPException(status_code=502, detail=f"Scraper failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Scraper failed: {exc}") from exc

    stale = bool(existing) and not fresh
    message = "Scrape started. Poll this endpoint until status is ready."
    if stale:
        message = (
            f"Cache older than {CACHE_TTL_HOURS:g}h — refresh started. "
            "Poll until status is ready."
        )

    return GetJobsResponse(
        job_title=query,
        count=len(existing),
        status="scraping",
        cached=False,
        run_id=run_id,
        message=message,
        jobs=[JobOut(**_normalize_job(row)) for row in existing],
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/styles.css")
def styles() -> FileResponse:
    return FileResponse(WEB_DIR / "styles.css", media_type="text/css")


@app.get("/app.js")
def app_js() -> FileResponse:
    return FileResponse(WEB_DIR / "app.js", media_type="application/javascript")
