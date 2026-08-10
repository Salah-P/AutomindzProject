"""FastAPI gateway: website ↔ Trigger.dev ↔ Supabase."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import Client, create_client

_root = Path(__file__).resolve().parents[1]
load_dotenv(_root / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

app = FastAPI(title="Automindz Jobs API", version="0.1.0")

_cors = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors if _cors != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class GetJobsRequest(BaseModel):
    search_query: str = Field(..., min_length=1, description="Query / label for this scrape run")
    limit: int | None = Field(None, ge=1, le=100, description="Optional max jobs for the scrape")
    force_refresh: bool = Field(
        False,
        description="If true, trigger a new scrape even when cached rows exist",
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
    status: str
    search_query: str
    run_id: str | None = None
    jobs: list[JobOut] = Field(default_factory=list)
    message: str | None = None


def get_supabase() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise HTTPException(
            status_code=500,
            detail="SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set",
        )
    return create_client(url, key)


def fetch_jobs_for_query(sb: Client, search_query: str) -> list[dict[str, Any]]:
    result = (
        sb.table("jobs")
        .select("*")
        .eq("search_query", search_query)
        .order("scraped_at", desc=True)
        .execute()
    )
    return list(result.data or [])


async def trigger_scrape(search_query: str, limit: int | None) -> str:
    """Kick off the Trigger.dev scrape task. Returns a run id when available."""
    secret = os.getenv("TRIGGER_SECRET_KEY")
    task_id = os.getenv("TRIGGER_SCRAPE_TASK_ID", "scrape-weworkremotely")
    if not secret:
        # Local-dev fallback: no Trigger.dev configured yet
        return f"local-{uuid4()}"

    # Trigger.dev REST trigger (v3). Adjust path if your project uses a custom endpoint.
    url = f"https://api.trigger.dev/api/v1/tasks/{task_id}/trigger"
    payload: dict[str, Any] = {"payload": {"search_query": search_query, "limit": limit}}
    headers = {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"Trigger.dev error ({resp.status_code}): {resp.text}",
            )
        data = resp.json()
        return str(data.get("id") or data.get("runId") or uuid4())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/get-jobs", response_model=GetJobsResponse)
async def get_jobs(body: GetJobsRequest) -> GetJobsResponse:
    """
    Return jobs for a search_query from Supabase.
    If none exist (or force_refresh), trigger a scrape via Trigger.dev.
    """
    sb = get_supabase()
    existing = fetch_jobs_for_query(sb, body.search_query)

    if existing and not body.force_refresh:
        return GetJobsResponse(
            status="ready",
            search_query=body.search_query,
            jobs=[JobOut(**_normalize_job(row)) for row in existing],
        )

    run_id = await trigger_scrape(body.search_query, body.limit)
    # After triggering, return whatever is already in DB (may be empty / stale on refresh).
    return GetJobsResponse(
        status="scraping" if not existing else "refreshing",
        search_query=body.search_query,
        run_id=run_id,
        jobs=[JobOut(**_normalize_job(row)) for row in existing],
        message="Scrape triggered. Poll GET /v1/get-jobs or POST again without force_refresh.",
    )


@app.get("/v1/get-jobs", response_model=GetJobsResponse)
def get_jobs_poll(
    search_query: str = Query(..., min_length=1),
) -> GetJobsResponse:
    """Poll cached jobs for a search_query (no trigger)."""
    sb = get_supabase()
    existing = fetch_jobs_for_query(sb, search_query)
    return GetJobsResponse(
        status="ready" if existing else "empty",
        search_query=search_query,
        jobs=[JobOut(**_normalize_job(row)) for row in existing],
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
