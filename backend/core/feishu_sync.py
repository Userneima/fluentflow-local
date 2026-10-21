"""A note exported to Feishu stays in step with it: when the note changes, the
same Feishu document is updated, chapter by chapter.

Requirement (owner, 2026-10-21). A note exported with the user's own identity
(lark-cli) has its document recorded on the task (``lark_response.sync``).
From then on:

- Exporting it again updates that document instead of creating another.
- When the note changes (edited in FluentFlow, or in Obsidian and taken back
  in), the document is updated once the changes have stopped for
  ``PUSH_QUIET_SECONDS``, the same wait the output folder uses.
- Only the chapters that changed are rewritten (``lark_cli_exporter._update``).
- Before anything in the document is replaced, the document as it is is kept
  among the note's versions (``note_versions``, side 飞书).

Notes exported before this, or through a Feishu app (OpenAPI), have no record
and are left alone. Pulling edits made in Feishu back into the note is the
other half and is not here yet.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.core import job_store, note_versions

logger = logging.getLogger(__name__)

PUSH_QUIET_SECONDS = 60.0


def recorded_sync(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """The Feishu document a note is kept in step with, or None."""
    if not isinstance(result, dict):
        return None
    response = result.get("lark_response")
    if not isinstance(response, dict) or response.get("via") != "lark_cli":
        return None
    sync = response.get("sync")
    if not isinstance(sync, dict) or not sync.get("doc_id") or not isinstance(sync.get("sections"), list):
        return None
    return sync


def _note_md(task_id: str, result: dict[str, Any]) -> Path:
    """Where the note's versions are kept: beside its md in the output folder,
    or, for a note with no folder, in the task's own storage."""
    from backend.core.output_folder import RESULT_KEY
    from backend.core.storage_paths import _artifact_storage_dir

    folder = result.get(RESULT_KEY) if isinstance(result.get(RESULT_KEY), dict) else {}
    if folder.get("md_path"):
        return Path(str(folder["md_path"]))
    return _artifact_storage_dir() / task_id / "笔记.md"


def keeper(task_id: str, result: dict[str, Any] | None) -> Callable[[str], None]:
    """What saves the Feishu document's markdown before an update replaces it."""
    md = _note_md(task_id, result if isinstance(result, dict) else {})

    def keep(text: str) -> None:
        note_versions.keep(md, text, note_versions.FEISHU)

    return keep


def push(task_id: str) -> dict[str, Any] | None:
    """Bring the task's Feishu document up to date with its note. Returns the
    export response, or None when there was nothing to do. Never raises: a
    failure is recorded on the task as ``lark_sync_error``."""
    from backend.core.lark_cli_exporter import export_markdown_via_lark_cli, note_digest
    from backend.core.note_title import resolve_lark_doc_title
    from backend.core.storage_paths import _artifact_storage_dir

    job = job_store.get_job(task_id)
    result = job.get("result") if isinstance(job, dict) and isinstance(job.get("result"), dict) else None
    sync = recorded_sync(result)
    if result is None or sync is None:
        return None
    note = str(result.get("summary_markdown") or "")
    if not note.strip() or note_digest(note) == sync.get("note_digest"):
        return None
    title = str(result.get("lark_doc_title") or "") or resolve_lark_doc_title(note, filename_stem="", form_title=None)
    try:
        response = export_markdown_via_lark_cli(
            title, note, task_id=task_id, artifact_root=_artifact_storage_dir(),
            previous=sync, keep_feishu=keeper(task_id, result),
        )
    except Exception as exc:  # noqa: BLE001 - the note itself is fine
        logger.warning("Feishu copy of %s not updated: %s", task_id, exc)
        _record(task_id, lark_sync_error=f"飞书那份没更新（{exc}）。笔记本身没受影响，下次改动时会再试。")
        return None
    previous = result.get("lark_response") if isinstance(result.get("lark_response"), dict) else {}
    _record(task_id, lark_response={**previous, **response}, lark_sync_error=None,
            lark_synced_at=datetime.now(timezone.utc).isoformat())
    return response


def _record(task_id: str, **fields: Any) -> None:
    job = job_store.get_job(task_id)
    if not isinstance(job, dict):
        return
    latest = job.get("result") if isinstance(job.get("result"), dict) else {}
    job_store.save_result_changes(task_id, latest, {**latest, **fields})


# ── when the note changes ───────────────────────────────────────────────────

_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="feishu-sync")
_TIMERS: dict[str, threading.Timer] = {}
_LOCK = threading.Lock()


def schedule_push_when_quiet(task_id: str, delay: float | None = None) -> None:
    """Push once the note has not changed for ``PUSH_QUIET_SECONDS``; each new
    change starts the wait again."""
    def fire() -> None:
        with _LOCK:
            if _TIMERS.get(task_id) is timer:
                _TIMERS.pop(task_id, None)
        _WORKER.submit(push, task_id)

    timer = threading.Timer(PUSH_QUIET_SECONDS if delay is None else delay, fire)
    timer.daemon = True
    with _LOCK:
        previous = _TIMERS.get(task_id)
        if previous is not None:
            previous.cancel()
        _TIMERS[task_id] = timer
    timer.start()


def _on_result_saved(task_id: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> None:
    if recorded_sync(after) is None:
        return
    note = str((after or {}).get("summary_markdown") or "")
    if note == str((before or {}).get("summary_markdown") or ""):
        return
    schedule_push_when_quiet(task_id)


job_store.add_result_listener(_on_result_saved)


__all__ = ["PUSH_QUIET_SECONDS", "keeper", "push", "recorded_sync", "schedule_push_when_quiet"]
