"""CV → titles → live jobs → ranked matches orchestration."""

from __future__ import annotations

import os
import time
from typing import Any

from cv_parser import CvParseError, extract_text
from job_cache import cache_is_fresh
from openrouter_client import build_profile_and_titles, score_jobs
from supabase_client import get_jobs_by_query, upload_cv
from trigger_client import TriggerError, trigger_scrape_jobs

SCRAPE_WAIT_SECONDS = float(
    os.getenv(
        "MATCH_SCRAPE_WAIT_SECONDS",
        "1" if os.getenv("VERCEL") else "8",
    )
)
MAX_TITLES = 5
MAX_JOBS_TO_SCORE = 20


def _collect_jobs_for_titles(titles: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Load cached jobs for each title; trigger scrapes when missing/stale."""
    run_ids: list[str] = []
    by_url: dict[str, dict[str, Any]] = {}

    for title in titles[:MAX_TITLES]:
        existing = get_jobs_by_query(title)
        fresh = cache_is_fresh(existing)
        if not existing or not fresh:
            try:
                run_ids.append(trigger_scrape_jobs(title))
            except TriggerError:
                pass
        for row in existing:
            url = str(row.get("job_url") or "")
            if url and url not in by_url:
                by_url[url] = row

    if run_ids and len(by_url) < 3:
        deadline = time.monotonic() + SCRAPE_WAIT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(1.5)
            for title in titles[:MAX_TITLES]:
                for row in get_jobs_by_query(title):
                    url = str(row.get("job_url") or "")
                    if url and url not in by_url:
                        by_url[url] = row
            if len(by_url) >= 5:
                break

    return list(by_url.values()), run_ids


def match_cv_bytes(
    *,
    filename: str,
    content: bytes,
    content_type: str,
) -> dict[str, Any]:
    """
    Full pipeline:
      1) parse CV
      2) store upload metadata (best-effort)
      3) LLM profile + search titles
      4) retrieve live/cached jobs via Trigger + Supabase
      5) LLM score + rank
    """
    if not content:
        raise CvParseError("Empty file")
    if len(content) > 5 * 1024 * 1024:
        raise CvParseError("File too large (max 5 MB)")

    text = extract_text(filename, content, content_type)

    upload_meta: dict[str, Any] | None = None
    try:
        upload_meta = upload_cv(
            filename=filename,
            content=content,
            content_type=content_type or "application/octet-stream",
        )
    except Exception:
        upload_meta = None

    built = build_profile_and_titles(text)
    profile = built["profile"]
    titles = built["titles"]

    jobs, run_ids = _collect_jobs_for_titles(titles)
    if not jobs:
        return {
            "status": "scraping",
            "message": (
                "Parsed your CV and started live scrapes for suggested titles. "
                "Wait ~30–90s then upload again (or search one of the titles) to score matches."
            ),
            "profile": profile,
            "titles": titles,
            "run_ids": run_ids,
            "cv_upload_id": (upload_meta or {}).get("id"),
            "matches": [],
        }

    jobs_sorted = sorted(
        jobs,
        key=lambda r: str(r.get("scraped_at") or ""),
        reverse=True,
    )[:MAX_JOBS_TO_SCORE]

    matches = score_jobs(profile, jobs_sorted)
    return {
        "status": "ready",
        "message": f"Ranked {len(matches)} job(s) from {len(titles)} search title(s).",
        "profile": profile,
        "titles": titles,
        "run_ids": run_ids,
        "cv_upload_id": (upload_meta or {}).get("id"),
        "matches": matches,
    }
