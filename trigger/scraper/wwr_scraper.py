"""WeWorkRemotely scraper: RSS feed → detail pages → query filter."""

from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

RSS_URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
USER_AGENT = os.getenv(
    "SCRAPER_USER_AGENT",
    "AutomindzJobBot/0.1 (+https://automindz.local; job aggregator research)",
)
REQUEST_DELAY_SECONDS = float(os.getenv("SCRAPER_REQUEST_DELAY", "1.0"))

# Prefer the real listing body over full-page text (nav/ads mention "AI Job Search", etc.).
_DESCRIPTION_BLOCK = re.compile(
    r'class="[^"]*lis-container__job__content__description[^"]*"[^>]*>(.*?)</div>\s*'
    r'(?:<div|</section|</article)',
    re.IGNORECASE | re.DOTALL,
)


class _TextExtractor(HTMLParser):
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


def _fetch(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/html, */*",
        },
    )
    with urlopen(req, timeout=30) as resp:  # noqa: S310
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text().strip()


def _detail_description(html: str) -> str:
    """Extract job description text from a WWR detail page (not page chrome)."""
    match = _DESCRIPTION_BLOCK.search(html)
    if match:
        return _html_to_text(match.group(1))
    # Fallback: full visible text (last resort)
    return _html_to_text(html)


def _company_and_title(rss_title: str) -> tuple[str, str]:
    """WWR RSS titles are usually 'Company Name: Role Title'."""
    if ":" in rss_title:
        company, title = rss_title.split(":", 1)
        company, title = company.strip(), title.strip()
        if company and title:
            return company, title
    return "Unknown", rss_title.strip()


def matches_query(job: dict[str, Any], query: str) -> bool:
    """
    True if ``query`` appears as a whole word/phrase in the job title or description.

    Uses case-insensitive word-boundary matching so ``AI`` matches ``AI Engineer``
    and ``built with AI``, but not substrings inside ``Airtable``, ``training``, etc.
    """
    q = (query or "").strip()
    if not q:
        return True

    # Escape regex metacharacters; allow flexible whitespace between query words.
    pattern = re.compile(
        r"\b" + re.escape(q).replace(r"\ ", r"\s+") + r"\b",
        re.IGNORECASE,
    )
    haystack = "\n".join(
        [
            str(job.get("job_title") or ""),
            str(job.get("job_description") or ""),
        ]
    )
    return pattern.search(haystack) is not None


def _parse_rss(rss_xml: str) -> list[dict[str, str]]:
    root = ET.fromstring(rss_xml)
    items: list[dict[str, str]] = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        if not link or not title:
            continue
        company, job_title = _company_and_title(title)
        items.append(
            {
                "job_url": link,
                "job_title": job_title,
                "company_name": company,
                "rss_description": description,
                "rss_title": title,
            }
        )
    return items


def scrape_jobs(query: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    """
    Scrape WeWorkRemotely programming jobs matching ``query``.

    Fetches RSS + per-job detail pages, then keeps jobs where ``matches_query``
    finds the query as a whole word in the title or description.

    Returns dicts with: job_url, job_title, company_name, job_description.
    Does not write to the database.
    """
    rss_xml = _fetch(RSS_URL)
    time.sleep(REQUEST_DELAY_SECONDS)

    items = _parse_rss(rss_xml)
    if limit is not None:
        items = items[:limit]

    jobs: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        # Start from RSS blurb (HTML); replace with scoped detail description when possible.
        description = _html_to_text(item["rss_description"]) if item["rss_description"] else ""
        try:
            html = _fetch(item["job_url"])
            detail = _detail_description(html)
            if detail:
                description = detail
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            print(f"[warn] detail fetch failed for {item['job_url']}: {exc}", flush=True)

        job = {
            "job_url": item["job_url"],
            "job_title": item["job_title"],
            "company_name": item["company_name"],
            "job_description": description or item["job_title"],
        }
        if matches_query(job, query):
            jobs.append(job)

        if i < len(items) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)

    return jobs
