"""Local job-mutation routes: cancelling, stopping a step, carrying on, deleting."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from backend.core import task_actions, task_activity
from backend.core.job_store import delete_jobs, get_job
from backend.core.local_request_scope import request_client_id
from backend.core.storage_cleanup import cleanup_task_all_files

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_ACTIVE_STATUSES = {"queued", "running"}

router = APIRouter()


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


@router.post("/jobs/{task_id}/cancel")
async def cancel_job(request: Request, task_id: str) -> dict[str, Any]:
    try:
        return await task_actions.cancel_task(task_id, _local_client_scope(request))
    except task_actions.Refused as exc:
        raise exc.as_http() from exc


@router.post("/jobs/{task_id}/stop")
async def stop_job_step(request: Request, task_id: str) -> dict[str, Any]:
    """Stop the step the task is on (``task_actions.stop_step``)."""
    try:
        return await task_actions.stop_step(task_id, _local_client_scope(request))
    except task_actions.Refused as exc:
        raise exc.as_http() from exc


@router.post("/jobs/{task_id}/resume")
async def resume_job(request: Request, task_id: str) -> dict[str, Any]:
    """Carry the task on from the step that did not finish
    (``task_actions.resume_task``): 接着做, or 重做这一步 for a failed one."""
    try:
        return await task_actions.resume_task(task_id, _local_client_scope(request))
    except task_actions.Refused as exc:
        raise exc.as_http() from exc


def _delete_job_for_request(request: Request, task_id: str) -> dict[str, Any]:
    client_id = _local_client_scope(request)
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") in _ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="Cancel running jobs before deleting them")
    busy = task_activity.busy_reason(job)
    if busy:
        raise HTTPException(status_code=409, detail=busy)
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
