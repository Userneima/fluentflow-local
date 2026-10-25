"""A note from a transcript file (a .srt, .vtt, .txt or .md): the file is
parsed, cleaned and, unless only the transcript was asked for, summarised by
the text model. The transcript-file route and a link whose captions were
downloaded (``link_submission``) both call ``summarize_transcript_source``."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from backend.core import glossary_correction
from backend.core.ai_summarizer import summarize_transcript_with_metadata
from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.job_lifecycle import (
    result_for_summary_failure,
    result_for_summary_success,
    result_for_transcript_only,
)
from backend.core.job_store import upsert_job
from backend.core.local_entry_guards import (
    local_ai_kwargs,
    require_owned_task_id,
)
from backend.core.local_error_diagnostics import diagnose_error
from backend.core.local_limits_config import (
    max_media_duration_seconds,
    max_transcript_upload_mb,
)
from backend.core.media_intake import TRANSCRIPT_SUFFIXES, file_size_mb
from backend.core.runtime_env import truthy
from backend.core.result_artifacts import _attach_result_artifacts
from backend.core.transcript_cleaner import clean_repeated_transcript
from backend.core.transcript_parser import parse_transcript_file


def _friendly_error(error: Any) -> str:
    return str(diagnose_error(error).get("detail") or "").strip() or str(error)


class _EmptyNote(Exception):
    """The model answered with nothing to write."""

    def __init__(self) -> None:
        super().__init__("AI summarization returned empty result")


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
        "deadline_hit": bool(getattr(summary_result, "deadline_hit", False)),
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
    summary_disabled = truthy(skip_summary)
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
        # The submitted transcript is FluentFlow's starting text: a later run
        # of the glossary step leaves alone what the user edits from here.
        glossary_correction.FINGERPRINTS: glossary_correction.fingerprints(segments_payload),
        "transcript_cleanup": _cleanup_payload(cleanup_result),
        "status": "transcript_ready",
        "source": "transcript_file",
        "summary_skipped": summary_disabled,
    }
    note_transcript_text = transcript_text
    if not summary_disabled:
        base_result["note_generation_transcript_source"] = "transcript_text"
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
        deadline_hit=bool(getattr(summary_result, "deadline_hit", False)),
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
