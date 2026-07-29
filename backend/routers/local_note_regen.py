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
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from backend.core.ai_summarizer import summarize_transcript_with_metadata
from backend.core.chapter_coverage import bind_chapter_coverage_time_ranges
from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.job_lifecycle import (
    result_for_summary_failure,
    result_for_summary_success,
    result_for_transcript_only,
)
from backend.core.job_store import finalize_job_result_if_unchanged, get_job, upsert_job
from backend.core.local_config import resolve_secret
from backend.core.local_entry_guards import (
    claim_task_id,
    local_ai_kwargs,
    read_upload_bounded,
    require_owned_task_id,
)
from backend.core.local_error_diagnostics import diagnose_error
from backend.core.local_limits_config import (
    max_media_duration_seconds,
    max_transcript_upload_mb,
)
from backend.core.local_request_scope import request_client_id
from backend.core.media_intake import TRANSCRIPT_SUFFIXES, file_size_mb
from backend.core.result_artifacts import _attach_result_artifacts
from backend.core.transcript_cleaner import clean_repeated_transcript
from backend.core.transcript_correction import (
    correct_transcript_segments,
    correction_result_fields,
    transcript_correction_enabled,
)
from backend.core.transcript_parser import parse_transcript_file

router = APIRouter()


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _friendly_error(error: Any) -> str:
    return str(diagnose_error(error).get("detail") or "").strip() or str(error)


def _summary_result_metadata(summary_result: Any) -> dict[str, Any]:
    return {
        "resolved_note_mode": getattr(summary_result, "resolved_mode", None),
        "note_mode_chunk_count": getattr(summary_result, "chunk_count", None),
        "note_mode_transcript_length": getattr(summary_result, "transcript_length", None),
        "coverage_checked": getattr(summary_result, "coverage_checked", None),
        "coverage_revision_used": getattr(summary_result, "coverage_revision_used", None),
        "note_mode_segment_count": getattr(summary_result, "segment_count", None),
        "note_mode_evidence_count": getattr(summary_result, "evidence_count", None),
        "note_mode_chapter_count": getattr(summary_result, "chapter_count", None),
        "note_mode_important_evidence_count": getattr(summary_result, "important_evidence_count", None),
        "note_mode_covered_important_evidence_count": getattr(summary_result, "covered_important_evidence_count", None),
        "note_mode_coverage_missing_count": getattr(summary_result, "coverage_missing_count", None),
    }


def _duration_limit_error(duration_seconds: float, filename: str | None) -> str | None:
    limit = max_media_duration_seconds()
    if limit <= 0 or duration_seconds <= limit:
        return None
    name = f"「{filename}」" if filename else "当前媒体"
    return (
        f"{name}时长过长：约 {duration_seconds / 60:.1f} 分钟，"
        f"当前限制为 {limit / 60:.1f} 分钟。"
    )


def _cleanup_payload(cleanup_result: Any) -> dict[str, Any]:
    return {
        "applied_count": cleanup_result.applied_count,
        "removed_segment_count": cleanup_result.removed_segment_count,
        "raw_length": cleanup_result.raw_length,
        "cleaned_length": cleanup_result.cleaned_length,
        "issues": [asdict(item) for item in cleanup_result.issues[:20]],
        "issue_count": len(cleanup_result.issues),
    }


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
    client_id = _local_client_scope(request)
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
            None, lambda: summarize_transcript_with_metadata(transcript, **kwargs)
        )
        md = summary_result.markdown
        if existing_job:
            latest_job = get_job(task_id_value, client_id=client_id)
            latest_result = deepcopy(latest_job.get("result")) if latest_job else None
            if latest_result != initial_result:
                raise HTTPException(
                    status_code=409,
                    detail="笔记在生成期间已被修改，本次生成结果未覆盖你的编辑。",
                )
        payload = {
            "summary_markdown": md,
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
            "prompt_preset": (prompt_preset or "").strip() or None,
            "prompt_preset_label": (prompt_preset_label or "").strip() or None,
            "regenerated_from_task_id": regenerated_from_task_id,
        }
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
        result.update({
            **payload,
            "summary_skipped": False,
            "status": "completed",
        })
        result = bind_chapter_coverage_time_ranges(result)
        if isinstance(result.get("chapter_coverage"), dict):
            payload["chapter_coverage"] = result["chapter_coverage"]
        if existing_job:
            updated = finalize_job_result_if_unchanged(
                task_id=task_id_value,
                expected_result=initial_result,
                result=result,
                status="completed",
                client_id=client_id,
                stage="done",
                progress=100,
                summary_status="completed",
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


async def _run_local_transcript_correction(
    *,
    loop: asyncio.AbstractEventLoop,
    task_id: str,
    route: str,
    source_filename: str | None,
    source_duration_seconds: float | None,
    source_file_size_mb: float | None,
    transcript_text: str,
    segments: list[dict[str, Any]],
    deepseek_api_key: str | None,
) -> tuple[dict[str, Any], str]:
    """Optional conservative transcript correction with the LOCAL credential
    store (mirrors the hosted stage in ``media_job`` without failing the task)."""
    if not transcript_correction_enabled():
        return {"note_generation_transcript_source": "transcript_text"}, transcript_text

    started_at = time.perf_counter()
    correction_result = await loop.run_in_executor(
        None,
        lambda: correct_transcript_segments(
            segments,
            api_key=resolve_secret(deepseek_api_key, "deepseek_api_key"),
            provider="deepseek",
        ),
    )
    note_transcript_text = correction_result.corrected_text or transcript_text
    fields = correction_result_fields(
        correction_result,
        note_input_applied=bool(correction_result.corrected_text),
    )
    fields["note_generation_transcript_source"] = (
        "corrected_transcript" if correction_result.corrected_text else "transcript_text"
    )
    log_event(
        task_id=task_id,
        event_name=(
            "transcript_correction_completed"
            if correction_result.status in {"completed", "no_changes"}
            else "transcript_correction_unavailable"
        ),
        source_type="transcript_file",
        source_filename=source_filename,
        source_duration_seconds=source_duration_seconds,
        source_file_size_mb=source_file_size_mb,
        transcript_length=len(transcript_text or ""),
        stage="transcript_correction",
        duration_seconds=round(time.perf_counter() - started_at, 3),
        success=correction_result.status in {"completed", "no_changes"},
        error_reason=correction_result.error,
        metadata=event_metadata(
            route=route,
            correction_status=correction_result.status,
            correction_applied_count=correction_result.applied_count,
            correction_rejected_count=correction_result.rejected_count,
            correction_provider=correction_result.provider,
            correction_model=correction_result.model,
            note_generation_transcript_source=fields["note_generation_transcript_source"],
        ),
    )
    return fields, note_transcript_text


async def summarize_transcript_source(
    *,
    raw: bytes | bytearray,
    filename: str,
    client_id: Optional[str],
    task_id: str,
    deepseek_api_key: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    qwen_api_key: Optional[str] = None,
    ai_provider: Optional[str] = None,
    ai_model: Optional[str] = None,
    note_mode: Optional[str] = None,
    skip_summary: Optional[str] = None,
    system_prompt: Optional[str] = None,
    prompt_preset: Optional[str] = None,
    prompt_preset_label: Optional[str] = None,
    route: str = "/summarize-transcript-file",
) -> dict[str, Any]:
    """Parse a .srt/.vtt/.txt/.md transcript source, optionally summarizing it.

    Shared by the ``/summarize-transcript-file`` route and the local
    video-source worker (YouTube caption downloads run this in-process instead
    of the hosted HTTP self-call). Raises ``HTTPException`` on invalid input.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in TRANSCRIPT_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"Unsupported transcript file type: {suffix}")

    loop = asyncio.get_event_loop()
    # Public uploads atomically claim a fresh job before entering this core;
    # the video-source worker references its own pre-created active job.
    task_id_value = require_owned_task_id(
        task_id,
        client_id=client_id,
        allowed_statuses={"queued", "running"},
    )
    summary_disabled = _truthy(skip_summary)
    source_filename = filename
    source_file_size_mb = file_size_mb(len(raw))
    limit_mb = max_transcript_upload_mb()
    if source_file_size_mb is not None and source_file_size_mb > limit_mb:
        raise HTTPException(
            status_code=413,
            detail=f"File is too large: {source_file_size_mb} MB. Limit is {limit_mb:g} MB.",
        )
    log_event(
        task_id=task_id_value,
        event_name="source_imported",
        source_type="transcript_file",
        source_filename=source_filename,
        source_file_size_mb=source_file_size_mb,
        stage="import",
        success=True,
        metadata=event_metadata(route=route, suffix=suffix),
    )
    upsert_job(
        task_id=task_id_value,
        status="running",
        client_id=client_id,
        stage="import",
        progress=10,
        source_type="transcript_file",
        source_filename=source_filename,
        source_file_size_mb=source_file_size_mb,
    )

    def _fail_job(stage: str, detail: str, raw_error: str | None = None) -> None:
        log_event(
            task_id=task_id_value,
            event_name="task_failed",
            source_type="transcript_file",
            source_filename=source_filename,
            source_file_size_mb=source_file_size_mb,
            stage=stage,
            success=False,
            error_reason=detail,
            metadata=event_metadata(
                route=route,
                raw_error=raw_error,
            ),
        )
        upsert_job(
            task_id=task_id_value,
            status="failed",
            client_id=client_id,
            stage=stage,
            source_type="transcript_file",
            source_filename=source_filename,
            source_file_size_mb=source_file_size_mb,
            summary_status="failed",
            error_reason=detail,
        )

    try:
        parsed = parse_transcript_file(raw, filename)
        if not parsed.text.strip():
            raise HTTPException(status_code=400, detail="Transcript file is empty")
        if parsed.duration:
            duration_error = _duration_limit_error(parsed.duration, source_filename)
            if duration_error:
                raise HTTPException(status_code=413, detail=duration_error)
    except HTTPException as exc:
        _fail_job("transcript_parse", str(exc.detail))
        raise
    except Exception as exc:
        friendly_error = _friendly_error(exc)
        _fail_job("transcript_parse", friendly_error, raw_error=str(exc))
        raise HTTPException(status_code=500, detail=friendly_error) from exc

    review_segments_input = parsed.segments or tuple(
        {"start": 0.0, "end": 0.0, "text": line}
        for line in parsed.text.splitlines()
        if line.strip()
    )
    cleanup_started_at = time.perf_counter()
    cleanup_result = clean_repeated_transcript(review_segments_input)
    if cleanup_result.applied_count > 0:
        log_event(
            task_id=task_id_value,
            event_name="transcript_cleanup_completed",
            source_type="transcript_file",
            source_filename=source_filename,
            source_duration_seconds=round(parsed.duration, 1),
            source_file_size_mb=source_file_size_mb,
            transcript_length=cleanup_result.cleaned_length,
            stage="transcript_cleanup",
            duration_seconds=round(time.perf_counter() - cleanup_started_at, 3),
            success=True,
            metadata=event_metadata(
                route=route,
                cleanup_issue_count=len(cleanup_result.issues),
                cleanup_applied_count=cleanup_result.applied_count,
                cleanup_removed_segment_count=cleanup_result.removed_segment_count,
                cleanup_raw_length=cleanup_result.raw_length,
                cleanup_cleaned_length=cleanup_result.cleaned_length,
            ),
        )
    transcript_text = cleanup_result.cleaned_text
    segments_payload = list(cleanup_result.cleaned_segments) if parsed.segments else []
    raw_segments_payload = list(parsed.segments)

    log_event(
        task_id=task_id_value,
        event_name="transcript_ready",
        source_type="transcript_file",
        source_filename=source_filename,
        source_duration_seconds=round(parsed.duration, 1),
        source_file_size_mb=source_file_size_mb,
        transcript_length=len(transcript_text or ""),
        stage="transcript_ready",
        success=True,
        metadata=event_metadata(
            route=route, segment_count=len(parsed.segments)
        ),
    )
    base_result: dict[str, Any] = {
        "task_id": task_id_value,
        "filename": filename,
        "transcript_text": transcript_text,
        "raw_transcript_text": parsed.text,
        "cleaned_transcript_text": cleanup_result.cleaned_text,
        "transcript_text_preview": transcript_text[:200],
        "summary_markdown": "",
        "audio_duration_seconds": round(parsed.duration, 1),
        "display_segments": segments_payload,
        "raw_segments": segments_payload,
        "stt_raw_segments": raw_segments_payload,
        "transcript_cleanup": _cleanup_payload(cleanup_result),
        "status": "transcript_ready",
        "source": "transcript_file",
        "summary_skipped": summary_disabled,
    }
    note_transcript_text = transcript_text
    if not summary_disabled:
        correction_fields, note_transcript_text = await _run_local_transcript_correction(
            loop=loop,
            task_id=task_id_value,
            route=route,
            source_filename=source_filename,
            source_duration_seconds=round(parsed.duration, 1),
            source_file_size_mb=source_file_size_mb,
            transcript_text=transcript_text,
            segments=segments_payload,
            deepseek_api_key=deepseek_api_key,
        )
        base_result.update(correction_fields)
    base_result = _attach_result_artifacts(task_id_value, base_result)
    upsert_job(
        task_id=task_id_value,
        status="running",
        client_id=client_id,
        stage="transcript_ready",
        progress=60,
        result=base_result,
        summary_status="pending",
    )

    if summary_disabled:
        log_event(
            task_id=task_id_value,
            event_name="summary_skipped",
            source_type="transcript_file",
            source_filename=source_filename,
            source_duration_seconds=round(parsed.duration, 1),
            source_file_size_mb=source_file_size_mb,
            transcript_length=len(transcript_text or ""),
            stage="summary",
            success=True,
            metadata=event_metadata(
                route=route, reason="transcript_only_mode"
            ),
        )
        result = result_for_transcript_only(base_result)
        result = _attach_result_artifacts(task_id_value, result)
        upsert_job(
            task_id=task_id_value,
            status="completed",
            client_id=client_id,
            stage="done",
            progress=100,
            result=result,
            summary_status="skipped",
        )
        return result

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
            None, lambda: summarize_transcript_with_metadata(note_transcript_text, **kwargs)
        )
        summary_md = summary_result.markdown
        if not summary_md.strip():
            raise ValueError("AI summarization returned empty result")
        log_event(
            task_id=task_id_value,
            event_name="summary_completed",
            source_type="transcript_file",
            source_filename=source_filename,
            source_duration_seconds=round(parsed.duration, 1),
            source_file_size_mb=source_file_size_mb,
            transcript_length=len(note_transcript_text or ""),
            summary_length=len(summary_md or ""),
            stage="summary",
            duration_seconds=round(time.perf_counter() - started_at, 3),
            success=True,
            metadata=event_metadata(
                route=route,
                ai_provider=(ai_provider or "").strip() or None,
                ai_model=(ai_model or "").strip() or None,
                requested_note_mode=summary_result.requested_mode,
                note_generation_transcript_source=base_result.get(
                    "note_generation_transcript_source"
                ),
                **_summary_result_metadata(summary_result),
            ),
        )
    except Exception as exc:
        friendly_error = _friendly_error(exc)
        log_event(
            task_id=task_id_value,
            event_name="summary_failed",
            source_type="transcript_file",
            source_filename=source_filename,
            source_duration_seconds=round(parsed.duration, 1),
            source_file_size_mb=source_file_size_mb,
            transcript_length=len(note_transcript_text or ""),
            stage="summary",
            duration_seconds=round(time.perf_counter() - started_at, 3),
            success=False,
            error_reason=friendly_error,
            metadata=event_metadata(
                route=route,
                raw_error=str(exc),
                note_generation_transcript_source=base_result.get(
                    "note_generation_transcript_source"
                ),
            ),
        )
        failed_result = result_for_summary_failure(base_result, friendly_error)
        failed_result = _attach_result_artifacts(task_id_value, failed_result)
        upsert_job(
            task_id=task_id_value,
            status="completed",
            client_id=client_id,
            stage="done",
            progress=100,
            result=failed_result,
            summary_status="failed",
            error_reason=friendly_error,
        )
        return failed_result

    result = result_for_summary_success(
        base_result,
        summary_md,
        requested_note_mode=summary_result.requested_mode,
        resolved_note_mode=summary_result.resolved_mode,
        note_mode_chunk_count=summary_result.chunk_count,
        note_mode_segment_count=getattr(summary_result, "segment_count", None),
        note_mode_evidence_count=getattr(summary_result, "evidence_count", None),
        note_mode_chapter_count=getattr(summary_result, "chapter_count", None),
        note_mode_important_evidence_count=getattr(summary_result, "important_evidence_count", None),
        note_mode_covered_important_evidence_count=getattr(
            summary_result, "covered_important_evidence_count", None
        ),
        note_mode_coverage_missing_count=getattr(summary_result, "coverage_missing_count", None),
        chapter_coverage=getattr(summary_result, "chapter_coverage", None),
        prompt_preset=(prompt_preset or "").strip() or None,
        prompt_preset_label=(prompt_preset_label or "").strip() or None,
    )
    result = _attach_result_artifacts(task_id_value, result)
    upsert_job(
        task_id=task_id_value,
        status="completed",
        client_id=client_id,
        stage="done",
        progress=100,
        result=result,
        summary_status="completed",
    )
    return result


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
    client_id = _local_client_scope(request)
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
