"""Hugging Face Inference (OpenAI-compatible) client for CV matching."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from openai import OpenAI

HF_BASE_URL = os.getenv("HF_BASE_URL", "https://router.huggingface.co/v1")
DEFAULT_MODEL = "moonshotai/Kimi-K3:together"


class LlmError(RuntimeError):
    """Raised when the Hugging Face / LLM call fails."""


# Back-compat for imports that still say OpenRouterError
OpenRouterError = LlmError


def _api_key() -> str:
    key = (
        os.getenv("HF_TOKEN", "").strip()
        or os.getenv("HUGGINGFACE_HUB_TOKEN", "").strip()
        or os.getenv("OPENROUTER_API_KEY", "").strip()
    )
    if not key:
        raise LlmError("HF_TOKEN is not set — add it to .env / Vercel env vars")
    return key


def _model() -> str:
    return (
        os.getenv("HF_MODEL", "").strip()
        or os.getenv("OPENROUTER_MODEL", "").strip()
        or DEFAULT_MODEL
    )


def _client(*, timeout: float = 90.0) -> OpenAI:
    return OpenAI(
        base_url=HF_BASE_URL,
        api_key=_api_key(),
        timeout=timeout,
    )


def chat_json(
    *,
    system: str,
    user: str,
    temperature: float = 0.2,
    timeout: float = 90.0,
) -> Any:
    """
    Chat completion via Hugging Face router; parse JSON from the reply.
    """
    try:
        completion = _client(timeout=timeout).chat.completions.create(
            model=_model(),
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": (
                        f"{user}\n\n"
                        "Respond with valid JSON only (no markdown fences)."
                    ),
                },
            ],
        )
    except Exception as exc:  # noqa: BLE001
        raise LlmError(f"Hugging Face chat failed: {exc}") from exc

    try:
        content = completion.choices[0].message.content or ""
    except (IndexError, AttributeError, TypeError) as exc:
        raise LlmError(f"Unexpected HF response shape: {completion}") from exc

    return _parse_json_content(content)


def _parse_json_content(content: str) -> Any:
    text = (content or "").strip()
    if not text:
        raise LlmError("Empty model response")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
        if not match:
            raise LlmError(f"Model did not return JSON: {text[:400]}")
        return json.loads(match.group(0))


def build_profile_and_titles(cv_text: str) -> dict[str, Any]:
    """LLM call 1: structured candidate profile + job-title search queries."""
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
        '  "titles": [string]\n'
        "}\n"
        "titles: 3-6 realistic job titles/search queries for a programming job board "
        '(e.g. "python backend", "platform engineer"), not only the current title.'
    )
    clipped = cv_text[:12000]
    result = chat_json(system=system, user=f"CV text:\n\n{clipped}")
    if not isinstance(result, dict):
        raise LlmError("Expected a JSON object for profile/titles")

    profile = result.get("profile") or {}
    titles = result.get("titles") or []
    if not isinstance(titles, list):
        titles = []
    clean_titles: list[str] = []
    for t in titles:
        s = str(t).strip()
        if s and s.lower() not in {x.lower() for x in clean_titles}:
            clean_titles.append(s)
    if not clean_titles:
        raise LlmError("Model returned no search titles")

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
    """LLM call 2: score each job; return ranked list with reasons."""
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
        "Score 0-100. Include every job_url provided. Reasons must be specific. "
        "Sort matches by score descending."
    )
    user = json.dumps({"profile": profile, "jobs": compact}, ensure_ascii=False)
    result = chat_json(system=system, user=user, temperature=0.1)
    if not isinstance(result, dict):
        raise LlmError("Expected a JSON object for matches")

    raw = result.get("matches") or []
    if not isinstance(raw, list):
        raise LlmError("matches must be a list")

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
