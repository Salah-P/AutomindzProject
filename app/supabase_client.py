"""Supabase helpers for the jobs table."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

_root = Path(__file__).resolve().parents[1]
load_dotenv(_root / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)


def get_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SECRET_KEY (or SUPABASE_SERVICE_ROLE_KEY) must be set"
        )
    return create_client(url, key)


def upsert_jobs(jobs: list[dict[str, Any]], search_query: str, *, client: Client | None = None) -> int:
    """
    Upsert scraped jobs. Dedup key is ``job_url`` (unique constraint).

    Each row gets ``search_query`` set to the scrape/query label.
    Returns the number of rows sent for upsert.
    """
    if not jobs:
        return 0

    sb = client or get_client()
    # Always bump scraped_at so cache TTL can see a fresh scrape.
    scraped_at = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "job_url": job["job_url"],
            "job_title": job["job_title"],
            "company_name": job["company_name"],
            "job_description": job["job_description"],
            "search_query": search_query,
            "scraped_at": scraped_at,
        }
        for job in jobs
    ]
    result = sb.table("jobs").upsert(rows, on_conflict="job_url").execute()
    return len(result.data or rows)


def get_jobs_by_query(search_query: str, *, client: Client | None = None) -> list[dict[str, Any]]:
    """Return all jobs stored for ``search_query``, newest first."""
    sb = client or get_client()
    result = (
        sb.table("jobs")
        .select("*")
        .eq("search_query", search_query)
        .order("scraped_at", desc=True)
        .execute()
    )
    return list(result.data or [])


def count_jobs_by_query(search_query: str, *, client: Client | None = None) -> int:
    sb = client or get_client()
    result = (
        sb.table("jobs")
        .select("id", count="exact")
        .eq("search_query", search_query)
        .execute()
    )
    if result.count is not None:
        return int(result.count)
    return len(result.data or [])
