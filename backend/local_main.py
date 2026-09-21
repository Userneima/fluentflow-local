"""FluentFlow Local edition backend composition root.

Assembles ONLY local routers on the shared app factory: no accounts, quota,
admin, OSS, ElevenLabs, hosted OAuth, or desktop-sync surfaces exist in this
application. The HTTP boundary middleware keeps the server loopback-only by
default, and startup recovers jobs stranded by a previous shutdown.

Classification note: ``local_ready`` — its complete import graph is restricted
to local/shared code. The route-contract test (`tests/test_local_main.py`)
proves the assembled app serves every required local route family and none of
the forbidden ones.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.core.local_config import load_project_env

# Load repository-local configuration before modules freeze default DB/event paths.
load_project_env()

from backend.core.app_factory import create_app
from backend.core.debreath_job import recover_stranded_renders
from backend.core.frontend_paths import FRONTEND_LOCAL_DIST_DIR
from backend.core.job_store import list_jobs_by_statuses, upsert_job
from backend.core.local_http_boundary import local_boundary_middleware
from backend.core.local_readiness import (
    failed_required_checks,
    format_report,
    run_readiness_checks,
)
from backend.core.visual_note_job import recover_stranded_notes
from backend.routers.local_agent import router as agent_router
from backend.routers.local_events import router as events_router
from backend.routers.local_feishu_export import router as feishu_export_router
from backend.routers.local_job_debreath import router as job_debreath_router
from backend.routers.local_job_edit import router as job_edit_router
from backend.routers.local_job_mutation import router as job_mutation_router
from backend.routers.local_job_read import router as job_read_router
from backend.routers.local_job_source import router as job_source_router
from backend.routers.local_job_visual_note import router as job_visual_note_router
from backend.routers.local_jobs import router as jobs_router
from backend.routers.local_note_regen import router as note_regen_router
from backend.routers.local_processing import router as processing_router
from backend.routers.local_spa import create_local_spa_router
from backend.routers.local_system import router as system_router
from backend.routers.local_video_sources import router as video_sources_router

logger = logging.getLogger(__name__)

LOCAL_API_ROUTERS = (
    system_router,
    events_router,
    jobs_router,
    job_read_router,
    job_source_router,
    job_mutation_router,
    job_edit_router,
    job_debreath_router,
    job_visual_note_router,
    processing_router,
    video_sources_router,
    note_regen_router,
    feishu_export_router,
    agent_router,
)


def recover_stale_jobs() -> int:
    """Mark jobs stranded by a previous shutdown as failed.

    Local workers are process-local (hub + serial queue chain), so a
    queued/running job from a previous run can never resume by itself; leaving
    it would show as stuck forever. Failed state keeps the local retry action
    available when the stored source still exists.
    """
    recovered = 0
    for job in list_jobs_by_statuses(("queued", "running")):
        task_id = str(job.get("task_id") or "")
        if not task_id:
            continue
        upsert_job(
            task_id=task_id,
            status="failed",
            client_id=job.get("client_id"),
            stage="recovery",
            progress=0,
            error_reason="服务重启中断了这个任务：请重新提交，或对仍保留源文件的任务使用重试。",
        )
        recovered += 1
    return recovered


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Readiness is advisory at startup: the server still boots (the SPA
    # router and diagnostics give readable errors), but failures are logged
    # up front so they are visible before the first task fails mid-way.
    checks = run_readiness_checks()
    failed = failed_required_checks(checks)
    if failed:
        logger.warning("Local readiness issues:\n%s", format_report(checks))
    else:
        logger.info("Local readiness: all required checks passed")
    recovered = recover_stale_jobs()
    if recovered:
        logger.info("Startup recovery marked %s stranded local jobs as failed", recovered)
    # The de-breath render slot lives in process memory, so a service killed
    # mid-encode leaves a completed task saying its de-breath is still running —
    # and the route refuses that state, which would make the entry dead for good.
    stranded_renders = recover_stranded_renders()
    if stranded_renders:
        logger.info("Startup recovery cleared %s stranded de-breath renders", stranded_renders)
    # Same shape, same reason: the visual-note slot is process memory, and the
    # entry refuses a task whose note still says "running".
    stranded_notes = recover_stranded_notes()
    if stranded_notes:
        logger.info("Startup recovery cleared %s stranded visual notes", stranded_notes)
    yield


def create_local_app() -> FastAPI:
    return create_app(
        title="FluentFlow Local",
        api_routers=LOCAL_API_ROUTERS,
        spa_router=create_local_spa_router(),
        frontend_dist_dir=FRONTEND_LOCAL_DIST_DIR,
        lifespan=lifespan,
        http_middleware=(local_boundary_middleware,),
    )


app = create_local_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.local_main:app",
        host=os.environ.get("FLUENTFLOW_LOCAL_HOST", "127.0.0.1"),
        port=int(os.environ.get("FLUENTFLOW_LOCAL_PORT", "8000") or 8000),
    )
