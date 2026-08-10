"""
End-to-end: scrape → upsert → report counts.

Usage (from repo root, with .env set):
  python -m scraper.persist_demo "python" --limit 5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scraper"))
sys.path.insert(0, str(ROOT / "api"))

from supabase_client import count_jobs_by_query, get_jobs_by_query, upsert_jobs  # noqa: E402
from wwr_scraper import scrape_jobs  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape + upsert demo with dedup proof")
    parser.add_argument("query", help="Search / title filter query")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument(
        "--runs",
        type=int,
        default=2,
        help="How many scrape+upsert cycles (default 2 to prove dedup)",
    )
    args = parser.parse_args(argv)

    before = count_jobs_by_query(args.query)
    print(f"query={args.query!r}  rows_before={before}")

    for run in range(1, args.runs + 1):
        jobs = scrape_jobs(args.query, limit=args.limit)
        print(f"run {run}: scraped {len(jobs)} matching job(s)")
        if not jobs:
            print("No matching jobs in RSS — nothing to upsert.")
            break
        n = upsert_jobs(jobs, search_query=args.query)
        after = count_jobs_by_query(args.query)
        print(f"run {run}: upserted={n}  rows_after={after}")

    final = get_jobs_by_query(args.query)
    print(f"final_row_count={len(final)}")
    for j in final[:5]:
        print(f"  - {j['company_name']}: {j['job_title'][:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
