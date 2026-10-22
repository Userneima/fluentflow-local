"""Local transcript/summary edit routes: validate, canonicalize segments, write
edit backups, regenerate artifacts, and persist the result.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Request

from backend.core import note_versions
from backend.core.job_store import get_job, save_result_changes
from backend.core.local_request_scope import request_client_id
from backend.core.result_artifacts import (
    _attach_result_artifacts,
    _canonical_display_segments,
    _sanitize_edit_records,
    _sanitize_edit_segments,
    _write_edited_transcript_backup,
    _write_transcript_edit_records_backup,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


STALE_NOTE = "笔记在别处被改过（Obsidian、agent 或另一个页面），已换成最新的版本；你刚才这几秒的修改没有保存，请在新版本上再改一次。"
STALE_TRANSCRIPT = "字幕在别处被改过（重新剪辑、另一个页面或 agent），已换成最新的版本；你刚才这几秒的修改没有保存，请在新版本上再改一次。"


NO_VERSION_NOTE = "这次保存没有带上笔记的版本，页面拿到的可能不是完整的最新笔记，已拒绝，笔记没有改动。请刷新页面再改。"
NO_VERSION_TRANSCRIPT = "这次保存没有带上字幕的版本，页面拿到的可能不是完整的最新字幕，已拒绝，字幕没有改动。请刷新页面再改。"


def _refuse_if_moved_on(result: dict[str, Any], payload: dict[str, Any], key: str, message: str, missing: str) -> None:
    """Refuse a save that does not name the version it was made on, or names an
    older one than the task holds now.

    ``base_version`` is the version the page started from (every read of the
    task carries it). These two routes are the web editor's saves: no MCP tool
    calls them, agents write through ``/agent/v1``. A save without a version
    used to be taken as it was, which is how the task list's 240-character
    preview was saved over a 40,000-character note (2026-10-22, decision 6.30).
    """
    base = payload.get("base_version")
    if not isinstance(base, str) or not base:
        raise HTTPException(status_code=409, detail={"code": "version_required", "message": missing})
    if base != result.get(key):
        raise HTTPException(status_code=409, detail={"code": "stale", "message": message})


@router.patch("/jobs/{task_id}/transcript")
def update_job_transcript(
    request: Request,
    task_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    client_id = _local_client_scope(request)
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    before = dict(job.get("result") or {})
    _refuse_if_moved_on(before, payload, "transcript_version", STALE_TRANSCRIPT, NO_VERSION_TRANSCRIPT)
    result = dict(before)
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
    result = _attach_result_artifacts(task_id, result, parts=("transcript",))
    # Only the fields this save changed: the note or the cut may move meanwhile.
    updated = save_result_changes(task_id, before, result, client_id=client_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True, "job": updated, "result": updated.get("result")}


@router.patch("/jobs/{task_id}/summary")
def update_job_summary(
    request: Request,
    task_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    client_id = _local_client_scope(request)
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    before = dict(job.get("result") or {})
    _refuse_if_moved_on(before, payload, "note_version", STALE_NOTE, NO_VERSION_NOTE)
    result = dict(before)
    summary = payload.get("summary_markdown")
    if not isinstance(summary, str):
        raise HTTPException(status_code=400, detail="summary_markdown is required")
    max_chars = int(os.environ.get("FLUENTFLOW_MAX_SUMMARY_EDIT_CHARS", "500000"))
    if len(summary) > max_chars:
        raise HTTPException(status_code=413, detail=f"Summary edit is too large: {len(summary)} chars")
    current = str(before.get("summary_markdown") or "")
    if len(current) > len(summary) and summary.strip() and summary == current[:len(summary)] and len(summary) <= 240:
        # The task list carries only the note's first 240 characters. A page
        # that saved before the full note reached it sent that preview back as
        # the whole note, and the 3.5-hour interview's 40,000-character note
        # became 240 characters in the task, its folder and Feishu (2026-10-22).
        raise HTTPException(status_code=409, detail={
            "code": "preview_not_note",
            "message": "这次保存只有笔记开头的一小段，像是页面还没载入全文就存了，已拒绝，笔记没有改动。请刷新页面再改。",
        })

    edited_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    if not result.get("summary_edited") and not result.get("summary_original_markdown"):
        # The first edit of a generated note keeps what was generated, so the
        # user's corrections can later be read as differences from it and
        # turned into rules for their own note skill.
        result["summary_original_markdown"] = str(result.get("summary_markdown") or "")
    result.update({
        "task_id": result.get("task_id") or task_id,
        "summary_markdown": summary,
        "summary_skipped": False,
        "summary_status": "completed" if summary.strip() else result.get("summary_status") or "completed",
        "summary_error": None,
        "summary_edited": True,
        "summary_edited_at": edited_at,
    })
    note_versions.declare(result, note_versions.WEB_EDIT)
    # The note's own file only; rewriting the transcript files on every pause
    # in typing was work for nothing.
    result = _attach_result_artifacts(task_id, result, parts=("summary",))
    updated = save_result_changes(task_id, before, result, client_id=client_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True, "job": updated, "result": updated.get("result")}
