"""FastAPI: Trigger.dev scrape → Supabase → web UI + CV match."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cv_parser import CvParseError  # noqa: E402
from job_cache import CACHE_TTL_HOURS, cache_is_fresh  # noqa: E402
from match_service import match_cv_bytes  # noqa: E402
from openrouter_client import OpenRouterError  # noqa: E402
from supabase_client import get_jobs_by_query  # noqa: E402
from trigger_client import TriggerError, trigger_scrape_jobs  # noqa: E402

PUBLIC_DIR = ROOT / "public"
WEB_DIR = PUBLIC_DIR

app = FastAPI(title="Automindz Jobs API", version="0.5.0")
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


class CandidateProfile(BaseModel):
    summary: str = ""
    roles: list[str] = Field(default_factory=list)
    seniority: str = ""
    skills: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)


class RankedMatch(BaseModel):
    score: float
    reason: str
    job: JobOut


class MatchCvResponse(BaseModel):
    status: str
    message: str | None = None
    profile: CandidateProfile
    titles: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    cv_upload_id: str | None = None
    matches: list[RankedMatch] = Field(default_factory=list)


def _normalize_job(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "job_url": row["job_url"],
        "job_title": row["job_title"],
        "company_name": row["company_name"],
        "job_description": row["job_description"],
        "search_query": row.get("search_query") or "",
        "scraped_at": str(row.get("scraped_at") or ""),
    }


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
    """Return jobs for job_title (cache + Trigger scrape)."""
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


@app.post("/v1/match-cv", response_model=MatchCvResponse)
@app.post("/api/v1/match-cv", response_model=MatchCvResponse)
async def match_cv(file: UploadFile = File(...)) -> MatchCvResponse:
    """
    Drop in a CV → parse → LLM titles → live jobs → ranked shortlist with reasons.
    Requires OPENROUTER_API_KEY.
    """
    filename = file.filename or "cv.pdf"
    content_type = file.content_type or "application/octet-stream"
    content = await file.read()

    try:
        result = match_cv_bytes(
            filename=filename,
            content=content,
            content_type=content_type,
        )
    except CvParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OpenRouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Match failed: {exc}") from exc

    matches_out: list[RankedMatch] = []
    for m in result.get("matches") or []:
        job = m.get("job") or {}
        matches_out.append(
            RankedMatch(
                score=float(m.get("score") or 0),
                reason=str(m.get("reason") or ""),
                job=JobOut(**_normalize_job(job)),
            )
        )

    profile = result.get("profile") or {}
    return MatchCvResponse(
        status=str(result.get("status") or "ready"),
        message=result.get("message"),
        profile=CandidateProfile(**profile),
        titles=list(result.get("titles") or []),
        run_ids=list(result.get("run_ids") or []),
        cv_upload_id=str(result["cv_upload_id"]) if result.get("cv_upload_id") else None,
        matches=matches_out,
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
