"""Trigger.dev client: trigger scrape-jobs (optionally wait)."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

TRIGGER_API_BASE = os.getenv("TRIGGER_API_BASE", "https://api.trigger.dev")
DEFAULT_TASK_ID = "scrape-jobs"
TERMINAL_STATUSES = frozenset(
    {
        "COMPLETED",
        "CANCELED",
        "FAILED",
        "CRASHED",
        "SYSTEM_FAILURE",
        "INTERRUPTED",
        "EXPIRED",
    }
)


class TriggerError(RuntimeError):
    """Raised when Trigger.dev trigger/wait fails."""


def _secret_key() -> str:
    key = os.getenv("TRIGGER_SECRET_KEY")
    if not key:
        raise TriggerError("TRIGGER_SECRET_KEY is not set")
    return key


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_secret_key()}",
        "Content-Type": "application/json",
    }


def trigger_scrape_jobs(job_title: str, *, task_id: str | None = None) -> str:
    """Trigger the scrape-jobs task. Returns the run id."""
    task = task_id or os.getenv("TRIGGER_SCRAPE_TASK_ID", DEFAULT_TASK_ID)
    url = f"{TRIGGER_API_BASE}/api/v1/tasks/{task}/trigger"
    payload = {"payload": {"job_title": job_title}}

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, headers=_headers(), json=payload)
        if resp.status_code >= 400:
            raise TriggerError(
                f"Failed to trigger task ({resp.status_code}): {resp.text}"
            )
        data = resp.json()
        run_id = data.get("id")
        if not run_id:
            raise TriggerError(f"Trigger response missing run id: {data}")
        return str(run_id)


def wait_for_run(
    run_id: str,
    *,
    timeout_seconds: float = 600.0,
    poll_interval_seconds: float = 1.0,
) -> dict[str, Any]:
    """Poll until the run reaches a terminal status. Returns the run JSON."""
    url = f"{TRIGGER_API_BASE}/api/v3/runs/{run_id}"
    deadline = time.monotonic() + timeout_seconds

    with httpx.Client(timeout=30.0) as client:
        while True:
            resp = client.get(url, headers=_headers())
            if resp.status_code >= 400:
                raise TriggerError(
                    f"Failed to retrieve run {run_id} ({resp.status_code}): {resp.text}"
                )
            run = resp.json()
            status = run.get("status")
            if status in TERMINAL_STATUSES:
                return run
            if time.monotonic() >= deadline:
                raise TriggerError(
                    f"Timed out waiting for run {run_id} (last status={status})"
                )
            time.sleep(poll_interval_seconds)


def trigger_and_wait_scrape_jobs(job_title: str) -> list[dict[str, Any]]:
    """
    Trigger scrape-jobs and wait for completion.

    Returns the jobs list from the task output (task also upserts to Supabase).
    """
    run_id = trigger_scrape_jobs(job_title)
    run = wait_for_run(run_id)
    status = run.get("status")

    if status != "COMPLETED":
        error = run.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else None
        raise TriggerError(
            f"Trigger run {run_id} ended with status={status}"
            + (f": {message}" if message else "")
        )

    output = run.get("output")
    if output is None:
        raise TriggerError(f"Run {run_id} completed but output is missing")

    # New task shape: { jobs, upserted }; keep compat with bare list.
    if isinstance(output, list):
        return output
    if isinstance(output, dict) and isinstance(output.get("jobs"), list):
        return output["jobs"]
    raise TriggerError(f"Run {run_id} output has unexpected shape: {type(output)}")
