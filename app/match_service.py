"""CV → titles → live jobs → ranked matches orchestration."""

from __future__ import annotations

import os
import re
import time
from typing import Any

from cv_parser import CvParseError, extract_text
from job_cache import cache_is_fresh
from openrouter_client import build_profile_and_titles, score_jobs
from supabase_client import (
    get_jobs_by_query,
    get_recent_jobs,
    search_jobs_text,
    upload_cv,
)
from trigger_client import TriggerError, trigger_scrape_jobs

SCRAPE_WAIT_SECONDS = float(
    os.getenv(
        "MATCH_SCRAPE_WAIT_SECONDS",
        "1" if os.getenv("VERCEL") else "12",
    )
)
MAX_TITLES = 5
MAX_JOBS_TO_SCORE = 12


def _seed_queries(titles: list[str], profile: dict[str, Any]) -> list[str]:
    """Build scrape/search seeds: full titles + key tokens + top skills."""
    seeds: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        s = " ".join((q or "").split()).strip()
        if not s:
            return
        key = s.lower()
        if key in seen:
            return
        seen.add(key)
        seeds.append(s)

    for title in titles[:MAX_TITLES]:
        add(title)
        # Prefer first meaningful token for WWR cache hits (e.g. "python").
        for tok in re.findall(r"[A-Za-z][A-Za-z+#.]{1,}", title):
            if tok.lower() in {"and", "the", "for", "with", "junior", "senior", "engineer"}:
                continue
            add(tok)
            break

    for skill in (profile.get("skills") or [])[:5]:
        add(str(skill))

    return seeds[:10]


def _collect_jobs_for_titles(
    titles: list[str],
    profile: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Load jobs for CV match:
    - exact search_query rows
    - fuzzy title/description matches
    - trigger scrapes for seed queries when cache is thin
    """
    run_ids: list[str] = []
    by_url: dict[str, dict[str, Any]] = {}
    seeds = _seed_queries(titles, profile)

    def absorb(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            url = str(row.get("job_url") or "")
            if url and url not in by_url:
                by_url[url] = row

    for seed in seeds:
        exact = get_jobs_by_query(seed)
        absorb(exact)
        absorb(search_jobs_text(seed, limit=25))
        if not exact or not cache_is_fresh(exact):
            try:
                run_ids.append(trigger_scrape_jobs(seed))
            except TriggerError:
                pass

    if len(by_url) < 5:
        absorb(get_recent_jobs(limit=30))

    if run_ids and len(by_url) < 5:
        deadline = time.monotonic() + SCRAPE_WAIT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(1.5)
            for seed in seeds[:MAX_TITLES]:
                absorb(get_jobs_by_query(seed))
                absorb(search_jobs_text(seed, limit=25))
            if len(by_url) >= 8:
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
      5) LLM score + rank (every job gets a score)
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

    jobs, run_ids = _collect_jobs_for_titles(titles, profile)
    if not jobs:
        return {
            "status": "scraping",
            "message": (
                "Parsed your CV and started live scrapes for suggested titles. "
                "Wait ~30–90s then upload again to score matches against jobs."
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
        "message": (
            f"Scored {len(matches)} job(s) against your CV "
            f"(from {len(titles)} suggested title(s))."
        ),
        "profile": profile,
        "titles": titles,
        "run_ids": run_ids,
        "cv_upload_id": (upload_meta or {}).get("id"),
        "matches": matches,
    }
