"""Shared transcript/summary edit router, assembled with an edition-specific
client scope.

Edits are edition-neutral at their core (validate, canonicalize segments, write
edit/backups, regenerate artifacts, persist the result). Two hosted concerns are
injected as optional hooks:

- ``reject_readonly`` — the hosted desktop-sync read-only write guard;
- ``on_after_write`` — hosted desktop-sync result propagation.

The local edition passes neither.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, BackgroundTasks, HTTPException, Request

from backend.core.job_store import get_job, update_job_result
from backend.core.note_write import (
    NOTE_SOURCE_EDITOR,
    apply_summary_edit,
    max_summary_edit_chars,
)
from backend.core.result_artifacts import (
    _attach_result_artifacts,
    _canonical_display_segments,
    _sanitize_edit_records,
    _sanitize_edit_segments,
    _write_edited_transcript_backup,
    _write_transcript_edit_records_backup,
)

logger = logging.getLogger(__name__)


def create_job_edit_router(
    *,
    request_client_scope: Callable[[Request], Optional[str]],
    reject_readonly: Optional[Callable[[Request, dict[str, Any]], None]] = None,
    on_after_write: Optional[Callable[[Request, dict[str, Any], BackgroundTasks], None]] = None,
) -> APIRouter:
    router = APIRouter()

    @router.patch("/jobs/{task_id}/transcript")
    def update_job_transcript(
        request: Request,
        task_id: str,
        background_tasks: BackgroundTasks,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        client_id = request_client_scope(request)
        job = get_job(task_id, client_id=client_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if reject_readonly is not None:
            reject_readonly(request, job)
        result = dict(job.get("result") or {})
        transcript = payload.get("transcript_text")
        if not isinstance(transcript, str):
            raise HTTPException(status_code=400, detail="transcript_text is required")
        max_chars = int(os.environ.get("FLUENTFLOW_MAX_TRANSCRIPT_EDIT_CHARS", "1000000"))
        if len(transcript) > max_chars:
            raise HTTPException(status_code=413, detail=f"Transcript edit is too large: {len(transcript)} chars")

        segments = _sanitize_edit_segments(payload.get("segments"))
        edit_records = _sanitize_edit_records(payload.get("edit_records"))
        edited_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        had_translation = any(
            str(segment.get("text_zh") or "").strip()
            for segment in _canonical_display_segments(result)
        ) or bool(result.get("bilingual_segments") or result.get("translated_segments_zh"))
        result.update({
            "task_id": result.get("task_id") or task_id,
            "transcript_text": transcript,
            "transcript_text_preview": transcript[:200],
            "raw_segments": segments,
            "display_segments": segments,
            "subtitle_mode": "source_only",
            "translation_status": "stale" if had_translation else result.get("translation_status"),
            "transcript_edit_records": edit_records,
            "transcript_edit_record_count": len(edit_records),
            "transcript_edited": True,
            "transcript_edited_at": edited_at,
        })
        try:
            backup_path = _write_edited_transcript_backup(task_id, result)
            edit_records_path = _write_transcript_edit_records_backup(task_id, result, edit_records)
        except Exception as exc:
            logger.warning("Edited transcript backup failed for %s: %s", task_id, exc)
            raise HTTPException(status_code=500, detail="Edited transcript backup failed") from exc

        result.update({
            "edited_transcript_path": str(backup_path),
            "edited_transcript_saved_at": edited_at,
            "transcript_edit_records_path": str(edit_records_path),
        })
        result = _attach_result_artifacts(task_id, result)
        updated = update_job_result(task_id, result, client_id=client_id)
        if not updated:
            raise HTTPException(status_code=404, detail="Job not found")
        if on_after_write is not None:
            on_after_write(request, updated, background_tasks)
        return {"ok": True, "job": updated, "result": updated.get("result")}

    @router.patch("/jobs/{task_id}/summary")
    def update_job_summary(
        request: Request,
        task_id: str,
        background_tasks: BackgroundTasks,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        client_id = request_client_scope(request)
        job = get_job(task_id, client_id=client_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if reject_readonly is not None:
            reject_readonly(request, job)
        result = dict(job.get("result") or {})
        summary = payload.get("summary_markdown")
        if not isinstance(summary, str):
            raise HTTPException(status_code=400, detail="summary_markdown is required")
        max_chars = max_summary_edit_chars()
        if len(summary) > max_chars:
            raise HTTPException(status_code=413, detail=f"Summary edit is too large: {len(summary)} chars")

        result = apply_summary_edit(result, task_id, summary, source=NOTE_SOURCE_EDITOR)
        result = _attach_result_artifacts(task_id, result)
        updated = update_job_result(task_id, result, client_id=client_id)
        if not updated:
            raise HTTPException(status_code=404, detail="Job not found")
        if on_after_write is not None:
            on_after_write(request, updated, background_tasks)
        return {"ok": True, "job": updated, "result": updated.get("result")}

    return router
