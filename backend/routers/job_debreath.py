"""Shared breath-gap removal route, assembled with an edition-specific scope.

One factory serves both editions because nothing about removing silence is
edition-specific: the task, its source media, its artifact directory and its
ownership already exist, and the work is ffmpeg on the machine that holds the
file. What differs is injected — the hosted edition passes its desktop-sync
read-only guard and its submission rate limit; the local edition passes neither,
because every local request is local execution and there is no shared server to
meter.

Three refusals happen in the response the caller is waiting on, not several
minutes into a background task where the only trace is a status field:

- a task that is not ``completed``, which has no finished source to cut;
- a source that is missing or in a container ffmpeg cannot cut here;
- a render already in progress.

The render slot is claimed here rather than inside the worker. FastAPI runs
background tasks after the response is sent, so two quick submissions could both
pass an "is anything running?" read and both be told yes; the second would then
be refused inside a worker nobody is watching, having already answered
``accepted``. Claiming in the request path means the refusal reaches the caller.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Request

from backend.core import debreath_job, silence_cuts
from backend.core.job_store import get_job

logger = logging.getLogger(__name__)

# Only a finished task has a source that was fully fetched and a result to hang
# the outputs on. A queued or running task is still writing the file this would
# read; a failed or cancelled one may hold a partial download.
DEBREATH_ELIGIBLE_STATUSES = frozenset({"completed"})

_SETTING_RANGES = {
    "noise_db": (-90.0, 0.0, silence_cuts.DEFAULT_NOISE_DB),
    "min_silence_seconds": (0.02, 60.0, silence_cuts.DEFAULT_MIN_SILENCE_SECONDS),
    "padding_seconds": (0.0, 2.0, silence_cuts.DEFAULT_PADDING_SECONDS),
}


def parse_debreath_settings(body: Any) -> dict[str, float]:
    """Validate the three detection settings, or refuse with the range named."""
    payload = body if isinstance(body, dict) else {}
    settings: dict[str, float] = {}
    for name, (low, high, default) in _SETTING_RANGES.items():
        raw = payload.get(name)
        if raw in (None, ""):
            settings[name] = default
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"{name} 必须是数字") from exc
        if not low <= value <= high:
            raise HTTPException(
                status_code=400, detail=f"{name} 需在 {low:g} 到 {high:g} 之间"
            )
        settings[name] = value
    return settings


def start_debreath(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[dict[str, Any]] = None,
    *,
    request_client_scope: Callable[[Request], Optional[str]],
    reject_readonly: Optional[Callable[[Request, dict[str, Any]], None]] = None,
    enforce_submission_rate_limit: Optional[Callable[[Request], None]] = None,
) -> dict[str, Any]:
    """Accept a de-breath for one task, or refuse it in this response.

    Module-level rather than a closure so the Agent API can reach the same
    implementation with the same edition hooks, the way ``retry_agent_task``
    reaches the retry route. An agent submitting this must hit the guards the
    page hits, or the two surfaces drift.

    Returns as soon as the work is accepted. Rendering re-encodes and takes
    minutes, so progress is written into the job result and read back through the
    ordinary job/task endpoints rather than held open on this request.
    """
    client_id = request_client_scope(request)
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if reject_readonly is not None:
        reject_readonly(request, job)
    status = str(job.get("status") or "")
    if status not in DEBREATH_ELIGIBLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"只有已完成的任务能去气口，这个任务现在是 {status or '未知状态'}",
        )
    if enforce_submission_rate_limit is not None:
        # Each accepted call costs minutes of CPU, so it is metered like a
        # submission. The daily cost budget is about model tokens and cannot see
        # this work.
        enforce_submission_rate_limit(request)
    if debreath_job.is_running(job.get("result")):
        raise HTTPException(status_code=409, detail="这个任务的去气口正在进行中")

    settings = parse_debreath_settings(payload)
    render = bool((payload or {}).get("render", True))

    try:
        source = debreath_job.resolve_source(task_id)
    except debreath_job.DebreathError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        debreath_job.claim(task_id)
    except debreath_job.DebreathError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    def _run() -> None:
        try:
            debreath_job.run_debreath(
                task_id, client_id=client_id, render=render, claimed=True, **settings
            )
        except debreath_job.DebreathError as exc:
            logger.warning("debreath refused for %s: %s", task_id, exc)
        except Exception:  # noqa: BLE001 - already recorded on the job result
            logger.exception("debreath failed for %s", task_id)
        finally:
            # run_debreath releases its own claim; this covers the case where it
            # never got to start, so the slot cannot leak.
            debreath_job.release(task_id)

    background_tasks.add_task(_run)
    return {
        "ok": True,
        "task_id": task_id,
        "accepted": True,
        "render": render,
        "source_filename": source.name,
        "settings": settings,
    }


def create_job_debreath_router(
    *,
    request_client_scope: Callable[[Request], Optional[str]],
    reject_readonly: Optional[Callable[[Request, dict[str, Any]], None]] = None,
    enforce_submission_rate_limit: Optional[Callable[[Request], None]] = None,
) -> APIRouter:
    router = APIRouter()

    @router.post("/jobs/{task_id}/debreath")
    def start_job_debreath(
        request: Request,
        task_id: str,
        background_tasks: BackgroundTasks,
        payload: Optional[dict[str, Any]] = Body(None),
    ) -> dict[str, Any]:
        """Remove the silent gaps from a finished task's source media."""
        return start_debreath(
            request,
            task_id,
            background_tasks,
            payload,
            request_client_scope=request_client_scope,
            reject_readonly=reject_readonly,
            enforce_submission_rate_limit=enforce_submission_rate_limit,
        )

    return router
