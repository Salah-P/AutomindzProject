"""Supabase helpers for the jobs table."""

from __future__ import annotations

import os
import re
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


def search_jobs_text(term: str, *, limit: int = 40, client: Client | None = None) -> list[dict[str, Any]]:
    """
    Broader retrieval for CV matching: match term against title, description, or search_query.
    """
    q = (term or "").strip()
    if not q:
        return []
    safe = re.sub(r"[,%()]", " ", q)
    safe = " ".join(safe.split())
    if not safe:
        return []
    pattern = f"%{safe}%"
    sb = client or get_client()
    by_url: dict[str, dict[str, Any]] = {}
    for column in ("job_title", "job_description", "search_query"):
        result = (
            sb.table("jobs")
            .select("*")
            .ilike(column, pattern)
            .order("scraped_at", desc=True)
            .limit(limit)
            .execute()
        )
        for row in result.data or []:
            url = str(row.get("job_url") or "")
            if url:
                by_url[url] = row
    return list(by_url.values())[:limit]


def get_recent_jobs(*, limit: int = 40, client: Client | None = None) -> list[dict[str, Any]]:
    sb = client or get_client()
    result = (
        sb.table("jobs")
        .select("*")
        .order("scraped_at", desc=True)
        .limit(limit)
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


CV_BUCKET = os.getenv("SUPABASE_CV_BUCKET", "cvs")


def upload_cv(
    *,
    filename: str,
    content: bytes,
    content_type: str,
    client: Client | None = None,
) -> dict[str, Any]:
    """
    Store a CV in the ``cvs`` Storage bucket and insert a ``cv_uploads`` row.
    Returns the inserted metadata row.
    """
    import re
    import uuid

    if not content:
        raise ValueError("Empty file")

    safe_name = Path(filename).name.strip() or "cv"
    safe_name = re.sub(r"[^\w.\-]+", "_", safe_name)[:180]
    storage_path = f"{uuid.uuid4().hex}_{safe_name}"

    sb = client or get_client()
    sb.storage.from_(CV_BUCKET).upload(
        storage_path,
        content,
        file_options={
            "content-type": content_type or "application/octet-stream",
            "upsert": "false",
        },
    )

    row = {
        "original_filename": Path(filename).name.strip() or safe_name,
        "storage_path": storage_path,
        "content_type": content_type or "application/octet-stream",
        "size_bytes": len(content),
    }
    result = sb.table("cv_uploads").insert(row).execute()
    data = (result.data or [None])[0]
    if not data:
        raise RuntimeError("CV metadata insert returned no row")
    return data
