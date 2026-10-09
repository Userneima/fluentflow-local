"""Completed-job retention: only FluentFlow's own copy of an upload expires.

The rule, decided by the owner on 2026-10-07: nothing a task produced is ever
deleted automatically. The transcript, subtitles, notes, every frame and the
de-breathed audio or video stay until the user deletes the task. The user
manages their own original recordings, so retention does not reach into their
folders either.

What remains is FluentFlow's private copy of an uploaded file, kept in its data
directory so a task can be re-run. Deleting the original elsewhere does not
remove that copy, so it still expires after the source window.

A video downloaded from a link is different: there is no original anywhere
else, so the task's copy is the only copy of the recording. It expires only
once a verified de-breathed version of it exists; without one it is kept.
The download is handed to the task when it finishes; a task from before that
still has a second copy in the video-link folder, which goes as soon as the
task's own copy is on disk at the same size.

Before this, a thirty-day window deleted whole tasks, notes included, without
notice: a hundred and forty-three tasks on the maintainer's machine.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.core.retention_time import parse_job_time
from backend.core.debreath_job import cut_file_on_disk
from backend.core.storage_cleanup import cleanup_task_source_files, remove_tree
from backend.core.storage_paths import _video_source_storage_dir, find_source_file

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
        _drop_link_download_kept_twice(task_id, job.get("metadata"))
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        expires = parse_job_time(result.get("source_retention_expires_at"))
        if not (result.get("source_file_available") and expires and expires <= now):
            continue
        owner = job.get("client_id")
        full = _full_result(task_id, owner, result, load_job)
        if full is None:
            continue
        if _downloaded_from_link(job.get("metadata")) and cut_file_on_disk(task_id, full) is None:
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


def _drop_link_download_kept_twice(task_id: str, metadata: Any) -> bool:
    """Remove a link download left in the video-link folder beside the task's
    own copy of it. Only when the task's copy is there at the same size: the
    one in the video-link folder may otherwise be the only copy."""
    video_source = metadata.get("video_source") if isinstance(metadata, dict) else None
    raw = str(video_source.get("file_path") or "").strip() if isinstance(video_source, dict) else ""
    if not raw:
        return False
    download = Path(raw).expanduser()
    own = find_source_file(task_id)
    if own is None or not download.is_file():
        return False
    try:
        if download.resolve().parent != _video_source_storage_dir().resolve():
            return False
        if download.resolve() == own.resolve() or download.stat().st_size != own.stat().st_size:
            return False
    except OSError:
        return False
    removed = remove_tree(download)
    meta_path = str(video_source.get("metadata_path") or "").strip()
    if removed and meta_path:
        remove_tree(Path(meta_path).expanduser())
    return removed


def _downloaded_from_link(metadata: Any) -> bool:
    video_source = metadata.get("video_source") if isinstance(metadata, dict) else None
    return isinstance(video_source, dict) and bool(video_source.get("file_path"))
