"""Shared cache freshness helpers for jobs rows."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

CACHE_TTL_HOURS = float(os.getenv("CACHE_TTL_HOURS", "24"))


def parse_scraped_at(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def cache_is_fresh(jobs: list[dict[str, Any]], *, ttl_hours: float | None = None) -> bool:
    """True when the newest row is newer than the TTL window."""
    ttl = CACHE_TTL_HOURS if ttl_hours is None else ttl_hours
    if not jobs or ttl <= 0:
        return False
    newest: datetime | None = None
    for row in jobs:
        scraped = parse_scraped_at(row.get("scraped_at"))
        if scraped is None:
            continue
        if newest is None or scraped > newest:
            newest = scraped
    if newest is None:
        return False
    age = datetime.now(timezone.utc) - newest.astimezone(timezone.utc)
    return age <= timedelta(hours=ttl)
