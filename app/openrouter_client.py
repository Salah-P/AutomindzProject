"""LLM client for CV matching (Ollama local by default, optional Hugging Face)."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from openai import OpenAI

DEFAULT_OLLAMA_BASE = "http://127.0.0.1:11434/v1"
DEFAULT_OLLAMA_MODEL = "llama3:latest"
DEFAULT_HF_BASE = "https://router.huggingface.co/v1"
DEFAULT_HF_MODEL = "moonshotai/Kimi-K3:together"


class LlmError(RuntimeError):
    """Raised when the LLM call fails."""


# Back-compat for imports that still say OpenRouterError
OpenRouterError = LlmError


def _provider() -> str:
    return (os.getenv("LLM_PROVIDER") or "ollama").strip().lower()


def _api_key() -> str:
    provider = _provider()
    if provider == "ollama":
        # Ollama ignores the key but the OpenAI client requires a non-empty value.
        return os.getenv("OLLAMA_API_KEY", "ollama").strip() or "ollama"
    key = (
        os.getenv("HF_TOKEN", "").strip()
        or os.getenv("HUGGINGFACE_HUB_TOKEN", "").strip()
        or os.getenv("OPENROUTER_API_KEY", "").strip()
    )
    if not key:
        raise LlmError("HF_TOKEN is not set — add it to .env / Vercel env vars")
    return key


def _model() -> str:
    provider = _provider()
    if provider == "ollama":
        return (
            os.getenv("OLLAMA_MODEL", "").strip()
            or os.getenv("HF_MODEL", "").strip()
            or DEFAULT_OLLAMA_MODEL
        )
    return (
        os.getenv("HF_MODEL", "").strip()
        or os.getenv("OPENROUTER_MODEL", "").strip()
        or DEFAULT_HF_MODEL
    )


def _base_url() -> str:
    provider = _provider()
    if provider == "ollama":
        return (
            os.getenv("OLLAMA_BASE_URL", "").strip()
            or DEFAULT_OLLAMA_BASE
        )
    return (
        os.getenv("HF_BASE_URL", "").strip()
        or DEFAULT_HF_BASE
    )


def _client(*, timeout: float = 180.0) -> OpenAI:
    return OpenAI(
        base_url=_base_url(),
        api_key=_api_key(),
        timeout=timeout,
    )


def chat_json(
    *,
    system: str,
    user: str,
    temperature: float = 0.2,
    timeout: float = 180.0,
) -> Any:
    """
    Chat completion via configured provider; parse JSON from the reply.
    """
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            completion = _client(timeout=timeout).chat.completions.create(
                model=_model(),
                temperature=temperature if attempt == 0 else min(0.4, temperature + 0.2),
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
            last_err = LlmError(f"LLM chat failed ({_provider()}/{_model()}): {exc}")
            continue

        content = _message_text(completion)
        if not content.strip():
            last_err = LlmError("Empty model response")
            continue
        try:
            return _parse_json_content(content)
        except LlmError as exc:
            last_err = exc
            continue

    raise last_err or LlmError("LLM chat failed")


def _message_text(completion: Any) -> str:
    try:
        msg = completion.choices[0].message
    except (IndexError, AttributeError, TypeError) as exc:
        raise LlmError(f"Unexpected HF response shape: {completion}") from exc

    content = getattr(msg, "content", None)
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            else:
                text = getattr(part, "text", None)
                if text:
                    parts.append(str(text))
        joined = "\n".join(parts).strip()
        if joined:
            return joined
    # Some router models put text in refusal / reasoning-like extras
    for attr in ("refusal", "reasoning"):
        extra = getattr(msg, attr, None)
        if isinstance(extra, str) and extra.strip():
            return extra
    return ""


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
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LlmError(f"Model returned invalid JSON: {exc}") from exc


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
    try:
        result = chat_json(system=system, user=f"CV text:\n\n{clipped}")
    except LlmError:
        return _heuristic_profile_and_titles(cv_text)

    if not isinstance(result, dict):
        return _heuristic_profile_and_titles(cv_text)

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
        return _heuristic_profile_and_titles(cv_text)

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


def _heuristic_profile_and_titles(cv_text: str) -> dict[str, Any]:
    """Offline fallback when the LLM provider is unavailable / out of credits."""
    text = cv_text or ""
    lower = text.lower()
    skill_vocab = [
        "python",
        "django",
        "flask",
        "fastapi",
        "javascript",
        "typescript",
        "react",
        "node",
        "java",
        "sql",
        "mysql",
        "postgres",
        "aws",
        "docker",
        "kubernetes",
        "git",
        "linux",
        "api",
        "backend",
        "frontend",
    ]
    skills = [s for s in skill_vocab if s in lower]
    titles = []
    if "python" in skills:
        titles.extend(["python", "python developer", "backend developer"])
    if "django" in skills:
        titles.append("django")
    if "qa" in lower or "test" in lower:
        titles.append("qa engineer")
    if not titles:
        titles = ["python", "software engineer", "backend"]
    # dedupe preserve order
    clean: list[str] = []
    for t in titles:
        if t.lower() not in {x.lower() for x in clean}:
            clean.append(t)
    summary = " ".join(text.split())[:280] or "Candidate profile extracted from CV text."
    return {
        "profile": {
            "summary": summary,
            "roles": [],
            "seniority": "unknown",
            "skills": skills[:20],
            "industries": [],
            "locations": [],
        },
        "titles": clean[:6],
    }


def score_jobs(profile: dict[str, Any], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """LLM call 2: score each job; return ranked list with reasons."""
    if not jobs:
        return []

    indexed = jobs[:12]
    scored_by_id: dict[int, dict[str, Any]] = {}
    batch_size = 3

    slim_profile = {
        "summary": str(profile.get("summary") or "")[:500],
        "seniority": profile.get("seniority") or "",
        "skills": (profile.get("skills") or [])[:15],
        "roles": (profile.get("roles") or [])[:6],
    }

    for start in range(0, len(indexed), batch_size):
        batch = indexed[start : start + batch_size]
        compact = []
        for offset, j in enumerate(batch):
            compact.append(
                {
                    "id": start + offset,
                    "job_title": j.get("job_title"),
                    "company_name": j.get("company_name"),
                    "job_description": str(j.get("job_description") or "")[:500],
                }
            )
        system = (
            "Score candidate-job fit. Return ONLY compact JSON:\n"
            '{"matches":[{"id":0,"score":80,"reason":"short reason"}]}\n'
            "Include every id. score 0-100. reason <= 25 words."
        )
        user = json.dumps({"profile": slim_profile, "jobs": compact}, ensure_ascii=False)
        try:
            result = chat_json(system=system, user=user, temperature=0.1, timeout=60.0)
            raw = result.get("matches") if isinstance(result, dict) else None
            if not isinstance(raw, list):
                raise LlmError("bad matches")
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    idx = int(item.get("id"))
                except (TypeError, ValueError):
                    continue
                if idx < start or idx >= start + len(batch) or idx in scored_by_id:
                    continue
                try:
                    score = float(item.get("score", 0))
                except (TypeError, ValueError):
                    score = 0.0
                scored_by_id[idx] = {
                    "score": max(0.0, min(100.0, score)),
                    "reason": str(item.get("reason") or "").strip() or "No reason provided.",
                }
        except LlmError:
            for offset, job in enumerate(batch):
                idx = start + offset
                if idx not in scored_by_id:
                    scored_by_id[idx] = {
                        "score": _heuristic_score(profile, job),
                        "reason": (
                            "LLM scoring unavailable for this batch; "
                            "score estimated from skill overlap."
                        ),
                    }

    scored: list[dict[str, Any]] = []
    for i, job in enumerate(indexed):
        entry = scored_by_id.get(i) or {
            "score": _heuristic_score(profile, job),
            "reason": "Model omitted this listing; score estimated from skill overlap.",
        }
        scored.append(
            {
                "score": entry["score"],
                "reason": entry["reason"],
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


def _heuristic_score(profile: dict[str, Any], job: dict[str, Any]) -> float:
    skills = [str(s).lower() for s in (profile.get("skills") or []) if s]
    blob = f"{job.get('job_title') or ''} {job.get('job_description') or ''}".lower()
    if not skills:
        return 40.0
    hits = sum(1 for s in skills if len(s) > 2 and s in blob)
    return max(15.0, min(85.0, 25.0 + hits * 8.0))
