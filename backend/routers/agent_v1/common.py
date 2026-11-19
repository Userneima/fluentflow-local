"""What the agent routes share: the caller's scope, the task brief and package,
the task page's address (``_page_url``), the hints a task answer carries, and
writing the note for a submitted transcript.

Names the tests replace and more than one route module calls (``get_job``,
``upsert_job``, ``_local_client_scope``, ``log_event``,
``_attach_result_artifacts``, ``summarize_transcript_with_metadata``,
``local_ai_kwargs``) are read here at call time: the route modules call them
as ``common.X``, so one patch on this module reaches every route.
"""

from __future__ import annotations

import asyncio
import logging
import math
from urllib.parse import quote
from typing import Any, Optional

from fastapi import HTTPException, Request

from backend.core import agent_correction, agent_cut, model_usage, note_section_times, note_versions, task_activity, task_steps
from backend.core.ai_summarizer import summarize_transcript_with_metadata
from backend.core.chapter_coverage import bind_chapter_coverage_time_ranges
from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event  # noqa: F401 - route modules call common.log_event
from backend.core.job_store import NOTE_BASIS_FIELDS, get_job, upsert_job
from backend.core.local_agent_package import build_agent_task_package
from backend.core.local_entry_guards import friendly_error, local_ai_kwargs
from backend.core.local_request_scope import request_client_id
from backend.core.result_artifacts import _attach_result_artifacts
from backend.core.storage_paths import _artifact_storage_dir

# Named after the module these lines came from, so log filters keep matching.
logger = logging.getLogger("backend.routers.local_agent")


_ROUTE = "/agent/v1/tasks"


def _truthy_json(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "on"}


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


_TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _note_pending(job: dict[str, Any]) -> bool:
    """Whether a task's note is still to come: being written, or waiting for
    the cut before it. A task reads ``completed`` as soon as its transcript is
    stored, and an agent that stopped waiting there would read an empty note,
    so every "is it done" answer on this surface goes through
    ``task_activity``, which reads the task's steps."""
    current = task_activity.activity(job)
    return current.busy and task_steps.NOTE in current.open_steps


def _cut_running(job: dict[str, Any]) -> bool:
    """Whether the recording is being cut after its transcript was stored."""
    current = task_activity.activity(job)
    return current.busy and task_steps.CUT in current.open_steps


def _task_done(job: dict[str, Any]) -> bool:
    return job.get("status") in _TERMINAL_TASK_STATUSES and not task_activity.activity(job).busy


def _task_package_response(job: dict[str, Any]) -> dict[str, Any]:
    package = build_agent_task_package(job, artifact_root=_artifact_storage_dir())
    package["note_pending"] = _note_pending(job)
    # What each step's model calls used and cost, and the whole task's total.
    package["model_usage"] = model_usage.for_task(str(job.get("task_id") or ""))
    # How long each step took, the queue and the agent's own steps included.
    task_id = str(job.get("task_id") or "")
    package["step_timings"] = task_steps.timings(job, task_steps.steps_of([task_id]).get(task_id, []))
    # The version of the note in this package: what a save of an edit to it names.
    package["note_version"] = (job.get("result") or {}).get("note_version")
    package["note_alignment"] = note_section_times.brief(job.get("result") or {})
    return package


def _task_brief(job: dict[str, Any]) -> dict[str, Any]:
    """What an answer says about the task: enough to decide the next step.

    Every answer that changes a task used to carry the whole package, about
    30,000 characters with the note in it, so fixing one note sent it to the
    agent three times. The package itself is one ``get_task_package`` away.
    """
    package = _task_package_response(job)
    note = package.get("note") or {}
    task = package.get("task") or {}
    cut_media_note = package.get("cut_media_note") or {}
    task_id = task.get("task_id") or job.get("task_id")
    return {
        "task_id": task_id,
        "status": task.get("status"),
        "title": package.get("title"),
        "note": {
            "status": note.get("status"),
            "chars": note.get("markdown_chars"),
            "version": (job.get("result") or {}).get("note_version"),
            "pending": package.get("note_pending"),
            "written_by": cut_media_note.get("channel"),
        },
        "output_folder": package.get("output_folder"),
        "next_actions": package.get("next_actions") or [],
        # Busy or not, on which step, which steps failed or were stopped, and
        # where ``resume_task`` would carry it on from.
        "activity": task_activity.activity(job).as_dict(),
        "package_url": f"/agent/v1/tasks/{task_id}/package",
    }


def _page_url(request: Request, task_id: str) -> str:
    """The address of the task's page in FluentFlow: its note and playback.

    Agents guessed it from the front end's routes: on 2026-11-19 one opened
    ``/?task=<id>``, and the root redirect dropped the query, so the owner saw
    the start page instead of the lesson he asked for.
    """
    return f"{str(request.base_url).rstrip('/')}/editor?task={quote(task_id, safe='')}"


def _job_for_request(request: Request, task_id: str) -> dict[str, Any]:
    job = get_job(task_id, client_id=_local_client_scope(request))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _result_changed(task_id: str, client_id: Optional[str], initial_result: Any) -> bool:
    """Whether the note or the transcript it rests on moved since
    ``initial_result``; other fields may change during a rewrite."""
    latest = get_job(task_id, client_id=client_id)
    if latest is None:
        return True
    now = latest.get("result") if isinstance(latest.get("result"), dict) else {}
    then = initial_result if isinstance(initial_result, dict) else {}
    return any(now.get(key) != then.get(key) for key in NOTE_BASIS_FIELDS)


def _bounded_finite_float(
    value: Any,
    *,
    field: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw_value = default if value is None else value
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{field} must be a number") from exc
    if not math.isfinite(parsed):
        raise HTTPException(status_code=422, detail=f"{field} must be a finite number")
    return min(max(parsed, minimum), maximum)


async def _write_transcript_task(
    task_id_value: str,
    client_id: Optional[str],
    title: str,
    transcript: str,
    options: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Write the note for a submitted transcript and store the finished task.

    Raises HTTPException after recording the failure on the task.
    """
    skip_summary = _truthy_json(options.get("skip_summary"))
    result: dict[str, Any] = {
        "task_id": task_id_value,
        "filename": title,
        "display_title": title,
        "transcript_text": transcript,
        "transcript_text_preview": transcript[:200],
        "source": "agent_transcript",
        "intelligence": options.get("intelligence"),
    }
    for key in ("prompt_preset", "prompt_preset_label"):
        value = str(options.get(key) or "").strip()
        if value:
            result[key] = value
    summary_status = "skipped"
    if skip_summary:
        result.update({"summary_skipped": True, "summary_status": "skipped"})
    else:
        kwargs = local_ai_kwargs(
            deepseek_api_key=payload.get("deepseek_api_key"),
            openai_api_key=payload.get("openai_api_key"),
            qwen_api_key=payload.get("qwen_api_key"),
            ai_provider=options.get("ai_provider") or payload.get("ai_provider"),
            ai_model=options.get("ai_model") or payload.get("ai_model"),
            # The note instructions may come as an option (MCP, the page's
            # vocabulary) or top level (older Agent API callers).
            system_prompt=options.get("system_prompt") or payload.get("system_prompt"),
            note_mode=options.get("note_mode"),
        )
        loop = asyncio.get_running_loop()
        try:
            summary_result = await loop.run_in_executor(
                None,
                model_usage.carry(
                    lambda: summarize_transcript_with_metadata(
                        transcript, task_id=task_id_value, client_id=client_id, **kwargs,
                    ),
                    task_id=task_id_value, step=task_steps.NOTE,
                ),
            )
        except Exception as exc:
            detail = friendly_error(exc)
            upsert_job(
                task_id=task_id_value,
                status="failed",
                client_id=client_id,
                stage="summary",
                source_type="agent_transcript",
                source_filename=title,
                summary_status="failed",
                error_reason=detail,
                metadata=event_metadata(route=_ROUTE, agent_input_type="transcript"),
            )
            raise HTTPException(status_code=500, detail=detail) from exc
        result.update({
            "summary_markdown": summary_result.markdown,
            "summary_status": "completed",
            "summary_skipped": False,
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
        })
        result = bind_chapter_coverage_time_ranges(result)
        note_versions.declare(result, note_versions.GENERATED)
        summary_status = "completed"
    try:
        result = _attach_result_artifacts(task_id_value, result)
        upsert_job(
            task_id=task_id_value,
            status="completed",
            client_id=client_id,
            stage="done",
            progress=100,
            source_type="agent_transcript",
            source_filename=title,
            summary_status=summary_status,
            result=result,
            metadata=event_metadata(route=_ROUTE, agent_input_type="transcript"),
        )
    except Exception as exc:
        detail = friendly_error(exc)
        upsert_job(
            task_id=task_id_value,
            status="failed",
            client_id=client_id,
            stage="finalize",
            progress=100,
            source_type="agent_transcript",
            source_filename=title,
            summary_status="failed",
            error_reason=detail,
            metadata=event_metadata(route=_ROUTE, agent_input_type="transcript"),
        )
        raise HTTPException(status_code=500, detail=detail) from exc
    return get_job(task_id_value, client_id=client_id) or {"task_id": task_id_value, "result": result}


async def _write_transcript_task_in_background(
    task_id_value: str,
    client_id: Optional[str],
    title: str,
    transcript: str,
    options: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    try:
        await _write_transcript_task(task_id_value, client_id, title, transcript, options, payload)
    except HTTPException:
        # Already recorded on the task as failed; wait_task reports it.
        return
    except Exception:  # noqa: BLE001 - the task must not be left running
        logger.exception("transcript note failed for %s", task_id_value)
        upsert_job(task_id=task_id_value, status="failed", client_id=client_id, stage="summary",
                   summary_status="failed", error_reason="写笔记时出错，详见本机日志。")


AGENT_NOTE_NEXT = (
    "The cut is decided and the cut video is rendering. Start the note now: call get_agent_note_material "
    "(in a subagent with a fresh context when your host has them); it hands over the transcript on the cut "
    "video's clock and frames from the matching moments, exactly as after the render. Save with "
    "save_agent_note as usual. wait_task will not stop for this again; call it to wait for the render."
)


ALIGN_NEXT = (
    "FluentFlow does not match the sections of a note an agent wrote to playback times; that is yours. "
    "Align them with get_agent_alignment_material and save_agent_alignment (in a subagent with a fresh "
    "context when your host has them), from the material, not from memory."
)
ALIGN_AFTER_DONE = " Do it once wait_task says done=true; while the task works the material is refused."


def _correction_next(job: dict[str, Any]) -> Optional[str]:
    """What to say when correcting the transcript or translating its subtitles
    was left to the agent; reading the picture's subtitles starts now, so
    the material is ready by the time the agent asks for it."""
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    left = agent_correction.pending(result)
    if agent_correction.SUBTITLES in left:
        agent_correction.start_reading(str(job.get("task_id") or ""), result)
    return agent_correction.next_hint(result)


def _alignment_next(job: dict[str, Any]) -> Optional[str]:
    """What to say when a note the agent wrote has sections without a time."""
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if not note_section_times.uses_agent(result):
        return None
    if note_section_times.status(result)["state"] not in {"missing", "partial", "failed"}:
        return None
    return ALIGN_NEXT + (ALIGN_AFTER_DONE if task_activity.activity(job).busy else "")


AGENT_CUT_NEXT = (
    "FluentFlow is waiting for you to judge where to cut this recording. Hand it to a new subagent with a "
    "fresh context when your host has them (give it the task_id): it calls get_agent_cut_material, judges, "
    "sends the decision with save_agent_cut and the report with save_agent_cut_report, and tells you the "
    "to_judge count. The material is long; read into this conversation, every later turn resends it. "
    "If nobody answers, FluentFlow judges it itself once the time limit passes."
)


def _agent_cut_brief(job: dict[str, Any]) -> Optional[dict[str, Any]]:
    brief = agent_cut.summary(job.get("result"))
    if brief and brief.get("status") == agent_cut.WAITING:
        brief["next"] = AGENT_CUT_NEXT
    return brief


def _note_moved(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "stale", "message": str(exc)})


def _current_versions(task_id: str, request: Request) -> dict[str, Any]:
    job = get_job(task_id, client_id=_local_client_scope(request)) or {}
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    return {"note_version": result.get("note_version")}
