"""Agent routes that submit and read tasks: create, workflow status, list, get,
package and wait."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request

from backend.core.event_context import event_metadata
from backend.core.job_store import list_jobs
from backend.core import agent_correction, note_section_times, task_intelligence
from backend.core.local_entry_guards import claim_task_id
from backend.core import model_usage, task_activity
from backend.core import agent_cut, agent_note, local_folder_intake
from backend.core import speaker_diarization as speaker_diarization_core
from backend.core.local_request_scope import (
    request_is_localhost,
    require_local_agent_access,
)
from backend.core.edition_identity import INTAKE_REJECTION
from backend.core.workflow_status import workflow_status
from backend.core.task_submission import local_path_options, queue_local_media_file
from backend.core.link_submission import allow_duplicate_requested, submit_video_source_job
from backend.routers.agent_v1 import common
from backend.routers.agent_v1.common import (
    AGENT_NOTE_NEXT,
    _ROUTE,
    _agent_cut_brief,
    _alignment_next,
    _bounded_finite_float,
    _correction_next,
    _cut_running,
    _job_for_request,
    _note_pending,
    _page_url,
    _task_brief,
    _task_done,
    _task_package_response,
    _truthy_json,
    _write_transcript_task,
    _write_transcript_task_in_background,
)

router = APIRouter(prefix="/agent/v1", dependencies=[Depends(require_local_agent_access)])

@router.post("/tasks")
async def create_agent_task(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    input_text = str(payload.get("input") or payload.get("url") or "").strip()
    transcript = str(payload.get("transcript_text") or "").strip()
    input_type = str(payload.get("input_type") or "").strip().lower()
    raw_options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    if raw_options.get("cut_judge") is not None:
        try:
            agent_cut.normalized_choice(raw_options["cut_judge"])
        except agent_cut.AgentCutError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        options = task_intelligence.agent_options(raw_options)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    client_id = common._local_client_scope(request)
    local_path = str(payload.get("path") or payload.get("local_path") or "").strip()

    try:
        cut_judge = agent_cut.normalized_choice(options.get("cut_judge") or "auto")
    except agent_cut.AgentCutError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # cut=false: only the transcript is wanted, nothing is judged or cut.
    # Recorded beside cut_judge, not as a queue option (the page has no such
    # option); left out or true records nothing.
    no_cut = {} if agent_cut.cut_wanted_option(options.pop("cut", None)) else {"cut": False}

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
            origin={"chosen_with": "agent_api", "folder": str(source.parent), "cut_judge": cut_judge, **no_cut},
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
            extra_metadata={"agent_input_type": "video_link", "cut_judge": cut_judge, **no_cut},
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
            common.upsert_job(
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


@router.get("/status")
def agent_workflow_status() -> dict[str, Any]:
    """Who does each step for a task an agent submits, and what needs the
    person: the same list Settings shows (decision 6.24), in Chinese."""
    overview = workflow_status(for_agent=True)
    return {
        "attention": [item["text"]["zh"] for item in overview["attention"]],
        "steps": [{"step": step["title"]["zh"], "now": step["summary"]["zh"]} for step in overview["steps"]],
        # Model calls that belong to no task (the key check, the note style,
        # a call whose thread lost its task): a task's own are in get_task.
        "model_usage_outside_tasks": model_usage.outside_tasks_or_none(),
    }


@router.get("/tasks")
def list_agent_tasks(request: Request, query: str = "", limit: int = 20) -> dict[str, Any]:
    """Recent tasks, or the ones whose title, note title or file name contains ``query``.

    How an agent finds the task behind a screenshot of a note: the screenshot
    shows the note's own title, which is often not the file's name, so both are
    searched and both are returned for the agent to judge.
    """
    needle = query.strip().lower()
    # At most 100 a call; has_more says a narrower query would find the rest.
    cap = max(1, min(int(limit or 20), 100))
    found = []
    for job in list_jobs(limit=None, client_id=common._local_client_scope(request), include_result=True):
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
        if len(found) > cap:
            break
    more = len(found) > cap
    return {"ok": True, "tasks": found[:cap], "has_more": more}


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
            "agent_cut": _agent_cut_brief(job),
            # Which step it is on, which failed or were stopped, and where
            # resume_task carries it on from.
            "activity": task_activity.activity(job).as_dict(),
            # What an edit starts from: name it as ``base_version`` when saving
            # the note, so a change made elsewhere meanwhile is not replaced.
            "note_version": (job.get("result") or {}).get("note_version"),
            "transcript_version": (job.get("result") or {}).get("transcript_version"),
            "note_alignment": note_section_times.brief(job.get("result") or {}),
            # Parts an agent saved before the note was whole: who carries on.
            "note_draft": agent_note.unfinished_draft(task_id),
            "intelligence": (job.get("result") or {}).get("intelligence"),
            "deferred_ai_steps": {
                key: value for key, value in (job.get("result") or {}).items()
                if isinstance(value, dict) and value.get("reason") == "agent_required"
            },
            # Correcting the transcript and translating the subtitles, when
            # they are left to the agent: get_agent_correction_material.
            "agent_corrections_left": agent_correction.pending(job.get("result") or {}),
            # Tokens and money spent on models, the task's total and each step's.
            "model_usage": model_usage.for_task(task_id),
        },
        "package_url": f"/agent/v1/tasks/{task_id}/package",
        # Open this to show the owner the task; never build it from routes.
        "page_url": _page_url(request, task_id),
    }


@router.get("/tasks/{task_id}/package")
def get_agent_task_package(request: Request, task_id: str) -> dict[str, Any]:
    return _task_package_response(_job_for_request(request, task_id))


# How long one wait_task call may hold: long enough that an agent waiting
# through a three-hour recording makes a few calls instead of a hundred, each
# of which resends its whole conversation (2026-10-27: 157 turns, about 25
# million input tokens, used up the Codex workspace's credits). It returns at
# once whenever the agent has something to do.
WAIT_MAX_SECONDS = 600


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
        maximum=WAIT_MAX_SECONDS,
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
        if agent_cut.is_waiting(job.get("result")):
            # FluentFlow is waiting for the caller: waiting on it back would
            # hold both until the time limit gives the judgement away.
            return {
                "ok": True,
                "done": False,
                "agent_cut": _agent_cut_brief(job),
                "package_url": f"/agent/v1/tasks/{task_id}/package",
            }
        if agent_note.tell_ready_while_cut_renders(job):
            # The cut is decided and rendering; the note need not wait for it.
            return {
                "ok": True,
                "done": False,
                "cut_running": True,
                "agent_note": {"status": "ready", "next": AGENT_NOTE_NEXT},
                "package_url": f"/agent/v1/tasks/{task_id}/package",
            }
        if _task_done(job):
            brief = _task_brief(job)
            answer = {
                "ok": True,
                "done": True,
                "note_status": brief["note"]["status"],
                "task": brief,
                "page_url": _page_url(request, task_id),
            }
            # One step at a time, in the order they build on each other: a
            # note left half written, the transcript corrections, the note,
            # then its alignment.
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            if result.get("download_only"):
                # Only the video was asked for: nothing is left to do.
                answer["downloaded_file"] = result.get("downloaded_file")
                return answer
            draft = agent_note.unfinished_draft(task_id)
            step = (
                (draft or {}).get("next") or _correction_next(job)
                or agent_note.write_next(result) or _alignment_next(job)
            )
            if step:
                answer["next"] = step
            return answer
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
