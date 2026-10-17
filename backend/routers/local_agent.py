"""Local-edition Agent API (``/agent/v1``).

Same task surface as the hosted Agent API — submit, inspect, wait, diagnose,
retry, regenerate, export — with local semantics: the single local event hub
and job store, the local entry guards (atomic task ids, strict AI keys),
local-owner Feishu export routes only, and no accounts, quota, or
desktop-sync. Per the design contract the whole surface is DISABLED until the
user configures a local access token (``FLUENTFLOW_ACCESS_TOKEN``); the
loopback/session boundary itself belongs to the composition-root unit.

Classification note: ``local_ready``. Video-link submission delegates to the
local video-source router and therefore remains inside the local/shared graph.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request

from backend.core.ai_summarizer import summarize_transcript_with_metadata
from backend.core.chapter_coverage import bind_chapter_coverage_time_ranges
from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.job_store import (
    append_job_result_list_item,
    finalize_job_result_if_unchanged,
    get_job,
    list_jobs,
    upsert_job,
)
from backend.core.lark_cli_exporter import export_markdown_via_lark_cli
from backend.core.lark_exporter import export_markdown_to_lark
from backend.core.local_agent_package import build_agent_task_package, note_generation_diagnosis
from backend.core.local_config import get_preference, resolve_secret
from backend.core.result_schema import FRAME_NOTE_WRITTEN_FROM
from backend.core.local_entry_guards import claim_task_id, friendly_error, local_ai_kwargs
from backend.core import agent_note, debreath_job, local_folder_intake, note_feedback, note_pdf, note_skills, visual_note_job
from backend.core.local_keyframe_provider import extract_keyframes
from backend.core import speaker_diarization as speaker_diarization_core
from backend.core.local_request_scope import (
    request_client_id,
    request_is_localhost,
    require_local_agent_access,
)
from backend.core.note_title import resolve_lark_doc_title
from backend.core.result_artifacts import _attach_result_artifacts
from backend.core.storage_paths import _artifact_storage_dir
from backend.core.edition_identity import INTAKE_REJECTION
from backend.routers.local_feishu_export import _local_lark_export_target, _record_export_on_task
from backend.routers.local_job_debreath import start_local_debreath
from backend.routers.local_job_visual_note import start_local_visual_note
from backend.routers.local_processing import (
    local_path_options,
    queue_is_busy,
    queue_local_media_file,
    retry_for_request,
)
from backend.routers.local_video_sources import allow_duplicate_requested, submit_video_source_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent/v1", dependencies=[Depends(require_local_agent_access)])

_ROUTE = "/agent/v1/tasks"


def _truthy_json(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "on"}


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


_TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})
_TERMINAL_NOTE_STATUSES = frozenset({"completed", "failed", "skipped"})
# How long a just-completed task whose note was deferred to the frame note may
# still show the pipeline's "skipped" before the note step marks it pending.
# That hand-over takes well under a second; the bound only stops a task whose
# note step never started (the service stopped in between) from holding an
# agent forever.
_DEFERRED_NOTE_HANDOVER_SECONDS = 120


def _seconds_since(stamp: Any) -> float | None:
    try:
        moment = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - moment).total_seconds()


def _deferred_note_about_to_start(job: dict[str, Any], result: dict[str, Any]) -> bool:
    """The pipeline stored the transcript as "skipped" and the note step has not
    marked it pending yet: the note is owed, not skipped."""
    if not result.get("note_deferred_to_visual_note"):
        return False
    if str(result.get("summary_markdown") or "").strip():
        return False
    if not (result.get("transcript_text") or result.get("raw_segments")):
        return False
    age = _seconds_since(job.get("updated_at"))
    return age is not None and age < _DEFERRED_NOTE_HANDOVER_SECONDS


def _note_pending(job: dict[str, Any]) -> bool:
    """Whether a completed task's note is still being written.

    A task turns ``completed`` as soon as its transcript is stored; the note
    is written after that, from the cut media. An agent that stopped waiting
    at ``completed`` would read an empty note, so every "is it done" answer on
    this surface goes through here.

    A running note rewrite wins over everything (a redo keeps the old note's
    ``completed`` while the new one is written). Otherwise the note's own
    terminal status wins over the job's stage, because the startup recovery
    fails a stranded note in the result without moving the job's ``note``
    stage, and that task must not read as pending forever.
    """
    if str(job.get("status") or "") != "completed":
        return False
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if visual_note_job.is_running(result):
        return True
    summary_status = str(result.get("summary_status") or job.get("summary_status") or "").lower()
    if summary_status == "skipped" and (
        _deferred_note_about_to_start(job, result)
        or (_cut_running(job) and result.get("note_deferred_to_visual_note") and not str(result.get("summary_markdown") or "").strip())
    ):
        return True
    if summary_status in _TERMINAL_NOTE_STATUSES:
        return False
    if summary_status == "pending":
        return True
    return str(job.get("stage") or "").lower() == "note"


def _cut_running(job: dict[str, Any]) -> bool:
    """Whether the recording is being cut after its transcript was stored
    (the survey-first flow: the model's judgement, then the render). The task
    reads ``completed`` by then and the note waits for the cut, and a render
    can go minutes without touching the task, so this is asked directly
    rather than inferred from how recently the task changed."""
    if str(job.get("status") or "") != "completed":
        return False
    return debreath_job.is_running(job.get("result") if isinstance(job.get("result"), dict) else {})


def _task_done(job: dict[str, Any]) -> bool:
    return job.get("status") in _TERMINAL_TASK_STATUSES and not _note_pending(job) and not _cut_running(job)


def _task_package_response(job: dict[str, Any]) -> dict[str, Any]:
    package = build_agent_task_package(job, artifact_root=_artifact_storage_dir())
    package["note_pending"] = _note_pending(job)
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
            "pending": package.get("note_pending"),
            "written_by": cut_media_note.get("channel"),
        },
        "output_folder": package.get("output_folder"),
        "next_actions": package.get("next_actions") or [],
        "package_url": f"/agent/v1/tasks/{task_id}/package",
    }


def _job_for_request(request: Request, task_id: str) -> dict[str, Any]:
    job = get_job(task_id, client_id=_local_client_scope(request))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _result_changed(task_id: str, client_id: Optional[str], initial_result: Any) -> bool:
    latest = get_job(task_id, client_id=client_id)
    return latest is None or deepcopy(latest.get("result")) != initial_result


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
                lambda: summarize_transcript_with_metadata(transcript, **kwargs),
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


@router.post("/tasks")
async def create_agent_task(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    input_text = str(payload.get("input") or payload.get("url") or "").strip()
    transcript = str(payload.get("transcript_text") or "").strip()
    input_type = str(payload.get("input_type") or "").strip().lower()
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    client_id = _local_client_scope(request)
    local_path = str(payload.get("path") or payload.get("local_path") or "").strip()

    if local_path and input_type in {"", "local_path", "local_file", "local_media"}:
        # A recording that stays where it is. This exists because every other entry
        # required the file to come to FluentFlow — as an upload, a link, or a
        # transcript — and a folder of recordings on this machine matched none of
        # them, so the work got done by scripts that bypassed the product entirely.
        #
        # Localhost only, for the same reason the page entry is: a server that
        # accepts a filesystem path reads its own disk on someone else's request.
        if not request_is_localhost(request):
            raise HTTPException(status_code=403, detail="只有本机能按路径处理文件。")
        try:
            source = local_folder_intake.resolve_media_file(local_path)
        except local_folder_intake.FolderIntakeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # The page entry's queue helper, not a second copy of it: preflight, the task
        # row, ownership and the terminal-state guarantee all live in there, and a
        # parallel implementation is how one entry ends up missing a guard the other
        # one has.
        options_for_path, duration_limit = local_path_options(options)
        # The title the agent passed names the task, as it does for a link; the
        # queue helper reads it from the options (found dropped 2026-10-13).
        title = str(payload.get("title") or "").strip()
        if title:
            options_for_path = {**options_for_path, "title": title}
        queued = await queue_local_media_file(
            source,
            client_id=client_id,
            options=options_for_path,
            duration_limit_seconds=duration_limit,
            route=_ROUTE,
            origin={"chosen_with": "agent_api", "folder": str(source.parent)},
        )
        task_id_value = queued.get("task_id")
        return {
            "ok": True,
            "task_id": task_id_value,
            "status": queued.get("status"),
            "package_url": f"/agent/v1/tasks/{task_id_value}/package",
            "job": queued,
        }

    if input_text and input_type in {"", "video_link", "url", "share_text"}:
        # Same speaker-separation default as a submission by path: an agent
        # should not get speakers for a recording on disk and none for a link.
        link_options = dict(options)
        if link_options.get("speaker_diarization") is None and speaker_diarization_core.default_on_for_agents():
            link_options["speaker_diarization"] = "true"
        job = await submit_video_source_job(
            input_text=input_text,
            title=str(payload.get("title") or "").strip(),
            raw_options=link_options,
            client_id=client_id,
            route=_ROUTE,
            extra_metadata={"agent_input_type": "video_link"},
            allow_duplicate=allow_duplicate_requested(payload),
        )
        task_id_value = job["task_id"]
        return {
            "ok": True,
            "task_id": task_id_value,
            "status": job.get("status"),
            "duplicate_of_active": bool(job.get("duplicate_of_active")),
            "package_url": f"/agent/v1/tasks/{task_id_value}/package",
            "job": job,
        }

    if transcript and input_type in {"", "transcript", "transcript_text"}:
        task_id_value = claim_task_id(
            str(payload.get("task_id") or "").strip() or None, client_id=client_id
        )
        title = str(payload.get("title") or "Transcript").strip()
        if not _truthy_json(payload.get("wait", True)):
            # The caller waits with wait_task, like every other submission. Writing
            # a long note takes minutes, and a request held open that long timed
            # out in MCP clients, which then submitted the same transcript again.
            upsert_job(
                task_id=task_id_value,
                status="running",
                client_id=client_id,
                stage="summary",
                progress=50,
                source_type="agent_transcript",
                source_filename=title,
                summary_status="pending",
                metadata=event_metadata(route=_ROUTE, agent_input_type="transcript"),
            )
            background_tasks.add_task(
                _write_transcript_task_in_background,
                task_id_value, client_id, title, transcript, options, payload,
            )
            return {
                "ok": True,
                "task_id": task_id_value,
                "status": "running",
                "package_url": f"/agent/v1/tasks/{task_id_value}/package",
            }
        job = await _write_transcript_task(task_id_value, client_id, title, transcript, options, payload)
        return {
            "ok": True,
            "task_id": task_id_value,
            "status": "completed",
            "package_url": f"/agent/v1/tasks/{task_id_value}/package",
            "task": _task_brief(job),
        }

    # Name this edition in the refusal. An input list alone leaves the caller unable
    # to tell a missing capability from the wrong backend answering.
    raise HTTPException(status_code=400, detail=INTAKE_REJECTION)


def _note_title(markdown: str) -> str:
    for line in (markdown or "").splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


@router.get("/tasks")
def list_agent_tasks(request: Request, query: str = "", limit: int = 20) -> dict[str, Any]:
    """Recent tasks, or the ones whose title, note title or file name contains ``query``.

    How an agent finds the task behind a screenshot of a note: the screenshot
    shows the note's own title, which is often not the file's name, so both are
    searched and both are returned for the agent to judge.
    """
    needle = query.strip().lower()
    found = []
    for job in list_jobs(limit=None, client_id=_local_client_scope(request), include_result=True):
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        note = str(result.get("summary_markdown") or "")
        title = str(result.get("display_title") or result.get("filename") or job.get("source_filename") or "")
        note_title = _note_title(note)
        haystack = " ".join([title, note_title, str(job.get("source_filename") or "")]).lower()
        if needle and needle not in haystack:
            continue
        found.append({
            "task_id": job.get("task_id"),
            "title": title,
            "note_title": note_title,
            "created_at": job.get("created_at"),
            "status": job.get("status"),
            "has_note": bool(note.strip()),
        })
        if len(found) >= max(1, min(int(limit or 20), 100)):
            break
    return {"ok": True, "tasks": found}


@router.get("/tasks/{task_id}")
def get_agent_task(request: Request, task_id: str) -> dict[str, Any]:
    job = _job_for_request(request, task_id)
    return {
        "ok": True,
        "task": {
            "task_id": job.get("task_id"),
            "status": job.get("status"),
            "stage": job.get("stage"),
            "progress": job.get("progress"),
            "summary_status": (job.get("result") or {}).get("summary_status") or job.get("summary_status"),
            "note_pending": _note_pending(job),
            "cut_running": _cut_running(job),
            "done": _task_done(job),
        },
        "package_url": f"/agent/v1/tasks/{task_id}/package",
    }


@router.get("/tasks/{task_id}/package")
def get_agent_task_package(request: Request, task_id: str) -> dict[str, Any]:
    return _task_package_response(_job_for_request(request, task_id))


@router.post("/tasks/{task_id}/wait")
async def wait_agent_task(
    request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None)
) -> dict[str, Any]:
    payload = payload or {}
    timeout_seconds = _bounded_finite_float(
        payload.get("timeout_seconds"),
        field="timeout_seconds",
        default=30,
        minimum=0,
        maximum=60,
    )
    poll_interval = _bounded_finite_float(
        payload.get("poll_interval_seconds"),
        field="poll_interval_seconds",
        default=2,
        minimum=0.5,
        maximum=10,
    )
    deadline = time.monotonic() + timeout_seconds
    while True:
        job = _job_for_request(request, task_id)
        if _task_done(job):
            brief = _task_brief(job)
            return {
                "ok": True,
                "done": True,
                "note_status": brief["note"]["status"],
                "task": brief,
            }
        if time.monotonic() >= deadline:
            return {
                "ok": True,
                "done": False,
                "note_pending": _note_pending(job),
                "cut_running": _cut_running(job),
                "task": {
                    "task_id": job.get("task_id"),
                    "status": job.get("status"),
                    "stage": job.get("stage"),
                    "progress": job.get("progress"),
                },
                "package_url": f"/agent/v1/tasks/{task_id}/package",
            }
        await asyncio.sleep(poll_interval)


@router.get("/tasks/{task_id}/diagnosis")
def get_agent_task_diagnosis(request: Request, task_id: str) -> dict[str, Any]:
    job = _job_for_request(request, task_id)
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    return {
        "ok": True,
        "task_id": task_id,
        "note": note_generation_diagnosis(job, result),
    }


@router.post("/tasks/{task_id}/retry")
async def retry_agent_task(request: Request, task_id: str) -> dict[str, Any]:
    # Optional JSON body {"speaker_diarization": true|false}: re-run the
    # recording with speaker separation switched on or off. Every other option
    # is the original task's.
    overrides: dict[str, str] = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - an empty or non-JSON body means no overrides
        body = None
    if isinstance(body, dict) and body.get("speaker_diarization") is not None:
        value = body.get("speaker_diarization")
        on = value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on"}
        overrides["speaker_diarization"] = "true" if on else "false"
    retried = await retry_for_request(request, task_id, overrides=overrides or None)
    next_task_id = str(retried.get("task_id") or "")
    job = retried.get("job") if isinstance(retried.get("job"), dict) else {}
    return {
        "ok": True,
        "source_task_id": task_id,
        "task_id": next_task_id,
        "status": job.get("status") or "queued",
        "package_url": f"/agent/v1/tasks/{next_task_id}/package",
        "task": _task_brief(job),
    }


@router.post("/tasks/{task_id}/debreath")
def debreath_agent_task(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Remove the silent gaps from a finished task's source media.

    Delegates to the local job route's entry, so an agent passes exactly the
    guards the page passes — ownership, completed-only, one render at a time,
    source present and in a supported container — instead of a second
    implementation that can drift from it.
    """
    accepted = start_local_debreath(request, task_id, background_tasks, payload)
    return {
        **accepted,
        "package_url": f"/agent/v1/tasks/{task_id}/package",
        "task": _task_brief(_job_for_request(request, task_id)),
    }


@router.post("/tasks/{task_id}/visual-note")
def visual_note_agent_task(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Write this task's note from its de-breathed media and remapped subtitles.

    Delegates to the local job route's entry, so an agent passes exactly the
    guards the page passes — ownership, completed-only, a cut file that exists,
    a cut list to remap the subtitles with, a reachable Claude, one run at a
    time — instead of a second implementation that can drift from it.

    ``preview: true`` is free and answers what would be sent and who pays, which
    is the call to make first: a plain call spends the machine owner's Claude
    allowance. ``replace_note: false`` writes the note without making it the
    task's note; ``restore_previous_note`` and ``use_generated_note`` switch
    between the two notes without a model call.
    """
    accepted = start_local_visual_note(request, task_id, background_tasks, payload)
    return {
        **accepted,
        "package_url": f"/agent/v1/tasks/{task_id}/package",
        "task": _task_brief(_job_for_request(request, task_id)),
    }


@router.post("/tasks/{task_id}/agent-note/material")
def agent_note_material(
    request: Request,
    task_id: str,
    payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """What the calling agent writes this task's note from: the transcript (whole,
    or one small part with ``small_parts``), its frames as paths on this machine,
    and the note skill with the fixed contract.

    The first call for a video extracts the frames (seconds to a minute), so it
    waits for an idle queue the same way the frame note does.
    """
    part = int((payload or {}).get("part") or 1)
    small_parts = bool((payload or {}).get("small_parts"))
    job = _job_for_request(request, task_id)
    if not agent_note._stored_frames(task_id, job.get("result") or {}) and queue_is_busy():
        raise HTTPException(
            status_code=409,
            detail="有任务正在处理中，等队列空下来再取材料，避免抽帧和转录同时跑。",
        )
    try:
        return {"ok": True, **agent_note.material(
            task_id, part=part, small_parts=small_parts, client_id=_local_client_scope(request),
            keyframe_extractor=extract_keyframes,
        )}
    except visual_note_job.VisualNoteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/agent-note")
def save_agent_note(
    request: Request,
    task_id: str,
    payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Make the note the calling agent wrote this task's note.

    With ``part``, one part of a long recording is kept and the note is replaced
    only once every part is in. The note it replaces is kept and comes back for
    free through ``POST /tasks/{id}/visual-note`` with ``restore_previous_note``.
    """
    body = payload or {}
    part_notes = body.get("part_notes")
    try:
        if body.get("part") is not None:
            saved = agent_note.save_part(
                task_id,
                part=int(body.get("part")),
                note_markdown=body.get("note_markdown"),
                basis_note=str(body.get("basis_note") or ""),
                model=str(body.get("model") or ""),
                client_id=_local_client_scope(request),
            )
            answer = {
                "ok": True,
                "task_id": task_id,
                "part": saved["part"],
                "total_parts": saved["total_parts"],
                "remaining_parts": saved["remaining_parts"],
                "completed": saved["completed"],
                "next": saved["next"],
                # What FluentFlow's check found this part does not say. Non-empty
                # asks the agent to write those points in and save the part again.
                "coverage": {
                    "checked": any(part.get("checked") for part in saved["coverage"]["parts"]),
                    "checked_by": saved["coverage"].get("checker") or None,
                    "missing": saved["coverage"]["missing"],
                    "parts": saved["coverage"]["parts"],
                },
            }
            if saved["job"] is not None:
                answer["previous_note_restorable"] = True
                answer["package_url"] = f"/agent/v1/tasks/{task_id}/package"
            return answer
        updated = agent_note.save(
            task_id,
            note_markdown=body.get("note_markdown"),
            part_notes=list(part_notes) if isinstance(part_notes, list) else None,
            basis_note=str(body.get("basis_note") or ""),
            model=str(body.get("model") or ""),
            client_id=_local_client_scope(request),
        )
    except visual_note_job.VisualNoteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="part 要是一个整数") from exc
    coverage = visual_note_job.visual_note_state(updated.get("result")).get("coverage") or {}
    return {
        "ok": True,
        "task_id": task_id,
        "previous_note_restorable": True,
        # What FluentFlow's own check found the note does not say. The note is
        # saved either way; a non-empty ``missing`` asks the agent to write
        # those points in and save again.
        "coverage": {
            "checked": any(part.get("checked") for part in coverage.get("parts") or []),
            # Who ran the check: ``text_model:<provider>/<model>``, or the Claude
            # channel. Empty when nothing checked.
            "checked_by": coverage.get("checker") or None,
            "missing": coverage.get("missing") or [],
            "parts": coverage.get("parts") or [],
        },
        "package_url": f"/agent/v1/tasks/{task_id}/package",
        "task": _task_brief(updated),
    }


@router.get("/tasks/{task_id}/agent-note/parts/{part}")
def get_agent_note_part(request: Request, task_id: str, part: int) -> dict[str, Any]:
    """One saved part of the agent's note, without its transcript: what the
    read-through reads, a part at a time."""
    try:
        return {"ok": True, "task_id": task_id, **agent_note.saved_part(
            task_id, part=part, client_id=_local_client_scope(request),
        )}
    except visual_note_job.VisualNoteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _feedback_call(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except (note_feedback.NoteFeedbackError, visual_note_job.VisualNoteError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/note-skill")
def get_note_skill() -> dict[str, Any]:
    """The note skill in effect and every rule that was added to it from feedback."""
    skill = note_skills.load_note_skill()
    return {
        "ok": True,
        "text": skill.text,
        "rules": skill.rules,
        "is_default": skill.is_default,
        "version": skill.version,
        "path": str(skill.path),
        "rules_log": note_feedback.rules_log(),
    }


@router.post("/note-skill/rules")
def apply_note_rule(payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    """Write a rule the agent has verified into the user's skill."""
    body = payload or {}
    ids = body.get("feedback_ids")
    return {"ok": True, **_feedback_call(
        note_feedback.apply_rule,
        skill_text=str(body.get("skill_text") or ""),
        rule=str(body.get("rule") or ""),
        feedback_ids=[str(i) for i in ids] if isinstance(ids, list) else [],
        rounds=int(body.get("rounds") or 0),
        evidence=str(body.get("evidence") or ""),
    )}


@router.get("/note-feedback")
def list_note_feedback() -> dict[str, Any]:
    return {"ok": True, "feedback": [note_feedback.public_view(item) for item in note_feedback.list_feedback()]}


@router.post("/note-feedback")
def record_note_feedback(request: Request, payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    """Keep one problem the user reported about a note as a test case."""
    body = payload or {}
    task_id = str(body.get("task_id") or "")
    title = ""
    if task_id:
        result = _job_for_request(request, task_id).get("result") or {}
        title = str(result.get("display_title") or result.get("filename") or "")
    part = body.get("part")
    return {"ok": True, "feedback": note_feedback.public_view(_feedback_call(
        note_feedback.record_feedback,
        task_id=task_id,
        problem=str(body.get("problem") or ""),
        kind=str(body.get("kind") or ""),
        criterion=str(body.get("criterion") or ""),
        note_excerpt=str(body.get("note_excerpt") or ""),
        part=int(part) if part else None,
        transcript_excerpt=str(body.get("transcript_excerpt") or ""),
        title=title,
    ))}


@router.post("/tasks/{task_id}/note-draft")
def draft_note_part(request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    """One part of the note written by the product's own writer with candidate rules; not saved."""
    body = payload or {}
    return {"ok": True, **_feedback_call(
        note_feedback.draft_part,
        task_id,
        rules=str(body.get("rules") or ""),
        part=int(body.get("part") or 1),
        client_id=_local_client_scope(request),
    )}


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
    client_id = _local_client_scope(request)
    job = _job_for_request(request, task_id)
    initial_result = deepcopy(job.get("result"))
    result = dict(initial_result or {})

    frame_note_unavailable: Optional[str] = None
    if _note_written_from_frames(result):
        described = visual_note_job.describe(
            task_id, job, api_key=resolve_secret(None, "anthropic_api_key")
        )
        if described["eligible"]:
            accepted = start_local_visual_note(
                request, task_id, background_tasks, {"replace_note": True}
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
    transcript = str(
        result.get("corrected_transcript_text") or result.get("transcript_text") or ""
    ).strip()
    if not transcript:
        raise HTTPException(status_code=400, detail="No transcript available for note regeneration")

    system_prompt = _stored_note_setting(job, payload, "system_prompt")
    note_mode = _stored_note_setting(job, payload, "note_mode")
    prompt_preset = _stored_note_setting(job, payload, "prompt_preset")
    prompt_preset_label = _stored_note_setting(job, payload, "prompt_preset_label")
    kwargs = local_ai_kwargs(
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
            lambda: summarize_transcript_with_metadata(transcript, **kwargs),
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
        updated = finalize_job_result_if_unchanged(
            task_id=task_id,
            expected_result=initial_result,
            result=result,
            status=str(job.get("status") or "completed"),
            client_id=client_id,
            stage=str(job.get("stage") or "done"),
            progress=job.get("progress"),
            summary_status="failed",
            error_reason=job.get("error_reason"),
        )
        if updated is None:
            raise HTTPException(status_code=409, detail=conflict) from exc
        log_event(
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
        "summary_written_from": "text_regeneration",
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
    result = _attach_result_artifacts(task_id, result)
    updated = finalize_job_result_if_unchanged(
        task_id=task_id,
        expected_result=initial_result,
        result=result,
        status="completed",
        client_id=client_id,
        stage="done",
        progress=100,
        summary_status="completed",
        error_reason=None,
    )
    if updated is None:
        latest = get_job(task_id, client_id=client_id)
        latest_result = latest.get("result") if latest else None
        if isinstance(latest_result, dict):
            _attach_result_artifacts(task_id, latest_result)
        raise HTTPException(status_code=409, detail=conflict)
    log_event(
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


@router.post("/tasks/{task_id}/exports")
async def export_agent_task(
    request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None)
) -> dict[str, Any]:
    payload = payload or {}
    client_id = _local_client_scope(request)
    job = _job_for_request(request, task_id)
    result = dict(job.get("result") or {})
    markdown = str(payload.get("markdown") or result.get("summary_markdown") or "").strip()
    if not markdown:
        raise HTTPException(status_code=400, detail="No markdown note available to export")
    target = str(payload.get("target") or "lark").strip().lower()
    if target == "pdf":
        try:
            exported = await asyncio.get_running_loop().run_in_executor(
                None, lambda: note_pdf.export_pdf(task_id, client_id=client_id, output_dir=payload.get("output_dir")),
            )
        except note_pdf.NotePdfError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "target": "pdf", **exported}
    if target not in {"lark", "feishu"}:
        raise HTTPException(status_code=400, detail="target is lark or pdf")

    title = str(
        payload.get("title") or result.get("display_title") or job.get("source_filename") or task_id
    ).strip()
    resolved_title = resolve_lark_doc_title(markdown, filename_stem="", form_title=title)
    # The request's route and folder, else the user's export settings, else
    # auto. Local-owner routes only; hosted account OAuth is rejected inside.
    # "auto" asks lark-cli whether it is signed in; keep that off the event loop.
    route_choice = str(payload.get("lark_export_route") or "").strip() or get_preference("lark_export_route") or "auto"
    folder_token = str(payload.get("folder_token") or "").strip() or get_preference("lark_folder_token")
    export_target = await asyncio.get_running_loop().run_in_executor(
        None, _local_lark_export_target, route_choice, payload.get("lark_via_cli")
    )
    kwargs: dict[str, Any] = {}
    if (app_id := resolve_secret(payload.get("lark_app_id"), "lark_app_id")):
        kwargs["app_id"] = app_id
    if (app_secret := resolve_secret(payload.get("lark_app_secret"), "lark_app_secret")):
        kwargs["app_secret"] = app_secret
    if folder_token:
        kwargs["folder_token"] = folder_token

    route = "/agent/v1/tasks/{task_id}/exports"
    started_at = time.perf_counter()
    log_event(
        task_id=task_id,
        event_name="agent_export_started",
        source_type=job.get("source_type"),
        source_filename=job.get("source_filename"),
        summary_length=len(markdown),
        stage="export",
        export_target=export_target,
        metadata=event_metadata(route=route, target=target, doc_title=resolved_title),
    )
    try:
        loop = asyncio.get_running_loop()
        if export_target == "lark_cli":
            export_response = await loop.run_in_executor(
                None,
                lambda: export_markdown_via_lark_cli(
                    resolved_title, markdown, task_id=task_id, artifact_root=_artifact_storage_dir(),
                ),
            )
        else:
            export_response = await loop.run_in_executor(
                None,
                lambda: export_markdown_to_lark(
                    resolved_title,
                    markdown,
                    task_id=task_id,
                    artifact_root=_artifact_storage_dir(),
                    **kwargs,
                ),
            )
    except Exception as exc:
        detail = friendly_error(exc)
        log_event(
            task_id=task_id,
            event_name="agent_export_completed",
            source_type=job.get("source_type"),
            source_filename=job.get("source_filename"),
            summary_length=len(markdown),
            stage="export",
            duration_seconds=round(time.perf_counter() - started_at, 3),
            success=False,
            error_reason=detail,
            export_target=export_target,
            metadata=event_metadata(route=route, target=target, raw_error=str(exc)),
        )
        raise HTTPException(status_code=500, detail=detail) from exc

    if isinstance(export_response, dict):
        export_response["doc_title"] = resolved_title
        export_response["task_id"] = task_id
    export_record = {
        "target": target,
        "route": export_target,
        "title": resolved_title,
        "url": export_response.get("url") if isinstance(export_response, dict) else None,
        "response": export_response,
    }
    updated = append_job_result_list_item(
        task_id,
        "exports",
        export_record,
        client_id=client_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Job not found")
    if export_record["url"]:
        # Where the editor reads the export from, same as the web export.
        _record_export_on_task(
            task_id, client_id, lark_doc_title=resolved_title, lark_response=export_response, lark_error=None,
        )
        updated = get_job(task_id, client_id=client_id) or updated
    log_event(
        task_id=task_id,
        event_name="agent_export_completed",
        source_type=job.get("source_type"),
        source_filename=job.get("source_filename"),
        summary_length=len(markdown),
        stage="export",
        duration_seconds=round(time.perf_counter() - started_at, 3),
        success=True,
        export_target=export_target,
        feishu_doc_url=export_record["url"],
        metadata=event_metadata(route=route, target=target, doc_title=resolved_title),
    )
    return {"ok": True, "task_id": task_id, "export": export_record, "task": _task_brief(updated)}
