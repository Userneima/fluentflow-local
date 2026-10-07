"""Completed-job retention: only FluentFlow's own copy of an upload expires.

The rule, decided by the owner on 2026-10-07: nothing a task produced is ever
deleted automatically. The transcript, subtitles, notes, every frame and the
de-breathed audio or video stay until the user deletes the task. The user
manages their own original recordings, so retention does not reach into their
folders either.

What remains is FluentFlow's private copy of an uploaded file, kept in its data
directory so a task can be re-run. Deleting the original elsewhere does not
remove that copy, so it still expires after the source window.

Before this, a thirty-day window deleted whole tasks, notes included, without
notice: a hundred and forty-three tasks on the maintainer's machine.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from backend.core.retention_time import parse_job_time
from backend.core.storage_cleanup import cleanup_task_source_files

_FINISHED = {"completed", "failed", "cancelled"}


def enforce_history_retention(
    client_id: str | None,
    *,
    source_days: int,
    list_jobs: Callable[..., list[dict]],
    update_result: Callable[..., Any],
    load_job: Callable[..., dict[str, Any] | None] | None = None,
    **_retired: Any,
) -> dict[str, Any]:
    """Expire FluentFlow's copies of uploads; never touch a task's results.

    Keyword arguments from the retired whole-task rule (``keep_count``,
    ``artifact_days``, ``delete_jobs``) are accepted and ignored.
    """
    del source_days  # each task carries its own expiry, set when it finished
    if not client_id:
        return {"pruned_count": 0, "task_ids": [], "expired_source_count": 0}
    now = datetime.now(timezone.utc)
    expired = 0
    for job in list_jobs(client_id=client_id):
        task_id = str(job.get("task_id") or "")
        if not task_id or job.get("status") not in _FINISHED:
            continue
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        expires = parse_job_time(result.get("source_retention_expires_at"))
        if not (result.get("source_file_available") and expires and expires <= now):
            continue
        owner = job.get("client_id")
        full = _full_result(task_id, owner, result, load_job)
        if full is None:
            continue
        cleanup = cleanup_task_source_files(task_id, job.get("metadata"))
        update_result(task_id, {
            **full,
            "source_file_available": False,
            "source_file_storage": None,
            "source_retention_status": "expired",
            "source_retention_cleaned_at": cleanup.get("source_retention_cleaned_at"),
        }, client_id=owner, touch_updated_at=False)
        expired += 1
    return {"pruned_count": 0, "task_ids": [], "expired_source_count": expired}


def _full_result(
    task_id: str,
    client_id: str | None,
    result: dict[str, Any],
    load_job: Callable[..., dict[str, Any] | None] | None,
) -> dict[str, Any] | None:
    """The stored result to merge into. A retention summary is never written
    back as if it were the result: that would replace the transcript and note
    with a few bookkeeping fields."""
    if not result.get("_retention_summary"):
        return result
    if load_job is None:
        return None
    job = load_job(task_id, client_id=client_id) or {}
    full = job.get("result")
    return full if isinstance(full, dict) else None
