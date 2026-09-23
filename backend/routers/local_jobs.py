"""Local job-query routes: list jobs, read one job, read one job's processing
detail, and the restart-interruption notice (list + acknowledge). Mutation,
cancellation, retry, artifacts, and SSE live elsewhere.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Request

from backend.core.job_store import (
    RESTART_INTERRUPTION_KEY,
    acknowledge_restart_interruptions,
    get_job,
    list_job_steps,
    list_job_summaries,
    list_jobs,
    list_unacknowledged_restart_interruptions,
)
from backend.core.local_request_scope import request_client_id
from backend.core.local_task_detail import POLICY, build_task_detail, build_task_snapshot


router = APIRouter()


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


@router.get("/jobs")
def get_jobs(
    request: Request,
    limit: Optional[int] = None,
    include_result: bool = False,
    updated_since: Optional[str] = None,
) -> dict[str, Any]:
    """Every job by default; ``limit`` caps it, ``updated_since`` narrows a poll
    to the rows written since the newest ``updated_at`` the caller already has."""
    client_id = _local_client_scope(request)
    limit_value = limit if limit and limit > 0 else None
    jobs = (
        list_jobs(limit=limit_value, client_id=client_id, updated_since=updated_since)
        if include_result
        else list_job_summaries(limit=limit_value, client_id=client_id, updated_since=updated_since)
    )
    return {"jobs": [{**job, "task_snapshot": build_task_snapshot(job)} for job in jobs]}


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
    jobs = list_unacknowledged_restart_interruptions(client_id=_local_client_scope(request))
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
        client_id=_local_client_scope(request),
    )
    return {"ok": True, "acknowledged": acknowledged}


@router.get("/jobs/{task_id}")
def get_job_detail(request: Request, task_id: str) -> dict[str, Any]:
    job = get_job(task_id, client_id=_local_client_scope(request))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {**job, "task_snapshot": build_task_snapshot(job)}


@router.get("/jobs/{task_id}/detail")
def get_job_processing_detail(request: Request, task_id: str) -> dict[str, Any]:
    job = get_job(task_id, client_id=_local_client_scope(request))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    steps = list_job_steps(task_id=task_id, limit=100)
    return build_task_detail(job, job_steps=steps)
