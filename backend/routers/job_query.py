"""Shared job-query router assembled with an edition-specific client scope and
task projection policy.

Reads only: list jobs, read one job, read one job's processing detail. Job
mutation, cancellation, retry, artifacts, and SSE stay out of this router.

The job store is edition-neutral and imported directly. Only the two concerns
that differ between the hosted and local editions are injected:

- ``request_client_scope`` — how a request maps to an isolation scope;
- ``build_task_snapshot`` / ``build_task_detail`` — the edition's task
  projection policy.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from backend.core.job_store import (
    get_job,
    list_job_steps,
    list_job_summaries,
    list_jobs,
)


def create_job_query_router(
    *,
    request_client_scope: Callable[[Request], Optional[str]],
    build_task_snapshot: Callable[..., dict[str, Any]],
    build_task_detail: Callable[..., dict[str, Any]],
) -> APIRouter:
    router = APIRouter()

    @router.get("/jobs")
    def get_jobs(request: Request, limit: int = 50, include_result: bool = False) -> dict[str, Any]:
        client_id = request_client_scope(request)
        jobs = (
            list_jobs(limit=limit, client_id=client_id)
            if include_result
            else list_job_summaries(limit=limit, client_id=client_id)
        )
        return {"jobs": [{**job, "task_snapshot": build_task_snapshot(job)} for job in jobs]}

    @router.get("/jobs/{task_id}")
    def get_job_detail(request: Request, task_id: str) -> dict[str, Any]:
        job = get_job(task_id, client_id=request_client_scope(request))
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return {**job, "task_snapshot": build_task_snapshot(job)}

    @router.get("/jobs/{task_id}/detail")
    def get_job_processing_detail(request: Request, task_id: str) -> dict[str, Any]:
        job = get_job(task_id, client_id=request_client_scope(request))
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        steps = list_job_steps(task_id=task_id, limit=100)
        return build_task_detail(job, job_steps=steps)

    return router
