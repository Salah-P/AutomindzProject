"""OpenRouter chat client for CV matching (titles + scoring)."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(RuntimeError):
    """Raised when OpenRouter is misconfigured or returns an error."""


def _api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise OpenRouterError(
            "OPENROUTER_API_KEY is not set — add it to .env / Vercel env vars"
        )
    return key


def _model() -> str:
    return os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()


def chat_json(
    *,
    system: str,
    user: str,
    temperature: float = 0.2,
    timeout: float = 60.0,
) -> Any:
    """
    Call OpenRouter chat completions and parse a JSON object/array from the reply.
    """
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "https://automindz-jobs.vercel.app"),
        "X-Title": os.getenv("OPENROUTER_APP_TITLE", "Automindz Jobs"),
    }
    payload = {
        "model": _model(),
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(OPENROUTER_URL, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise OpenRouterError(
                f"OpenRouter error ({resp.status_code}): {resp.text[:800]}"
            )
        data = resp.json()

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenRouterError(f"Unexpected OpenRouter response shape: {data}") from exc

    return _parse_json_content(content)


def _parse_json_content(content: str) -> Any:
    text = (content or "").strip()
    if not text:
        raise OpenRouterError("Empty model response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
        if not match:
            raise OpenRouterError(f"Model did not return JSON: {text[:400]}")
        return json.loads(match.group(0))


def build_profile_and_titles(cv_text: str) -> dict[str, Any]:
    """
    LLM call 1: structured candidate profile + realistic job-title search queries.
    """
    system = (
        "You are a recruiting analyst. Given raw CV text, return ONLY a JSON object with:\n"
        "{\n"
        '  "profile": {\n'
        '    "summary": string,\n'
        '    "roles": [string],\n'
        '    "seniority": string,\n'
        '    "skills": [string],\n'
        '    "industries": [string],\n'
        '    "locations": [string]\n'
        "  },\n"
        '  "titles": [string]  // 3-6 realistic job titles to search for (not only current title)\n'
        "}\n"
        "Titles must be short search queries suitable for a job board (e.g. \"python backend\", "
        '"platform engineer"). Prefer programming / software roles when the CV fits.'
    )
    # Cap CV size to keep prompts bounded.
    clipped = cv_text[:12000]
    user = f"CV text:\n\n{clipped}"
    result = chat_json(system=system, user=user)
    if not isinstance(result, dict):
        raise OpenRouterError("Expected a JSON object for profile/titles")

    profile = result.get("profile") or {}
    titles = result.get("titles") or []
    if not isinstance(titles, list):
        titles = []
    clean_titles = []
    for t in titles:
        s = str(t).strip()
        if s and s.lower() not in {x.lower() for x in clean_titles}:
            clean_titles.append(s)
    if not clean_titles:
        raise OpenRouterError("Model returned no search titles")

    return {
        "profile": {
            "summary": str(profile.get("summary") or "").strip(),
            "roles": [str(x) for x in (profile.get("roles") or [])][:12],
            "seniority": str(profile.get("seniority") or "").strip(),
            "skills": [str(x) for x in (profile.get("skills") or [])][:30],
            "industries": [str(x) for x in (profile.get("industries") or [])][:12],
            "locations": [str(x) for x in (profile.get("locations") or [])][:12],
        },
        "titles": clean_titles[:6],
    }


def score_jobs(profile: dict[str, Any], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    LLM call 2: score each job; return ranked list with reasons.
    Each item: job_url, score (0-100), reason.
    """
    if not jobs:
        return []

    compact = []
    for j in jobs[:25]:
        compact.append(
            {
                "job_url": j.get("job_url"),
                "job_title": j.get("job_title"),
                "company_name": j.get("company_name"),
                "job_description": str(j.get("job_description") or "")[:1800],
            }
        )

    system = (
        "You are a hiring match scorer. Given a candidate profile and job listings, "
        "return ONLY JSON:\n"
        '{\n  "matches": [\n'
        '    {"job_url": string, "score": number, "reason": string}\n'
        "  ]\n}\n"
        "Score 0-100 (fit for THIS candidate). Include every job_url provided. "
        "Reasons must be specific (skills, seniority, domain) — not generic praise. "
        "Sort matches by score descending."
    )
    user = json.dumps({"profile": profile, "jobs": compact}, ensure_ascii=False)
    result = chat_json(system=system, user=user, temperature=0.1)
    if not isinstance(result, dict):
        raise OpenRouterError("Expected a JSON object for matches")

    raw = result.get("matches") or []
    if not isinstance(raw, list):
        raise OpenRouterError("matches must be a list")

    by_url = {str(j.get("job_url")): j for j in jobs}
    scored: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("job_url") or "").strip()
        if not url or url not in by_url or url in seen:
            continue
        seen.add(url)
        try:
            score = float(item.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(100.0, score))
        reason = str(item.get("reason") or "").strip() or "No reason provided."
        job = by_url[url]
        scored.append(
            {
                "score": score,
                "reason": reason,
                "job": {
                    "id": str(job.get("id") or ""),
                    "job_url": job.get("job_url"),
                    "job_title": job.get("job_title"),
                    "company_name": job.get("company_name"),
                    "job_description": job.get("job_description"),
                    "search_query": job.get("search_query"),
                    "scraped_at": str(job.get("scraped_at") or ""),
                },
            }
        )

    scored.sort(key=lambda m: m["score"], reverse=True)
    return scored
