"""WeWorkRemotely scraper: RSS listing + detail-page description fetch."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_RSS_URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
DEFAULT_UA = "AutomindzJobBot/0.1 (+local-dev)"


@dataclass
class JobDraft:
    job_url: str
    job_title: str
    company_name: str
    job_description: str
    search_query: str


class _TextExtractor(HTMLParser):
    """Minimal HTML → visible text extractor for job detail pages."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        if tag in {"script", "style", "noscript"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            text = data.strip()
            if text:
                self._chunks.append(text)

    def text(self) -> str:
        return "\n".join(self._chunks)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def fetch_url(url: str, user_agent: str) -> str:
    req = Request(url, headers={"User-Agent": user_agent, "Accept": "*/*"})
    with urlopen(req, timeout=30) as resp:  # noqa: S310 — intentional outbound HTTP
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def parse_rss_items(rss_xml: str) -> list[dict[str, str]]:
    root = ET.fromstring(rss_xml)
    items: list[dict[str, str]] = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        if not link:
            continue
        company = _company_from_title(title)
        items.append(
            {
                "job_url": link,
                "job_title": title,
                "company_name": company,
                "rss_description": description,
            }
        )
    return items


def _company_from_title(title: str) -> str:
    # WWR titles are typically "Company Name: Job Title"
    if ":" in title:
        return title.split(":", 1)[0].strip() or "Unknown"
    return "Unknown"


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text().strip()


def enrich_with_detail(
    items: Iterable[dict[str, str]],
    *,
    search_query: str,
    user_agent: str,
    delay: float,
    limit: int | None,
) -> list[JobDraft]:
    jobs: list[JobDraft] = []
    for i, item in enumerate(items):
        if limit is not None and i >= limit:
            break
        detail_text = ""
        try:
            html = fetch_url(item["job_url"], user_agent)
            detail_text = html_to_text(html)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            print(f"[warn] detail fetch failed for {item['job_url']}: {exc}", file=sys.stderr)

        description = detail_text or item.get("rss_description") or item["job_title"]
        jobs.append(
            JobDraft(
                job_url=item["job_url"],
                job_title=item["job_title"],
                company_name=item["company_name"],
                job_description=description,
                search_query=search_query,
            )
        )
        if delay > 0:
            time.sleep(delay)
    return jobs


def scrape(
    *,
    search_query: str,
    rss_url: str | None = None,
    limit: int | None = None,
) -> list[JobDraft]:
    rss_url = rss_url or os.getenv("WWR_RSS_URL", DEFAULT_RSS_URL)
    user_agent = os.getenv("SCRAPER_USER_AGENT", DEFAULT_UA)
    delay = _env_float("SCRAPER_REQUEST_DELAY", 1.0)

    rss_xml = fetch_url(rss_url, user_agent)
    items = parse_rss_items(rss_xml)
    return enrich_with_detail(
        items,
        search_query=search_query,
        user_agent=user_agent,
        delay=delay,
        limit=limit,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape WeWorkRemotely programming jobs")
    parser.add_argument(
        "--search-query",
        required=True,
        help="Label stored on each row in jobs.search_query",
    )
    parser.add_argument("--rss-url", default=None, help="Override WWR RSS URL")
    parser.add_argument("--limit", type=int, default=None, help="Max jobs to enrich")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON array of jobs to stdout (for Trigger.dev subprocess)",
    )
    args = parser.parse_args(argv)

    try:
        jobs = scrape(
            search_query=args.search_query,
            rss_url=args.rss_url,
            limit=args.limit,
        )
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"[error] scrape failed: {exc}", file=sys.stderr)
        return 1

    payload = [asdict(j) for j in jobs]
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Scraped {len(payload)} jobs for query={args.search_query!r}")
        for j in payload[:5]:
            print(f"  - {j['company_name']}: {j['job_title'][:80]}")
        if len(payload) > 5:
            print(f"  ... and {len(payload) - 5} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
