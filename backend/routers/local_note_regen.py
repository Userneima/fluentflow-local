"""Local-edition note regeneration: re-run AI summarization on an existing
transcript (``/regenerate-summary``) and import + summarize a standalone
transcript file (``/summarize-transcript-file``).

Functionally local: user-provided AI keys resolve through the local credential
store, jobs persist under the local client scope, and there is no quota, rate
limiting, account guard, or desktop-sync propagation. Response payloads keep
the hosted field names so the shared frontend works unchanged.
"""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from backend.core import model_usage, note_versions, task_steps, visual_note_job
from backend.core.ai_summarizer import summarize_transcript_with_metadata
from backend.core.chapter_coverage import bind_chapter_coverage_time_ranges
from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.job_store import NOTE_BASIS_FIELDS, finalize_note_if_unchanged, get_job, upsert_job
from backend.core.local_entry_guards import (
    claim_task_id,
    local_ai_kwargs,
    read_upload_bounded,
    require_owned_task_id,
)
from backend.core.local_limits_config import (
    max_transcript_upload_mb,
)
from backend.core.media_intake import TRANSCRIPT_SUFFIXES
from backend.core.request_scope import local_client_scope

from backend.core.transcript_note import (  # noqa: E402
    _EmptyNote,
    _friendly_error,
    _summary_result_metadata,
    summarize_transcript_source,
)

router = APIRouter()




def _what_the_note_rests_on(result: Any) -> tuple[Any, ...]:
    """What a rewrite started from and must still find (``NOTE_BASIS_FIELDS``):
    anything else in the task may move during the model call (audit,
    2026-10-18: any write at all refused a rewrite already paid for)."""
    if not isinstance(result, dict):
        return tuple(None for _ in NOTE_BASIS_FIELDS)
    return tuple(result.get(key) for key in NOTE_BASIS_FIELDS)


@router.post("/regenerate-summary")
async def regenerate_summary(
    request: Request,
    transcript: str = Form(...),
    deepseek_api_key: Optional[str] = Form(None),
    openai_api_key: Optional[str] = Form(None),
    qwen_api_key: Optional[str] = Form(None),
    ai_provider: Optional[str] = Form(None),
    ai_model: Optional[str] = Form(None),
    note_mode: Optional[str] = Form(None),
    system_prompt: Optional[str] = Form(None),
    prompt_preset: Optional[str] = Form(None),
    prompt_preset_label: Optional[str] = Form(None),
    task_id: Optional[str] = Form(None),
    source_type: Optional[str] = Form(None),
    source_filename: Optional[str] = Form(None),
    source_duration_seconds: Optional[float] = Form(None),
) -> dict[str, Any]:
    """Re-run AI summarization on an existing transcript."""
    loop = asyncio.get_event_loop()
    client_id = local_client_scope(request)
    requested_task_id = (task_id or "").strip()
    existing_job = None
    regenerated_from_task_id = None
    if requested_task_id:
        try:
            task_id_value = require_owned_task_id(
                requested_task_id,
                client_id=client_id,
            )
            existing_job = get_job(task_id_value, client_id=client_id)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            regenerated_from_task_id = requested_task_id
            task_id_value = claim_task_id(None, client_id=client_id)
    else:
        task_id_value = claim_task_id(None, client_id=client_id)
    initial_result = deepcopy(existing_job.get("result")) if existing_job else None
    kwargs = local_ai_kwargs(
        deepseek_api_key=deepseek_api_key,
        openai_api_key=openai_api_key,
        qwen_api_key=qwen_api_key,
        ai_provider=ai_provider,
        ai_model=ai_model,
        system_prompt=system_prompt,
        note_mode=note_mode,
    )
    started_at = time.perf_counter()
    try:
        summary_result = await loop.run_in_executor(
            None, model_usage.carry(
                lambda: summarize_transcript_with_metadata(transcript, **kwargs), task_id=task_id_value, step=task_steps.NOTE,
            ),
        )
        md = summary_result.markdown
        if not str(md or "").strip():
            # The other write paths refuse an empty note here too. Writing it
            # through would replace a note the user has with nothing and call
            # that completed.
            raise _EmptyNote()
        if existing_job:
            latest_job = get_job(task_id_value, client_id=client_id)
            latest_result = deepcopy(latest_job.get("result")) if latest_job else None
            if _what_the_note_rests_on(latest_result) != _what_the_note_rests_on(initial_result):
                raise HTTPException(
                    status_code=409,
                    detail="笔记在生成期间已被修改，本次生成结果未覆盖你的编辑。",
                )
        payload = {
            "summary_markdown": md,
            "summary_edited": False,
            "summary_original_markdown": None,
            "task_id": task_id_value,
            "requested_note_mode": summary_result.requested_mode,
            "resolved_note_mode": summary_result.resolved_mode,
            "note_mode_chunk_count": summary_result.chunk_count,
            "note_mode_segment_count": getattr(summary_result, "segment_count", None),
            "note_mode_evidence_count": getattr(summary_result, "evidence_count", None),
            "note_mode_chapter_count": getattr(summary_result, "chapter_count", None),
            "note_mode_important_evidence_count": getattr(summary_result, "important_evidence_count", None),
            "note_mode_covered_important_evidence_count": getattr(summary_result, "covered_important_evidence_count", None),
            "note_mode_coverage_missing_count": getattr(summary_result, "coverage_missing_count", None),
            "chapter_coverage": getattr(summary_result, "chapter_coverage", None),
            "deadline_hit": bool(getattr(summary_result, "deadline_hit", False)),
            "prompt_preset": (prompt_preset or "").strip() or None,
            "prompt_preset_label": (prompt_preset_label or "").strip() or None,
            "regenerated_from_task_id": regenerated_from_task_id,
            # The note is now the text model's. Without this the stamp of an
            # earlier frame note stayed, and the page kept describing a text
            # rewrite as Claude reading the frames.
            "summary_written_from": "text_regeneration",
        }
        note_versions.declare(payload, note_versions.REWRITE)
        existing = (
            existing_job
            if existing_job and task_id_value == requested_task_id
            else get_job(task_id_value, client_id=client_id)
        )
        result = dict(existing.get("result") or {}) if existing else {
            "task_id": task_id_value,
            "filename": source_filename,
            "transcript_text": transcript,
            "audio_duration_seconds": source_duration_seconds or 0,
            "source": source_type,
        }
        if existing:
            # The note this replaces stays restorable, as an agent's rewrite
            # keeps it (audit, 2026-10-18: a hand-edited note was lost here).
            result["visual_note"] = visual_note_job.record_replaced_note(result, replaced_by="text_regeneration")
        result.update({
            **payload,
            "summary_skipped": False,
            "status": "completed",
        })
        result = bind_chapter_coverage_time_ranges(result)
        if isinstance(result.get("chapter_coverage"), dict):
            payload["chapter_coverage"] = result["chapter_coverage"]
        if existing_job:
            # Refused only when the note or its transcript moved meanwhile.
            updated = finalize_note_if_unchanged(
                task_id_value, initial_result, result, status="completed", client_id=client_id,
                stage="done", progress=100, summary_status="completed",
            )
            if updated is None:
                raise HTTPException(
                    status_code=409,
                    detail="笔记在生成期间已被修改，本次生成结果未覆盖你的编辑。",
                )
        else:
            upsert_job(
                task_id=task_id_value,
                status="completed",
                client_id=client_id,
                stage="done",
                progress=100,
                source_type=source_type,
                source_filename=source_filename,
                summary_status="completed",
                result=result,
            )
        log_event(
            task_id=task_id_value,
            event_name="summary_regenerated",
            source_type=source_type,
            source_filename=source_filename,
            source_duration_seconds=source_duration_seconds,
            transcript_length=len(transcript or ""),
            summary_length=len(md or ""),
            stage="summary_regenerate",
            duration_seconds=round(time.perf_counter() - started_at, 3),
            success=True,
            metadata=event_metadata(
                route="/regenerate-summary",
                regenerated_from_task_id=regenerated_from_task_id,
                ai_provider=(ai_provider or "").strip() or None,
                ai_model=(ai_model or "").strip() or None,
                requested_note_mode=summary_result.requested_mode,
                **_summary_result_metadata(summary_result),
            ),
        )
        return payload
    except HTTPException:
        raise
    except _EmptyNote as exc:
        detail = (
            "模型没有返回内容，已保留之前的笔记。"
            if existing_job
            else "模型没有返回内容，没有生成笔记。"
        )
        log_event(
            task_id=task_id_value,
            event_name="summary_regenerated",
            source_type=source_type,
            source_filename=source_filename,
            source_duration_seconds=source_duration_seconds,
            transcript_length=len(transcript or ""),
            stage="summary_regenerate",
            duration_seconds=round(time.perf_counter() - started_at, 3),
            success=False,
            error_reason=detail,
            metadata=event_metadata(route="/regenerate-summary", raw_error=str(exc)),
        )
        # The old note stays, and so does the task's own status: only the
        # regeneration failed. A task that has no note yet has nothing to keep.
        upsert_job(
            task_id=task_id_value,
            status=str(existing_job.get("status") or "completed") if existing_job else "failed",
            client_id=client_id,
            stage="summary_regenerate",
            source_type=source_type,
            source_filename=source_filename,
            summary_status="failed",
            error_reason=detail,
        )
        raise HTTPException(status_code=502, detail=detail) from exc
    except Exception as exc:
        friendly_error = _friendly_error(exc)
        log_event(
            task_id=task_id_value,
            event_name="summary_regenerated",
            source_type=source_type,
            source_filename=source_filename,
            source_duration_seconds=source_duration_seconds,
            transcript_length=len(transcript or ""),
            stage="summary_regenerate",
            duration_seconds=round(time.perf_counter() - started_at, 3),
            success=False,
            error_reason=friendly_error,
            metadata=event_metadata(route="/regenerate-summary", raw_error=str(exc)),
        )
        log_event(
            task_id=task_id_value,
            event_name="task_failed",
            source_type=source_type,
            source_filename=source_filename,
            source_duration_seconds=source_duration_seconds,
            transcript_length=len(transcript or ""),
            stage="summary_regenerate",
            success=False,
            error_reason=friendly_error,
            metadata=event_metadata(route="/regenerate-summary", raw_error=str(exc)),
        )
        upsert_job(
            task_id=task_id_value,
            status="failed",
            client_id=client_id,
            stage="summary_regenerate",
            source_type=source_type,
            source_filename=source_filename,
            summary_status="failed",
            error_reason=friendly_error,
        )
        raise HTTPException(status_code=500, detail=friendly_error) from exc


@router.post("/summarize-transcript-file")
async def summarize_transcript_file(
    request: Request,
    file: UploadFile = File(...),
    deepseek_api_key: Optional[str] = Form(None),
    openai_api_key: Optional[str] = Form(None),
    qwen_api_key: Optional[str] = Form(None),
    ai_provider: Optional[str] = Form(None),
    ai_model: Optional[str] = Form(None),
    note_mode: Optional[str] = Form(None),
    skip_summary: Optional[str] = Form(None),
    system_prompt: Optional[str] = Form(None),
    prompt_preset: Optional[str] = Form(None),
    prompt_preset_label: Optional[str] = Form(None),
    task_id: Optional[str] = Form(None),
) -> dict[str, Any]:
    """Parse an existing .srt/.vtt/.txt/.md transcript, optionally summarizing it."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in TRANSCRIPT_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported transcript file type: {suffix}",
        )
    raw = await read_upload_bounded(file, max_transcript_upload_mb())
    client_id = local_client_scope(request)
    task_id_value = claim_task_id(task_id, client_id=client_id)
    return await summarize_transcript_source(
        raw=raw,
        filename=file.filename,
        client_id=client_id,
        task_id=task_id_value,
        deepseek_api_key=deepseek_api_key,
        openai_api_key=openai_api_key,
        qwen_api_key=qwen_api_key,
        ai_provider=ai_provider,
        ai_model=ai_model,
        note_mode=note_mode,
        skip_summary=skip_summary,
        system_prompt=system_prompt,
        prompt_preset=prompt_preset,
        prompt_preset_label=prompt_preset_label,
    )
