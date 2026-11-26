"""A note from a transcript file (a .srt, .vtt, .txt or .md): the file is
parsed, cleaned and, unless only the transcript was asked for, summarised by
the text model. The transcript-file route, a link whose captions were
downloaded (``link_submission``) and a subtitle file on this machine submitted
by path (``submit_subtitle_in_place``) all call ``summarize_transcript_source``."""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from backend.core import glossary_correction, model_usage, task_steps, task_stop
from backend.core.ai_summarizer import summarize_transcript_with_metadata
from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.job_lifecycle import (
    result_for_summary_failure,
    result_for_summary_success,
    result_for_transcript_only,
)
from backend.core.job_store import get_job, save_result_changes, upsert_job
from backend.core.local_entry_guards import (
    claim_task_id,
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
from backend.core.transcript_parser import UTF16_HINT, parse_transcript_file, utf16_without_mark


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


@model_usage.works_for(task_steps.NOTE)
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
    result_fields: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Parse a .srt/.vtt/.txt/.md transcript source, optionally summarizing it.

    Shared by the ``/summarize-transcript-file`` route and the local
    video-source worker (YouTube caption downloads run this in-process instead
    of the hosted HTTP self-call). Raises ``HTTPException`` on invalid input.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in TRANSCRIPT_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"Unsupported transcript file type: {suffix}")

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
        if utf16_without_mark(raw):
            # Read as another encoding it is garbage with zeros in it, and the
            # note would be written from that.
            raise HTTPException(status_code=400, detail=f"没有读出 {filename} 的文字：{UTF16_HINT}。")
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
        # Who writes the note and what the task is called, from an entry
        # that knows (a subtitle file by path: ``submit_subtitle_in_place``).
        **(result_fields or {}),
    }
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

    return await _write_transcript_note(
        task_id_value, client_id, base_result, transcript_text,
        summary_disabled=summary_disabled, route=route, source_filename=source_filename,
        duration=round(parsed.duration, 1), source_file_size_mb=source_file_size_mb,
        deepseek_api_key=deepseek_api_key, openai_api_key=openai_api_key, qwen_api_key=qwen_api_key,
        ai_provider=ai_provider, ai_model=ai_model, note_mode=note_mode, system_prompt=system_prompt,
        prompt_preset=prompt_preset, prompt_preset_label=prompt_preset_label,
    )


async def _write_transcript_note(
    task_id_value: str,
    client_id: Optional[str],
    base_result: dict[str, Any],
    note_transcript_text: str,
    *,
    summary_disabled: bool,
    route: str,
    source_filename: Optional[str],
    duration: float,
    source_file_size_mb: Optional[float],
    speaker_labeled: bool = False,
    before: Optional[dict[str, Any]] = None,
    deepseek_api_key: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    qwen_api_key: Optional[str] = None,
    ai_provider: Optional[str] = None,
    ai_model: Optional[str] = None,
    note_mode: Optional[str] = None,
    system_prompt: Optional[str] = None,
    prompt_preset: Optional[str] = None,
    prompt_preset_label: Optional[str] = None,
) -> dict[str, Any]:
    """Write the note of a task whose transcript is stored (``base_result``)
    and store it with the result: the first run of a transcript file, and a
    subtitle task carried on at its note (``note_from_stored_transcript``).

    ``before`` is the result as the caller read it, when the task was already
    stored before this run: then only what the note changed is written onto
    the result as it is now (``save_result_changes``), so an edit made while
    the model wrote is kept, in the files as in the database: only the note's
    files are written then, as for a recording's note
    (``local_intake_flow._write_text_note_instead``); writing them all again
    put the transcript the note started from back into the transcript files
    and the 过程文件 copy (review of 5d6a580)."""
    loop = asyncio.get_event_loop()
    artifact_parts = {} if before is None else {"parts": ("summary",)}

    def _store(result: dict[str, Any], **fields: Any) -> None:
        if before is None:
            upsert_job(task_id=task_id_value, status="completed", client_id=client_id, result=result, **fields)
            return
        if str((get_job(task_id_value) or {}).get("status") or "") == "cancelled":
            return  # stopped while the model wrote: not brought back
        save_result_changes(task_id_value, before, result, client_id=client_id)
        upsert_job(task_id=task_id_value, status="completed", client_id=client_id, **fields)

    if base_result.get("summary_error"):
        # A note written now clears the failure of the run before.
        base_result = {**base_result, "summary_error": None}
    if summary_disabled:
        log_event(
            task_id=task_id_value,
            event_name="summary_skipped",
            source_type="transcript_file",
            source_filename=source_filename,
            source_duration_seconds=duration,
            source_file_size_mb=source_file_size_mb,
            transcript_length=len(note_transcript_text or ""),
            stage="summary",
            success=True,
            metadata=event_metadata(
                route=route, reason="transcript_only_mode"
            ),
        )
        result = result_for_transcript_only(base_result)
        result = _attach_result_artifacts(task_id_value, result, **artifact_parts)
        _store(
            result,
            stage="done",
            progress=100,
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

    def summarize() -> Any:
        # Bound to the task, so 停下这一步 stops its chapters' and check's
        # requests too (``model_usage.unless_stopping``): stopping this
        # coroutine alone left the thread writing the note to the end.
        with task_stop.bound(task_id_value):
            return summarize_transcript_with_metadata(
                note_transcript_text, speaker_labeled=speaker_labeled, task_id=task_id_value,
                client_id=client_id, **kwargs,
            )

    # A run of its own: a stop of an earlier run does not reach it. One
    # stopped earlier in this run was delivered at an await before this
    # line, so it never gets here.
    task_stop.begin(task_id_value)
    try:
        summary_result = await loop.run_in_executor(None, model_usage.carry(summarize))
        summary_md = summary_result.markdown
        if not summary_md.strip():
            raise ValueError("AI summarization returned empty result")
        log_event(
            task_id=task_id_value,
            event_name="summary_completed",
            source_type="transcript_file",
            source_filename=source_filename,
            source_duration_seconds=duration,
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
            source_duration_seconds=duration,
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
        failed_result = _attach_result_artifacts(task_id_value, failed_result, **artifact_parts)
        _store(
            failed_result,
            stage="done",
            progress=100,
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
    result = _attach_result_artifacts(task_id_value, result, **artifact_parts)
    _store(
        result,
        stage="done",
        progress=100,
        summary_status="completed",
    )
    return result



def stored_transcript_of(job: dict[str, Any]) -> bool:
    """Whether the task already holds its transcript, so carrying it on at the
    note writes the note from it instead of reading the file again."""
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    return bool(str(result.get("transcript_text") or "").strip() or result.get("raw_segments"))


def stored_resume_point(job: dict[str, Any], start_at: str) -> str:
    """Where a task made from a transcript file (a subtitle file by path, a
    link's captions) picks up when carried on from ``start_at``: cut off
    before its transcript was stored, it reads the file again (FETCH); with
    the transcript stored, the note is written from it while the note is not
    done (NOTE); with the note done, only the note it holds is exported
    (FEISHU). Every step after reading the file used to write the note again,
    the Feishu step included: the user's edited note was replaced and nothing
    was exported (review of 5d6a580)."""
    if start_at == task_steps.FETCH or not stored_transcript_of(job):
        return task_steps.FETCH
    if task_steps.status_of(str(job.get("task_id") or ""), task_steps.NOTE) != task_steps.DONE:
        return task_steps.NOTE
    return task_steps.FEISHU


async def export_stored_note(task_id: str, client_id: Optional[str]) -> None:
    """Carry a transcript-file task on at its Feishu step: send the note it
    holds, as the user may have edited it; the model is not asked again.

    The export is the one the task's note took the first time
    (``task_flow.export_agent_note_if_asked``), which takes a recorded error
    for an export already tried, so the failure being carried on is cleared
    first, as a recording's export overwrites it. Waited for, so the step is
    not closed as unfinished under it."""
    from backend.core import task_flow

    job = get_job(task_id, client_id=client_id) or {}
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if result.get("lark_error"):
        save_result_changes(task_id, result, {**result, "lark_error": None}, client_id=client_id)
    if job.get("status") != "completed":
        # Its transcript and note are stored: the task is done but for this.
        upsert_job(task_id=task_id, status="completed", client_id=client_id, stage="done", progress=100)
    export = task_flow.export_agent_note_if_asked(task_id, client_id)
    if export is None:
        task_steps.skip_open(task_id)
        return
    await asyncio.to_thread(export.join)


@model_usage.works_for(task_steps.NOTE)
async def note_from_stored_transcript(
    task_id: str, *, client_id: Optional[str], options: dict[str, Any], route: str,
) -> dict[str, Any]:
    """Write a transcript-file task's note from the transcript it holds, when
    it is carried on at its note (a subtitle file by path, a link's captions).

    Reading the file again rebuilt the whole result from it: what the user
    had edited in the transcript, and the glossary step's record of it, were
    replaced by the file's text, and a file changed on disk since gave a note
    from other words (review of 2026-11-25). A recording carried on at its
    note is not transcribed again either. The note reads the transcript as
    every text-model note does (``note_prompt.transcript_for_note``).

    The task stays completed while the note is written, as a recording
    writing its note after its transcript does (``mark_note_running``):
    marked running, a stop made it a cancelled task, listed 已取消, and a link
    sent again no longer found it as a duplicate (review of 5d6a580)."""
    from backend.core import local_intake_flow, note_prompt

    local_intake_flow.mark_note_running(task_id, client_id)
    job = get_job(task_id, client_id=client_id) or {}
    before = dict(job.get("result") or {})
    note_input, speaker_labeled = note_prompt.transcript_for_note(before)
    return await _write_transcript_note(
        task_id, client_id, before, note_input,
        summary_disabled=truthy(options.get("skip_summary")), route=route,
        source_filename=job.get("source_filename"),
        duration=round(float(before.get("audio_duration_seconds") or 0), 1),
        source_file_size_mb=job.get("source_file_size_mb"), speaker_labeled=speaker_labeled, before=before,
        ai_provider=options.get("ai_provider"), ai_model=options.get("ai_model"),
        note_mode=options.get("note_mode"), system_prompt=options.get("system_prompt"),
        prompt_preset=options.get("prompt_preset"), prompt_preset_label=options.get("prompt_preset_label"),
    )

# ── a subtitle file on this machine, by path ────────────────────────────────
#
# Requirement (owner, 2026-11-25; found 2026-10-29 sorting 「1.短视频」): a
# subtitle file with no video beside it gets its note where it is, as a
# recording by path does: an output folder beside it, the marker, the md kept
# in step with Obsidian. Before this it could only be sent as transcript text,
# and the task did not know which folder the file was in.


async def submit_subtitle_in_place(
    source: Path, *, client_id: Optional[str], options: dict[str, Any], route: str, origin: dict[str, Any],
) -> dict[str, Any]:
    """Record the task for a subtitle file read where it is and start it.

    The path goes under ``output_folder.SUBTITLE_INTAKE_KEY``, which only the
    output folder reads: under ``folder_intake`` the flows that play, cut and
    re-queue a recording would take the subtitles for one. Started through the
    queue's runner, so ``cancel_task`` stops it and nothing it does afterwards
    brings a cancelled or deleted task back; like a link's captions it does
    not wait its turn, as there is nothing to transcribe."""
    from backend.core.output_folder import SUBTITLE_INTAKE_KEY

    # Read before the task exists: a file gone since it was checked, or one
    # too large to read, leaves no queued task behind.
    size_mb = file_size_mb(source.stat().st_size)
    limit_mb = max_transcript_upload_mb()
    if size_mb is not None and size_mb > limit_mb:
        raise HTTPException(status_code=413, detail=f"字幕文件太大：{size_mb} MB，上限是 {limit_mb:g} MB。")
    task_id_value = claim_task_id(None, client_id=client_id)
    title = str(options.get("title") or "").strip() or source.stem
    upsert_job(
        task_id=task_id_value,
        status="queued",
        client_id=client_id,
        stage="queued",
        progress=0,
        source_type="transcript_file",
        source_filename=source.name,
        source_file_size_mb=size_mb,
        metadata=event_metadata(
            route=route,
            raw_title=title,
            display_title=title,
            queue_options=options,
            agent_input_type="subtitle_file",
            **{SUBTITLE_INTAKE_KEY: {**origin, "original_path": str(source)}},
        ),
    )

    await _start_subtitle_run(task_id_value, source, client_id=client_id, options=options, title=title, route=route)
    return {"task_id": task_id_value, "status": "queued", "filename": source.name, "title": title}


def subtitle_in_place_of(job: dict[str, Any]) -> Optional[Path]:
    """The subtitle file a task was made from by path, or None for any other task."""
    from backend.core.output_folder import SUBTITLE_INTAKE_KEY

    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    origin = metadata.get(SUBTITLE_INTAKE_KEY)
    if not isinstance(origin, dict) or not origin.get("original_path"):
        return None
    return Path(str(origin["original_path"]))


async def resume_subtitle_in_place(job: dict[str, Any], *, route: str, start_at: str = task_steps.FETCH) -> bool:
    """Carry a subtitle task by path on after a restart or a stop. Cut off
    while it read the file, it reads it again; at its note with the transcript
    stored, only the note is written, from that transcript, so what the user
    edited in it stays (``note_from_stored_transcript``); at its Feishu step,
    only the note it holds is exported (``stored_resume_point``). The model answers
    the note already got are kept (``model_answers``), so they are not paid
    for again. Raises ``FileNotFoundError`` with the user's words when the
    file is gone."""
    from backend.core.job_store import reopen_job

    source = subtitle_in_place_of(job)
    if source is None or not source.is_file():
        raise FileNotFoundError(f"这个任务的字幕文件已不在原来的位置：{source}，没法接着做；请重新提交这个字幕文件。")
    task_id = str(job.get("task_id") or "")
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    stored = metadata.get("queue_options")
    options = dict(stored) if isinstance(stored, dict) else {}
    title = str(metadata.get("display_title") or options.get("title") or "").strip() or source.stem
    resume_at = stored_resume_point(job, start_at)
    task_steps.reopen_from(task_id, resume_at)
    if not (resume_at != task_steps.FETCH and job.get("status") == "completed"):
        reopen_job(task_id)
    return await _start_subtitle_run(task_id, source, client_id=job.get("client_id"), options=options,
                                     title=title, route=route, resume_at=resume_at)


async def _start_subtitle_run(
    task_id: str, source: Path, *, client_id: Optional[str], options: dict[str, Any], title: str, route: str,
    resume_at: str = task_steps.FETCH,
) -> bool:
    from backend.core.task_flow import RESTART_RESUMED_KEY
    from backend.core.job_store import update_job_metadata
    from backend.core.task_queue import _start_behind_queue

    async def worker(_previous: Any, done: asyncio.Event) -> None:
        # Nothing here uses this machine for long (reading a text file, then
        # the text model's wait), so the task passes its place in line on at
        # once: the next recording goes when the one ahead of this task is
        # done (``task_queue._ChainLink``), not after this note's model call.
        done.set()
        stopped = False
        try:
            await _run_subtitle_in_place(task_id, source, client_id=client_id, options=options,
                                         title=title, route=route, resume_at=resume_at)
        except asyncio.CancelledError:
            stopped = True
            raise
        finally:
            done.set()
            task_steps.close_open(task_id, stopped=stopped)
            update_job_metadata(task_id, {RESTART_RESUMED_KEY: None})

    return await _start_behind_queue(task_id=task_id, client_id=client_id, route=route,
                                     stage="transcript_file", chained_worker=worker)


async def _run_subtitle_in_place(
    task_id: str, source: Path, *, client_id: Optional[str], options: dict[str, Any], title: str, route: str,
    resume_at: str = task_steps.FETCH,
) -> None:
    """Read the subtitle file, store its transcript (and the note, when
    FluentFlow's text model writes it), and fill the folder beside it; carried
    on at its note, only the note, from the transcript the task holds; at its
    Feishu step, only the export (``stored_resume_point``). The agent's note,
    saved later, refreshes that folder as any note does. A failure is
    recorded on the task for ``wait_task``."""
    from backend.core import output_folder, task_flow

    if resume_at == task_steps.FEISHU:
        await export_stored_note(task_id, client_id)
        return
    if resume_at == task_steps.NOTE:
        task_steps.start(task_id, task_steps.NOTE)
        await note_from_stored_transcript(task_id, client_id=client_id, options=options, route=route)
    else:
        if not await _read_subtitle_in_place(task_id, source, client_id=client_id, options=options,
                                             title=title, route=route):
            return
    task_steps.settle(task_id, task_steps.NOTE, task_steps.note_outcome(get_job(task_id, client_id=client_id)))
    task_steps.skip_open(task_id)
    output_folder.schedule_sync(task_id)
    # FluentFlow's model wrote the note: sent to Feishu when the user turned
    # on the automatic export, as the agent's note is once it saves it.
    task_flow.export_agent_note_if_asked(task_id, client_id)


async def _read_subtitle_in_place(
    task_id: str, source: Path, *, client_id: Optional[str], options: dict[str, Any], title: str, route: str,
) -> bool:
    """Read the file, store its transcript and write the note; False when
    that failed (recorded on the task)."""
    task_steps.start(task_id, task_steps.FETCH)
    try:
        raw = await asyncio.to_thread(source.read_bytes)
        # A file named .srt/.vtt with no timed line in it (a bare WEBVTT
        # header, another format) would otherwise be read as plain text and
        # handed to the note writer.
        if not parse_transcript_file(raw, source.name).segments:
            hint = f"：{UTF16_HINT}" if utf16_without_mark(raw) else ""
            raise HTTPException(status_code=400, detail=f"{source.name} 里没有读出带时间的字幕{hint}。")
        task_steps.finish(task_id, task_steps.FETCH, task_steps.DONE)
        if not truthy(options.get("skip_summary")):
            task_steps.start(task_id, task_steps.NOTE)
        await summarize_transcript_source(
            raw=raw,
            filename=source.name,
            client_id=client_id,
            task_id=task_id,
            ai_provider=options.get("ai_provider"),
            ai_model=options.get("ai_model"),
            note_mode=options.get("note_mode"),
            skip_summary=options.get("skip_summary"),
            system_prompt=options.get("system_prompt"),
            prompt_preset=options.get("prompt_preset"),
            prompt_preset_label=options.get("prompt_preset_label"),
            route=route,
            result_fields={"display_title": title, "intelligence": options.get("intelligence")},
        )
    except Exception as exc:  # noqa: BLE001 - recorded on the task, never left running
        if str((get_job(task_id) or {}).get("status") or "") != "failed":
            detail = str(exc.detail) if isinstance(exc, HTTPException) else _friendly_error(exc)
            upsert_job(task_id=task_id, status="failed", client_id=client_id, stage="import",
                       error_reason=detail or "读取字幕文件时出错。")
        return False
    return True
