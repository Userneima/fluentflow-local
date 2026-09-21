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
import math
import time
from copy import deepcopy
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
    list_job_summaries,
    note_conflict_fingerprint,
    update_job_result,
    upsert_job,
)
from backend.core.lark_cli_exporter import export_markdown_via_lark_cli
from backend.core.lark_exporter import export_markdown_to_lark
from backend.core.local_agent_package import build_agent_task_package, note_generation_diagnosis
from backend.core.local_config import resolve_secret
from backend.core.local_entry_guards import claim_task_id, friendly_error, local_ai_kwargs
from backend.core import local_folder_intake
from backend.core.local_request_scope import (
    request_client_id,
    request_is_localhost,
    require_local_agent_access,
)
from backend.core.note_title import resolve_lark_doc_title
from backend.core.note_write import (
    NOTE_SOURCE_AGENT,
    apply_summary_edit,
    max_summary_edit_chars,
)
from backend.core.result_artifacts import _attach_result_artifacts
from backend.core.storage_paths import _artifact_storage_dir
from backend.core.edition_identity import INTAKE_REJECTION
from backend.routers.local_feishu_export import _local_lark_export_target
from backend.routers.local_job_debreath import start_local_debreath
from backend.routers.local_job_visual_note import start_local_visual_note
from backend.routers.local_processing import (
    local_path_options,
    queue_local_media_file,
    retry_job_from_stored_source,
)
from backend.routers.local_video_sources import submit_video_source_job

router = APIRouter(prefix="/agent/v1", dependencies=[Depends(require_local_agent_access)])

_ROUTE = "/agent/v1/tasks"


def _truthy_json(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "on"}


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


def _task_package_response(job: dict[str, Any]) -> dict[str, Any]:
    return build_agent_task_package(job, artifact_root=_artifact_storage_dir())


def _job_for_request(request: Request, task_id: str) -> dict[str, Any]:
    job = get_job(task_id, client_id=_local_client_scope(request))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _result_changed(task_id: str, client_id: Optional[str], initial_result: Any) -> bool:
    """True only when the note or its transcript moved — see NOTE_CONFLICT_FIELDS."""
    latest = get_job(task_id, client_id=client_id)
    if latest is None:
        return True
    return note_conflict_fingerprint(latest.get("result")) != note_conflict_fingerprint(initial_result)


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


@router.post("/tasks")
async def create_agent_task(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
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
        job = await submit_video_source_job(
            input_text=input_text,
            title=str(payload.get("title") or "").strip(),
            raw_options=options,
            client_id=client_id,
            route=_ROUTE,
            extra_metadata={"agent_input_type": "video_link"},
        )
        task_id_value = job["task_id"]
        return {
            "ok": True,
            "task_id": task_id_value,
            "status": job.get("status"),
            "package_url": f"/agent/v1/tasks/{task_id_value}/package",
            "job": job,
        }

    if transcript and input_type in {"", "transcript", "transcript_text"}:
        task_id_value = claim_task_id(
            str(payload.get("task_id") or "").strip() or None, client_id=client_id
        )
        title = str(payload.get("title") or "Transcript").strip()
        skip_summary = _truthy_json(options.get("skip_summary"))
        result: dict[str, Any] = {
            "task_id": task_id_value,
            "filename": title,
            "display_title": title,
            "transcript_text": transcript,
            "transcript_text_preview": transcript[:200],
            "source": "agent_transcript",
        }
        summary_status = "skipped"
        if skip_summary:
            result.update({"summary_skipped": True, "summary_status": "skipped"})
        else:
            kwargs = local_ai_kwargs(
                deepseek_api_key=payload.get("deepseek_api_key"),
                openai_api_key=payload.get("openai_api_key"),
                qwen_api_key=payload.get("qwen_api_key"),
                anthropic_api_key=payload.get("anthropic_api_key"),
                ai_provider=payload.get("ai_provider"),
                ai_model=payload.get("ai_model"),
                system_prompt=payload.get("system_prompt"),
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
        job = get_job(task_id_value, client_id=client_id)
        return {
            "ok": True,
            "task_id": task_id_value,
            "status": "completed",
            "package_url": f"/agent/v1/tasks/{task_id_value}/package",
            "package": _task_package_response(job or {"task_id": task_id_value, "result": result}),
        }

    # Name this edition in the refusal. An input list alone leaves the caller unable
    # to tell a missing capability from the wrong backend answering.
    raise HTTPException(status_code=400, detail=INTAKE_REJECTION)


_NOTE_FILTERS = ("any", "missing", "present")


def _agent_task_row(job: dict[str, Any]) -> dict[str, Any]:
    """A task as an agent picking work needs to see it.

    Deliberately not the full list row: an agent choosing which tasks to write
    notes for needs the note's state and size, not previews, artifacts, or
    transcription telemetry. ``note.source`` is the load-bearing field — without
    it a second "write notes for everything that needs one" pass cannot tell a
    pipeline note from one this agent already wrote, and rewrites its own work.
    """
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    return {
        "task_id": job.get("task_id"),
        "status": job.get("status"),
        # raw_title first, matching the browser app: records written before the
        # title-truncation fix carry a mangled display_title (a "4.5期…" file
        # became "4"), and an agent picking work by title needs a readable one.
        "title": result.get("raw_title") or result.get("display_title") or job.get("source_filename"),
        "updated_at": job.get("updated_at"),
        "transcript_chars": result.get("transcript_text_chars") or 0,
        "note": {
            # The result wins over the job's summary_status column: a note
            # written by the editor or an agent updates the result only, so the
            # column can still read "skipped" for a task that now has a note.
            "status": result.get("summary_status") or job.get("summary_status"),
            "chars": result.get("summary_markdown_chars") or 0,
            "edited": bool(result.get("summary_edited")),
            "source": result.get("summary_source"),
            "source_label": result.get("summary_source_label"),
        },
    }


def _note_is_missing(row: dict[str, Any]) -> bool:
    """Whether this task still needs a note written.

    The note body is the only reliable signal. Status is not: it is duplicated
    between the result and a job column that note writers do not update, and a
    "skipped" status with a body means transcript-only mode plus a note somebody
    wrote afterwards — which is exactly the task an agent must NOT redo.
    """
    return not row["note"]["chars"]


@router.get("/tasks")
def list_agent_tasks(
    request: Request,
    limit: int = 50,
    note: str = "any",
    status: str = "",
) -> dict[str, Any]:
    """List tasks so an agent can find the ones that still need a note.

    Without this an agent can only act on task ids the user pasted by hand,
    which is what made "write notes for everything that needs one" impossible to
    ask for. Reads the cheap list projection, so no transcript or note body is
    loaded to answer it.
    """
    note_filter = (note or "any").strip().lower()
    if note_filter not in _NOTE_FILTERS:
        raise HTTPException(
            status_code=422,
            detail=f"note must be one of: {', '.join(_NOTE_FILTERS)}",
        )
    rows = [
        _agent_task_row(job)
        for job in list_job_summaries(limit=limit, client_id=_local_client_scope(request))
    ]
    wanted_status = (status or "").strip().lower()
    if wanted_status:
        rows = [row for row in rows if str(row["status"] or "").lower() == wanted_status]
    if note_filter == "missing":
        rows = [row for row in rows if _note_is_missing(row)]
    elif note_filter == "present":
        rows = [row for row in rows if not _note_is_missing(row)]
    return {"ok": True, "count": len(rows), "note_filter": note_filter, "tasks": rows}


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
            "summary_status": job.get("summary_status"),
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
        if job.get("status") in {"completed", "failed", "cancelled"}:
            return {"ok": True, "done": True, "package": _task_package_response(job)}
        if time.monotonic() >= deadline:
            return {
                "ok": True,
                "done": False,
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
    retried = await retry_job_from_stored_source(request, task_id)
    next_task_id = str(retried.get("task_id") or "")
    job = retried.get("job") if isinstance(retried.get("job"), dict) else {}
    return {
        "ok": True,
        "source_task_id": task_id,
        "task_id": next_task_id,
        "status": job.get("status") or "queued",
        "package_url": f"/agent/v1/tasks/{next_task_id}/package",
        "package": _task_package_response(job),
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
        "package": _task_package_response(_job_for_request(request, task_id)),
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
        "package": _task_package_response(_job_for_request(request, task_id)),
    }


@router.post("/tasks/{task_id}/note/regenerate")
async def regenerate_agent_task_note(
    request: Request,
    task_id: str,
    payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    payload = payload or {}
    client_id = _local_client_scope(request)
    job = _job_for_request(request, task_id)
    initial_result = deepcopy(job.get("result"))
    result = dict(initial_result or {})
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

    kwargs = local_ai_kwargs(
        deepseek_api_key=payload.get("deepseek_api_key"),
        openai_api_key=payload.get("openai_api_key"),
        qwen_api_key=payload.get("qwen_api_key"),
        anthropic_api_key=payload.get("anthropic_api_key"),
        ai_provider=payload.get("ai_provider"),
        ai_model=payload.get("ai_model"),
        system_prompt=payload.get("system_prompt"),
        note_mode=payload.get("note_mode"),
    )
    route = "/agent/v1/tasks/{task_id}/note/regenerate"
    started_at = time.perf_counter()
    try:
        loop = asyncio.get_running_loop()
        summary_result = await loop.run_in_executor(
            None,
            lambda: summarize_transcript_with_metadata(transcript, **kwargs),
        )
    except Exception as exc:
        detail = friendly_error(exc)
        if _result_changed(task_id, client_id, initial_result):
            raise HTTPException(
                status_code=409,
                detail="笔记在生成期间已被修改，本次生成失败未覆盖你的编辑。",
            ) from exc
        result.update({
            "summary_status": "failed",
            "summary_error": detail,
            "summary_skipped": False,
        })
        updated = finalize_job_result_if_unchanged(
            task_id=task_id,
            expected_result=initial_result,
            result=result,
            status="failed",
            client_id=client_id,
            stage="summary_regenerate",
            progress=job.get("progress"),
            summary_status="failed",
            error_reason=detail,
        )
        if updated is None:
            raise HTTPException(
                status_code=409,
                detail="笔记在生成期间已被修改，本次生成失败未覆盖你的编辑。",
            ) from exc
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
        raise HTTPException(
            status_code=409,
            detail="笔记在生成期间已被修改，本次生成结果未覆盖你的编辑。",
        )
    result.update({
        "summary_markdown": summary_result.markdown,
        "summary_status": "completed",
        "summary_error": None,
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
        "prompt_preset": str(payload.get("prompt_preset") or "").strip() or result.get("prompt_preset"),
        "prompt_preset_label": str(payload.get("prompt_preset_label") or "").strip() or result.get("prompt_preset_label"),
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
        raise HTTPException(
            status_code=409,
            detail="笔记在生成期间已被修改，本次生成结果未覆盖你的编辑。",
        )
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
            route=route, note_generation_transcript_source=transcript_source
        ),
    )
    return {"ok": True, "task_id": task_id, "package": _task_package_response(updated)}


@router.put("/tasks/{task_id}/note")
def save_agent_task_note(
    request: Request,
    task_id: str,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Store a note an external agent wrote, in place of generating one locally.

    This is the write half of reading a task package: an agent reads the
    transcript, writes the note with its own model, and puts it back here. It
    deliberately shares ``apply_summary_edit`` with the editor route so the
    stored note is indistinguishable in shape from a hand-edited one — the
    editor, exports, and artifacts all keep working without knowing who wrote
    it, and an in-flight local regeneration correctly loses to this write.
    """
    client_id = _local_client_scope(request)
    job = _job_for_request(request, task_id)
    result = dict(job.get("result") or {})

    summary = payload.get("summary_markdown")
    if not isinstance(summary, str):
        raise HTTPException(status_code=400, detail="summary_markdown is required")
    # Stricter than the editor, which may legitimately clear a note: an agent
    # that produced nothing has failed, and must not blank the stored note.
    if not summary.strip():
        raise HTTPException(status_code=400, detail="summary_markdown must not be empty")
    limit = max_summary_edit_chars()
    if len(summary) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"Note is too large: {len(summary)} chars (limit {limit})",
        )

    # Optional precondition. An agent spends minutes writing, so the note it
    # read may already have been edited by the user in the meantime; echoing
    # back what it read turns a silent clobber into a 409 it can react to.
    expected = payload.get("expected_summary_markdown")
    if expected is not None:
        if not isinstance(expected, str):
            raise HTTPException(status_code=400, detail="expected_summary_markdown must be a string")
        if expected != str(result.get("summary_markdown") or ""):
            raise HTTPException(
                status_code=409,
                detail="笔记已被改动，本次写入未覆盖。请重新读取任务包后再写。",
            )

    next_result = apply_summary_edit(
        result,
        task_id,
        summary,
        source=NOTE_SOURCE_AGENT,
        source_label=str(payload.get("author") or "").strip() or None,
    )
    next_result = _attach_result_artifacts(task_id, next_result)
    updated = update_job_result(task_id, next_result, client_id=client_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Job not found")
    log_event(
        task_id=task_id,
        event_name="agent_note_saved",
        source_type=job.get("source_type"),
        source_filename=job.get("source_filename"),
        summary_length=len(summary),
        stage="summary_saved",
        success=True,
        metadata=event_metadata(
            route="/agent/v1/tasks/{task_id}/note",
            summary_source=NOTE_SOURCE_AGENT,
            summary_source_label=next_result.get("summary_source_label"),
            precondition_checked=expected is not None,
        ),
    )
    return {"ok": True, "task_id": task_id, "package": _task_package_response(updated)}


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
    if target not in {"lark", "feishu"}:
        raise HTTPException(status_code=400, detail="Only lark export is supported")

    title = str(
        payload.get("title") or result.get("display_title") or job.get("source_filename") or task_id
    ).strip()
    resolved_title = resolve_lark_doc_title(markdown, filename_stem="", form_title=title)
    # Local-owner routes only; hosted account OAuth is rejected inside.
    export_target = _local_lark_export_target(
        payload.get("lark_export_route"), payload.get("lark_via_cli")
    )
    kwargs: dict[str, Any] = {}
    if (app_id := resolve_secret(payload.get("lark_app_id"), "lark_app_id")):
        kwargs["app_id"] = app_id
    if (app_secret := resolve_secret(payload.get("lark_app_secret"), "lark_app_secret")):
        kwargs["app_secret"] = app_secret
    if payload.get("folder_token"):
        kwargs["folder_token"] = payload.get("folder_token")

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
                None, lambda: export_markdown_via_lark_cli(resolved_title, markdown)
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
    return {"ok": True, "task_id": task_id, "export": export_record, "package": _task_package_response(updated)}
