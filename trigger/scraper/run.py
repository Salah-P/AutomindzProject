"""CLI entrypoint for Trigger.dev: print scrape_jobs(query) as JSON to stdout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python scraper/run.py` from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))

from wwr_scraper import scrape_jobs  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape WeWorkRemotely jobs as JSON")
    parser.add_argument("query", help="Title filter query (all tokens must match)")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max matching jobs to enrich with detail pages",
    )
    args = parser.parse_args(argv)

    try:
        jobs = scrape_jobs(args.query, limit=args.limit)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    # Windows consoles often default to cp1252; force UTF-8 for JSON stdout.
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(json.dumps(jobs, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
