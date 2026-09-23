"""Local job-query routes: list jobs, read one job, read one job's processing
detail. Mutation, cancellation, retry, artifacts, and SSE live elsewhere.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from backend.core.job_store import (
    get_job,
    list_job_steps,
    list_job_summaries,
    list_jobs,
)
from backend.core.local_request_scope import request_client_id
from backend.core.local_task_detail import build_task_detail, build_task_snapshot


router = APIRouter()


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


@router.get("/jobs")
def get_jobs(request: Request, limit: int = 50, include_result: bool = False) -> dict[str, Any]:
    client_id = _local_client_scope(request)
    jobs = (
        list_jobs(limit=limit, client_id=client_id)
        if include_result
        else list_job_summaries(limit=limit, client_id=client_id)
    )
    return {"jobs": [{**job, "task_snapshot": build_task_snapshot(job)} for job in jobs]}


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
