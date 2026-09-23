"""Local job-mutation routes: cancellation and deletion."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.job_store import cancel_job_steps, delete_jobs, get_job, upsert_job
from backend.core.local_job_runtime import JOB_EVENTS
from backend.core.local_request_scope import request_client_id
from backend.core.storage_cleanup import cleanup_task_all_files

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_ACTIVE_STATUSES = {"queued", "running"}

router = APIRouter()


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


@router.post("/jobs/{task_id}/cancel")
async def cancel_job(request: Request, task_id: str) -> dict[str, Any]:
    client_id = _local_client_scope(request)
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") in _TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"Job is already {job.get('status')}")

    cancelled_running_task = await JOB_EVENTS.cancel(task_id)
    cancelled_steps = cancel_job_steps(task_id)
    upsert_job(
        task_id=task_id,
        status="cancelled",
        client_id=job.get("client_id") or client_id,
        stage=job.get("stage") or "cancelled",
        progress=job.get("progress") or 0,
        source_type=job.get("source_type"),
        source_filename=job.get("source_filename"),
        source_file_size_mb=job.get("source_file_size_mb"),
        summary_status=job.get("summary_status"),
        error_reason="user_cancelled",
        metadata=job.get("metadata"),
    )
    await JOB_EVENTS.publish(
        task_id,
        {"stage": "error", "progress": job.get("progress") or 0, "error": "Task cancelled"},
    )
    log_event(
        task_id=task_id,
        event_name="task_cancelled",
        source_type=job.get("source_type"),
        source_filename=job.get("source_filename"),
        source_file_size_mb=job.get("source_file_size_mb"),
        stage=job.get("stage"),
        success=False,
        metadata=event_metadata(trigger="background_tasks"),
    )
    return {
        "ok": True,
        "task_id": task_id,
        "status": "cancelled",
        "cancelled_running_task": cancelled_running_task,
        "cancelled_steps": cancelled_steps,
        "queued_before_cancel": False,
    }


def _delete_job_for_request(request: Request, task_id: str) -> dict[str, Any]:
    client_id = _local_client_scope(request)
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") in _ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="Cancel running jobs before deleting them")
    cleanup_task_all_files(task_id, job.get("metadata"))
    deleted = delete_jobs([task_id], client_id=client_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True, "task_id": task_id, "deleted": True}


@router.delete("/jobs/{task_id}")
def delete_job(request: Request, task_id: str) -> dict[str, Any]:
    return _delete_job_for_request(request, task_id)


@router.post("/jobs/{task_id}/delete")
def delete_job_fallback(request: Request, task_id: str) -> dict[str, Any]:
    return _delete_job_for_request(request, task_id)
