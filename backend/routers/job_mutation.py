"""Shared job-mutation router, assembled with an edition-specific client scope
and job-event sink.

Owns cancellation and deletion. Both are edition-neutral at their core (mark
cancelled / stop steps / reconcile the event sink / log; or clean task files and
delete the record). The concerns that differ between editions are injected as
optional hooks:

- ``reconcile_cancel`` — hosted quota release and in-process queue bookkeeping;
- ``reject_readonly`` — the hosted desktop-sync read-only write guard.

The local edition has neither, so it passes nothing.

Retry and transcript/summary edits are NOT here yet: retry re-runs the pipeline
(processing unit), and the edits depend on edit-record backup and result-artifact
helpers that must be made edition-neutral first.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional, Protocol

from fastapi import APIRouter, HTTPException, Request

from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.job_store import cancel_job_steps, delete_jobs, get_job, upsert_job
from backend.core.storage_cleanup import cleanup_task_all_files

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_ACTIVE_STATUSES = {"queued", "running"}


class JobEventSink(Protocol):
    async def cancel(self, task_id: str) -> bool:
        """Stop a live task this process owns; return whether one was running."""

    async def publish(self, task_id: str, event: dict[str, Any]) -> None:
        """Broadcast a task event to subscribers."""


def create_job_mutation_router(
    *,
    request_client_scope: Callable[[Request], Optional[str]],
    job_events: JobEventSink,
    reconcile_cancel: Optional[Callable[..., bool]] = None,
    reject_readonly: Optional[Callable[[Request, dict[str, Any]], None]] = None,
) -> APIRouter:
    router = APIRouter()

    @router.post("/jobs/{task_id}/cancel")
    async def cancel_job(request: Request, task_id: str) -> dict[str, Any]:
        client_id = request_client_scope(request)
        job = get_job(task_id, client_id=client_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.get("status") in _TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail=f"Job is already {job.get('status')}")

        cancelled_running_task = await job_events.cancel(task_id)
        cancelled_steps = cancel_job_steps(task_id)
        queued_before_cancel = (
            reconcile_cancel(client_id=client_id, task_id=task_id, job=job)
            if reconcile_cancel is not None
            else False
        )
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
        await job_events.publish(
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
            "queued_before_cancel": queued_before_cancel,
        }

    def _delete_job_for_request(request: Request, task_id: str) -> dict[str, Any]:
        client_id = request_client_scope(request)
        job = get_job(task_id, client_id=client_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if reject_readonly is not None:
            reject_readonly(request, job)
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

    return router
