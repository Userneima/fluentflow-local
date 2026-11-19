"""The agent route that rewrites a task's note
(``POST /tasks/{task_id}/note/regenerate``)."""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request

from backend.core.chapter_coverage import bind_chapter_coverage_time_ranges
from backend.core.event_context import event_metadata
from backend.core.job_store import finalize_note_if_unchanged
from backend.core.local_config import resolve_secret
from backend.core.result_schema import FRAME_NOTE_WRITTEN_FROM
from backend.core.local_entry_guards import friendly_error
from backend.core import note_prompt, model_usage, task_steps
from backend.core import note_versions, visual_note_job
from backend.core.local_request_scope import require_local_agent_access
from backend.core.task_reruns import start_local_visual_note
from backend.routers.agent_v1 import common
from backend.routers.agent_v1.common import (
    _job_for_request,
    _note_moved,
    _result_changed,
    _task_brief,
)

router = APIRouter(prefix="/agent/v1", dependencies=[Depends(require_local_agent_access)])

def _note_written_from_frames(result: dict[str, Any]) -> bool:
    """Whether the task's current note is the one Claude wrote from the frames."""
    if result.get("summary_written_from") in FRAME_NOTE_WRITTEN_FROM:
        return True
    state = visual_note_job.visual_note_state(result)
    return state.get("status") == visual_note_job.STATUS_COMPLETED and bool(state.get("promoted"))


def _stored_note_setting(job: dict[str, Any], payload: dict[str, Any], key: str) -> Optional[str]:
    """The caller's value, else the one the task was submitted with.

    "Regenerate" means the same note settings again unless the caller says
    otherwise; without this an agent's regenerate quietly dropped the prompt
    and note mode the user had chosen for the task.
    """
    value = str(payload.get(key) or "").strip()
    if value:
        return value
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    stored = metadata.get("queue_options") if isinstance(metadata.get("queue_options"), dict) else {}
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    return str(stored.get(key) or result.get(key) or "").strip() or None


@router.post("/tasks/{task_id}/note/regenerate")
async def regenerate_agent_task_note(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Rewrite a finished task's note, by the same writer that wrote it.

    A note Claude wrote from the frames is rewritten the way the editor rewrites
    it — the visual note run, in the background — whenever that run can work for
    this task; the answer then says ``writer: "claude_frames"`` and the caller
    follows with ``wait_task``/``get_task_package``. Otherwise the configured text
    model rewrites it from the transcript, here, with the prompt and note mode
    the caller passed or the task was submitted with.

    Either way the previous note is kept and can be put back for free
    (``POST /tasks/{id}/visual-note`` with ``restore_previous_note``). A failed
    rewrite leaves the task completed with its note untouched and says so in
    ``summary_status``/``summary_error``.
    """
    payload = payload or {}
    client_id = common._local_client_scope(request)
    job = _job_for_request(request, task_id)
    initial_result = deepcopy(job.get("result"))
    result = dict(initial_result or {})
    # A named note version is checked now; the text rewrite below is written
    # only while the note is still the one read here, the frame rewrite checks
    # it again when it replaces the note.
    try:
        visual_note_job.check_base(result, payload.get("base_version"))
    except visual_note_job.NoteMovedError as exc:
        raise _note_moved(exc) from exc

    frame_note_unavailable: Optional[str] = None
    if _note_written_from_frames(result):
        described = visual_note_job.describe(
            task_id, job, api_key=resolve_secret(None, "anthropic_api_key")
        )
        if described["eligible"]:
            accepted = start_local_visual_note(
                request, task_id, background_tasks,
                {"replace_note": True, "base_version": payload.get("base_version")},
            )
            return {
                "ok": True,
                "task_id": task_id,
                "accepted": True,
                "writer": "claude_frames",
                "model": accepted.get("model"),
                "previous_note_restorable": True,
                "visual_note": accepted,
                "package_url": f"/agent/v1/tasks/{task_id}/package",
                "task": _task_brief(_job_for_request(request, task_id)),
            }
        frame_note_unavailable = str(described.get("reason") or "") or None

    transcript_source = (
        "corrected_transcript"
        if str(result.get("corrected_transcript_text") or "").strip()
        else "transcript_text"
    )
    # What the first note read, speaker labels included (``note_prompt``,
    # 10-18 review, item 6).
    transcript, speaker_labeled = note_prompt.transcript_for_note(result)
    transcript = transcript.strip()
    if not transcript:
        raise HTTPException(status_code=400, detail="No transcript available for note regeneration")

    system_prompt = _stored_note_setting(job, payload, "system_prompt")
    note_mode = _stored_note_setting(job, payload, "note_mode")
    prompt_preset = _stored_note_setting(job, payload, "prompt_preset")
    prompt_preset_label = _stored_note_setting(job, payload, "prompt_preset_label")
    kwargs = common.local_ai_kwargs(
        deepseek_api_key=payload.get("deepseek_api_key"),
        openai_api_key=payload.get("openai_api_key"),
        qwen_api_key=payload.get("qwen_api_key"),
        ai_provider=payload.get("ai_provider"),
        ai_model=payload.get("ai_model"),
        system_prompt=system_prompt,
        note_mode=note_mode,
    )
    route = "/agent/v1/tasks/{task_id}/note/regenerate"
    conflict = "笔记在生成期间已被修改，本次生成结果未覆盖你的编辑。"
    started_at = time.perf_counter()
    try:
        loop = asyncio.get_running_loop()
        summary_result = await loop.run_in_executor(
            None,
            model_usage.carry(
                lambda: common.summarize_transcript_with_metadata(
                    transcript, speaker_labeled=speaker_labeled, task_id=task_id, client_id=client_id, **kwargs,
                ),
                task_id=task_id, step=task_steps.NOTE,
            ),
        )
        if not str(summary_result.markdown or "").strip():
            # Writing an empty note through would replace the user's note with
            # nothing and call that completed.
            raise RuntimeError("文本模型没有返回笔记内容，原来的笔记保持不变。")
    except Exception as exc:
        detail = friendly_error(exc)
        if _result_changed(task_id, client_id, initial_result):
            raise HTTPException(status_code=409, detail=conflict) from exc
        # The task itself is still finished, with its transcript and its old
        # note; only this rewrite failed. Marking the whole task failed hid a
        # good transcript behind a failure badge.
        result.update({
            "summary_status": "failed",
            "summary_error": detail,
            "summary_skipped": False,
        })
        updated = finalize_note_if_unchanged(
            task_id,
            initial_result,
            result,
            status=str(job.get("status") or "completed"),
            client_id=client_id,
            stage=str(job.get("stage") or "done"),
            progress=job.get("progress"),
            summary_status="failed",
            error_reason=job.get("error_reason"),
        )
        if updated is None:
            raise HTTPException(status_code=409, detail=conflict) from exc
        common.log_event(
            task_id=task_id,
            event_name="agent_note_regenerated",
            source_type=job.get("source_type"),
            source_filename=job.get("source_filename"),
            transcript_length=len(transcript),
            stage="summary_regenerate",
            duration_seconds=round(time.perf_counter() - started_at, 3),
            success=False,
            error_reason=detail,
            metadata=event_metadata(
                route=route, raw_error=str(exc),
                note_generation_transcript_source=transcript_source,
            ),
        )
        raise HTTPException(status_code=500, detail=detail) from exc

    if _result_changed(task_id, client_id, initial_result):
        raise HTTPException(status_code=409, detail=conflict)
    # Keep the note being replaced where the page's restore switch looks for it.
    previous_note_restorable = bool(str(result.get("summary_markdown") or "").strip())
    result["visual_note"] = visual_note_job.record_replaced_note(result, replaced_by="text_model")
    result.update({
        "summary_markdown": summary_result.markdown,
        "summary_status": "completed",
        "summary_error": None,
        "summary_skipped": False,
        "summary_edited": False,
        "summary_original_markdown": None,
        # The note is now the text model's; an earlier frame-note stamp would
        # make the page describe it as Claude reading the frames.
        "summary_written_from": note_prompt.TEXT_REGENERATION,
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
        "prompt_preset": prompt_preset,
        "prompt_preset_label": prompt_preset_label,
    })
    result = bind_chapter_coverage_time_ranges(result)
    note_versions.declare(result, note_versions.REWRITE)
    result = common._attach_result_artifacts(task_id, result)
    updated = finalize_note_if_unchanged(
        task_id,
        initial_result,
        result,
        status="completed",
        client_id=client_id,
        stage="done",
        progress=100,
        summary_status="completed",
        error_reason=None,
    )
    if updated is None:
        latest = common.get_job(task_id, client_id=client_id)
        latest_result = latest.get("result") if latest else None
        if isinstance(latest_result, dict):
            common._attach_result_artifacts(task_id, latest_result)
        raise HTTPException(status_code=409, detail=conflict)
    common.log_event(
        task_id=task_id,
        event_name="agent_note_regenerated",
        source_type=job.get("source_type"),
        source_filename=job.get("source_filename"),
        transcript_length=len(transcript),
        summary_length=len(summary_result.markdown or ""),
        stage="summary_regenerate",
        duration_seconds=round(time.perf_counter() - started_at, 3),
        success=True,
        metadata=event_metadata(
            route=route, note_generation_transcript_source=transcript_source,
            frame_note_unavailable_reason=frame_note_unavailable,
        ),
    )
    return {
        "ok": True,
        "task_id": task_id,
        "writer": "text_model",
        "provider": kwargs.get("provider"),
        "model": kwargs.get("model"),
        "previous_note_restorable": previous_note_restorable,
        **({"frame_note_unavailable_reason": frame_note_unavailable} if frame_note_unavailable else {}),
        "task": _task_brief(updated),
    }
