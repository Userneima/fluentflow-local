"""Completed-job retention: media expires, the work does not.

The rule, decided 2026-10-07: a task's transcript, subtitles, notes and the
frames those notes show are kept until the user deletes the task. Only what is
large and can be produced again expires — the source copy after the source
window, the audio, video and unused frames after the artifact window. The task
then says what was cleared and when, instead of disappearing from the list.

Before this, the artifact window deleted whole tasks, notes included, without
notice. A hundred and forty-three tasks went that way on the maintainer's
machine, most of them with a transcript and many with a note that had cost a
model call.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from backend.core.retention_time import parse_job_time
from backend.core.storage_cleanup import cleanup_task_media_files, cleanup_task_source_files

_FRAME_NAME = re.compile(r"[A-Za-z0-9_\-]+\.(?:jpe?g|png|webp)", re.IGNORECASE)
_IMAGE_TARGET = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")
_FINISHED = {"completed", "failed", "cancelled"}


def frames_shown_in_notes(result: dict[str, Any]) -> set[str]:
    """File names of frames that any stored note of this task embeds."""
    texts: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            # Only text that embeds an image counts. The records of frames
            # sent to the model name every candidate, and keeping those would
            # keep nearly all of them.
            if "![" in value:
                texts.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(result)
    names: set[str] = set()
    for text in texts:
        for target in _IMAGE_TARGET.finditer(text):
            names.update(match.group(0) for match in _FRAME_NAME.finditer(target.group(1)))
    return names


def enforce_history_retention(
    client_id: str | None,
    *,
    keep_count: int = 0,
    artifact_days: int,
    source_days: int,
    list_jobs: Callable[..., list[dict]],
    update_result: Callable[..., Any],
    delete_jobs: Callable[..., Any] | None = None,
    load_job: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Expire source copies and media; never delete a task or its text.

    ``keep_count`` and ``delete_jobs`` are accepted for callers written against
    the old rule and are not used: nothing here removes a task.
    """
    del keep_count, delete_jobs, source_days
    if not client_id:
        return {"pruned_count": 0, "task_ids": [], "expired_source_count": 0, "expired_media_count": 0}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=artifact_days) if artifact_days > 0 else None
    expired_sources = 0
    expired_media: list[str] = []
    for job in list_jobs(client_id=client_id):
        task_id = str(job.get("task_id") or "")
        if not task_id or job.get("status") not in _FINISHED:
            continue
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        owner = job.get("client_id")

        expires = parse_job_time(result.get("source_retention_expires_at"))
        if result.get("source_file_available") and expires and expires <= now:
            full = _full_result(task_id, owner, result, load_job)
            if full is not None:
                cleanup = cleanup_task_source_files(task_id, job.get("metadata"))
                update_result(task_id, {
                    **full,
                    "source_file_available": False,
                    "source_file_storage": None,
                    "source_retention_status": "expired",
                    "source_retention_cleaned_at": cleanup.get("source_retention_cleaned_at"),
                }, client_id=owner, touch_updated_at=False)
                result = {**result, "source_file_available": False}
                expired_sources += 1

        updated = parse_job_time(job.get("updated_at") or job.get("created_at"))
        if not cutoff or not updated or updated >= cutoff:
            continue
        if result.get("media_retention_status") == "expired":
            continue
        full = _full_result(task_id, owner, result, load_job)
        if full is None:
            continue
        cleanup = cleanup_task_media_files(
            task_id, job.get("metadata"), keep_frames=frames_shown_in_notes(full),
        )
        update_result(task_id, {
            **full,
            "source_file_available": False,
            "source_file_storage": None,
            "media_retention_status": "expired",
            "media_retention_cleaned_at": cleanup.get("media_retention_cleaned_at"),
            "media_retention_freed_bytes": cleanup.get("media_retention_freed_bytes"),
        }, client_id=owner, touch_updated_at=False)
        expired_media.append(task_id)
    return {
        "pruned_count": 0,
        "task_ids": [],
        "expired_source_count": expired_sources,
        "expired_media_count": len(expired_media),
        "expired_media_task_ids": expired_media,
    }


def _full_result(
    task_id: str,
    client_id: str | None,
    result: dict[str, Any],
    load_job: Callable[..., dict[str, Any] | None] | None,
) -> dict[str, Any] | None:
    """The stored result to merge into. A retention summary is never written
    back as if it were the result: that would replace the transcript and note
    with three bookkeeping fields."""
    if not result.get("_retention_summary"):
        return result
    if load_job is None:
        return None
    job = load_job(task_id, client_id=client_id) or {}
    full = job.get("result")
    return full if isinstance(full, dict) else None
