"""WeWorkRemotely scraper: RSS feed → title filter → detail-page descriptions."""

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


def _company_and_title(rss_title: str) -> tuple[str, str]:
    """WWR RSS titles are usually 'Company Name: Role Title'."""
    if ":" in rss_title:
        company, title = rss_title.split(":", 1)
        company, title = company.strip(), title.strip()
        if company and title:
            return company, title
    return "Unknown", rss_title.strip()


def _title_matches(query: str, title: str) -> bool:
    """Case-insensitive match: all query tokens must appear in the title."""
    tokens = [t for t in re.split(r"\s+", query.strip().lower()) if t]
    if not tokens:
        return True
    haystack = title.lower()
    return all(token in haystack for token in tokens)


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
    Scrape WeWorkRemotely programming jobs matching ``query`` in the RSS title.

    Returns dicts with: job_url, job_title, company_name, job_description.
    Does not write to the database.
    """
    rss_xml = _fetch(RSS_URL)
    time.sleep(REQUEST_DELAY_SECONDS)

    matched = [
        item for item in _parse_rss(rss_xml) if _title_matches(query, item["rss_title"])
    ]
    if limit is not None:
        matched = matched[:limit]

    jobs: list[dict[str, Any]] = []
    for i, item in enumerate(matched):
        description = item["rss_description"]
        try:
            html = _fetch(item["job_url"])
            detail = _html_to_text(html)
            if detail:
                description = detail
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            # Keep RSS blurb if the detail page fails
            print(f"[warn] detail fetch failed for {item['job_url']}: {exc}", flush=True)

        jobs.append(
            {
                "job_url": item["job_url"],
                "job_title": item["job_title"],
                "company_name": item["company_name"],
                "job_description": description or item["job_title"],
            }
        )
        if i < len(matched) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)

    return jobs
