"""Local job-query routes: list jobs, read one job, read one job's processing
detail, and the restart-interruption notice (list + acknowledge). Mutation,
cancellation, retry, artifacts, and SSE live elsewhere.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Request

from backend.core import cut_trust, note_progress, task_activity, task_steps
from backend.core.local_error_diagnostics import playback_audio_diagnosis
from backend.core.job_store import (
    RESTART_INTERRUPTION_KEY,
    acknowledge_restart_interruptions,
    get_job,
    list_job_states,
    list_job_summaries,
    list_jobs,
    list_unacknowledged_restart_interruptions,
)
from backend.core.local_task_detail import POLICY, build_task_detail, build_task_snapshot
from backend.core.request_scope import local_client_scope


router = APIRouter()


@router.get("/jobs")
def get_jobs(
    request: Request,
    limit: Optional[int] = None,
    include_result: bool = False,
    updated_since: Optional[str] = None,
    view: Optional[str] = None,
) -> dict[str, Any]:
    """Every job by default; ``limit`` caps it, ``updated_since`` narrows a poll
    to the rows written since the newest ``updated_at`` the caller already has.

    ``view=activity`` answers only whether each task is still working (its
    status and ``activity``), for the restart guard: tenths of a second where
    the full list can take seconds. A service from before this view ignores the
    parameter and sends the full list, whose rows carry the same ``activity``,
    so a guard that asks for it works against either."""
    client_id = local_client_scope(request)
    if view == "activity":
        states = list_job_states(client_id=client_id)
        steps = task_steps.steps_of(str(job["task_id"]) for job in states)
        return {"jobs": [
            {
                "task_id": job["task_id"],
                "status": job["status"],
                "activity": task_activity.activity(job, steps.get(str(job["task_id"]), [])).as_dict(),
            }
            for job in states
        ]}
    limit_value = limit if limit and limit > 0 else None
    # A note being written changes its row's progress without writing the row.
    writing = note_progress.live_task_ids()
    jobs = (
        list_jobs(limit=limit_value, client_id=client_id, updated_since=updated_since, also_task_ids=writing)
        if include_result
        else list_job_summaries(limit=limit_value, client_id=client_id, updated_since=updated_since,
                                also_task_ids=writing)
    )
    steps = task_steps.steps_of(str(job.get("task_id") or "") for job in jobs)
    return {"jobs": [
        {
            **(job if include_result else _list_row(job)),
            "task_snapshot": build_task_snapshot(job) if include_result else _list_snapshot(build_task_snapshot(job)),
            "activity": task_activity.activity(job, steps.get(str(job.get("task_id") or ""), [])).as_dict(),
        }
        for job in jobs
    ]}


# What a list row leaves to the task's own read (``GET /jobs/{id}``, or its
# ``/detail`` for the processing page), because no list reader uses it: the
# files a task made (the card offers only the cut copy), the previews' second
# copies, and the snapshot's files, actions and data checks. They were about a
# third of the list's 3.2 MB on 300 tasks (2026-11-26).
_LIST_ARTIFACTS = ("debreath_media",)
_LIST_DROPPED_RESULT_KEYS = ("summary_preview", "transcript_text_preview")
_LIST_DROPPED_SNAPSHOT_KEYS = ("artifacts", "actions", "step_statuses", "data_quality")


def _list_row(job: dict[str, Any]) -> dict[str, Any]:
    result = job.get("result")
    if not isinstance(result, dict):
        return job
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    trimmed = {key: value for key, value in result.items() if key not in _LIST_DROPPED_RESULT_KEYS}
    trimmed["artifacts"] = {key: artifacts[key] for key in _LIST_ARTIFACTS if key in artifacts}
    return {**job, "result": trimmed}


def _list_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if key not in _LIST_DROPPED_SNAPSHOT_KEYS}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _interrupted_task(job: dict[str, Any]) -> dict[str, Any]:
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    interruption = metadata.get(RESTART_INTERRUPTION_KEY)
    interruption = interruption if isinstance(interruption, dict) else {}
    video_source = metadata.get("video_source") if isinstance(metadata.get("video_source"), dict) else {}
    folder_intake = metadata.get("folder_intake") if isinstance(metadata.get("folder_intake"), dict) else {}
    retryable = POLICY.is_source_retryable(job)
    filename = _text(job.get("source_filename"))
    return {
        "task_id": job.get("task_id"),
        "title": _text(
            metadata.get("display_title")
            or video_source.get("display_title")
            or metadata.get("raw_title")
            or filename
        ) or _text(job.get("task_id")),
        "filename": filename or None,
        "source_type": job.get("source_type"),
        "started": bool(interruption.get("started")),
        "interrupted_at": interruption.get("interrupted_at"),
        "retryable": retryable,
        # Where the recording was, for the ones that cannot be re-run: the user
        # has to find it again (or paste the link again) to resubmit.
        "original_path": None if retryable else (_text(folder_intake.get("original_path")) or None),
        "source_link": None if retryable else (
            _text(video_source.get("source_url") or metadata.get("video_source_input_preview")) or None
        ),
    }


@router.get("/jobs/interrupted")
def get_interrupted_jobs(request: Request) -> dict[str, Any]:
    """Tasks the last service restart cut off that have not been acknowledged."""
    jobs = list_unacknowledged_restart_interruptions(client_id=local_client_scope(request))
    return {"tasks": [_interrupted_task(job) for job in jobs]}


@router.post("/jobs/interrupted/acknowledge")
def acknowledge_interrupted_jobs(
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Record that the user has seen these interruptions, so no window shows them again."""
    raw_ids = payload.get("task_ids") if isinstance(payload, dict) else None
    if not isinstance(raw_ids, list):
        raise HTTPException(status_code=422, detail="task_ids must be a list")
    acknowledged = acknowledge_restart_interruptions(
        [str(task_id) for task_id in raw_ids],
        client_id=local_client_scope(request),
    )
    return {"ok": True, "acknowledged": acknowledged}


@router.get("/jobs/{task_id}")
def get_job_detail(request: Request, task_id: str) -> dict[str, Any]:
    job = get_job(task_id, client_id=local_client_scope(request))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    result = job.get("result") if isinstance(job.get("result"), dict) else None
    # Why the player has nothing of FluentFlow's own to play, in the user's
    # words; only this read carries it, the list does not.
    problem = playback_audio_diagnosis(result)
    if problem:
        job = {**job, "result": {**result, "playback_audio_problem": problem}}
    return {
        **job, "task_snapshot": build_task_snapshot(job), "activity": task_activity.activity(job).as_dict(),
        # Whether the cut report still asks the user about its judge's places.
        "cut_standing": cut_trust.of_job(job),
    }


@router.get("/jobs/{task_id}/note-progress")
def get_note_progress(request: Request, task_id: str) -> dict[str, Any]:
    """How far FluentFlow's own writer has got on the note, and the text written
    so far; both empty when no writer is on it. Polled by the editor every few
    seconds while the note is written, so it reads nothing it does not show."""
    job = get_job(task_id, client_id=local_client_scope(request))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    progress = task_activity.activity(job).note_progress
    return {"progress": progress, "draft": note_progress.draft(task_id) if progress else ""}


@router.get("/jobs/{task_id}/detail")
def get_job_processing_detail(request: Request, task_id: str) -> dict[str, Any]:
    job = get_job(task_id, client_id=local_client_scope(request))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    steps = task_steps.steps_of([task_id]).get(task_id, [])
    # The timeline is read from the steps (``build_task_detail``); the steps
    # are given too, with what they mean for the task as a whole.
    return {
        **build_task_detail(job),
        "steps": steps,
        "activity": task_activity.activity(job, steps).as_dict(),
        "step_timings": task_steps.timings(job, steps),
    }
