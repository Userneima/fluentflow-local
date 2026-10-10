"""FluentFlow Local edition backend composition root.

Assembles ONLY local routers on the shared app factory: no accounts, quota,
admin, OSS, cloud transcription, hosted OAuth, or desktop-sync surfaces exist in this
application. The HTTP boundary middleware keeps the server loopback-only by
default, and startup recovers jobs stranded by a previous shutdown.

Classification note: ``local_ready`` — its complete import graph is restricted
to local/shared code. The route-contract test (`tests/test_local_main.py`)
proves the assembled app serves every required local route family and none of
the forbidden ones.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException

from backend.core import hf_endpoint
from backend.core.local_config import load_project_env

# Load repository-local configuration before modules freeze default DB/event paths.
load_project_env()
# Choose the model download host before anything imports `huggingface_hub`,
# which reads HF_ENDPOINT into a constant at import time. An HF_ENDPOINT from
# .env or the shell is honoured without a probe; a failed probe never blocks
# startup. Without this the service had no mirror fallback: only the installer
# did, and a first task on a machine whose install-time download failed waited
# forever on a host it could not reach.
hf_endpoint.apply_at_startup()

from backend.core.app_factory import create_app
from backend.core import claude_code_note, visual_note_channel  # noqa: E402
from backend.core.event_context import event_metadata  # noqa: E402
from backend.core.event_logger import log_event  # noqa: E402
from backend.core.claude_code_note import stop_running_notes
from backend.core.debreath_job import recover_stranded_renders
from backend.core.frontend_paths import FRONTEND_LOCAL_DIST_DIR
from backend.core.job_store import (
    RESTART_INTERRUPTION_KEY,
    adopt_jobs_for_owner,
    get_job,
    list_jobs_by_statuses,
    repair_truncated_display_titles,
    sync_summary_status_column,
    upsert_job,
)
from backend.core.local_http_boundary import local_boundary_middleware
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.core.local_readiness import (
    failed_required_checks,
    format_report,
    run_readiness_checks,
)
from backend.core.storage_paths import _video_source_storage_dir
from backend.core.video_source import clean_interrupted_downloads
from backend.core.visual_note_job import recover_stranded_notes
from backend.routers.local_agent import router as agent_router
from backend.routers.local_events import router as events_router
from backend.routers.local_feishu_export import router as feishu_export_router
from backend.routers.local_job_debreath import router as job_debreath_router
from backend.routers.local_job_edit import router as job_edit_router
from backend.routers.local_job_mutation import router as job_mutation_router
from backend.routers.local_job_read import router as job_read_router
from backend.routers.local_job_visual_note import router as job_visual_note_router
from backend.routers.local_jobs import router as jobs_router
from backend.routers.local_note_regen import router as note_regen_router
from backend.routers.local_note_style import router as note_style_router
from backend.routers.local_processing import retry_task
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
    job_mutation_router,
    job_edit_router,
    job_debreath_router,
    job_visual_note_router,
    processing_router,
    video_sources_router,
    note_regen_router,
    note_style_router,
    feishu_export_router,
    agent_router,
)


# How many restarts in a row may cut a task off mid-run before startup stops
# re-queueing it on its own. A task that was only waiting lost nothing and does
# not count. The limit is for the one that is itself what brings the service
# down — re-running it on every start would take the service down on every start.
RESTART_RESUME_LIMIT = 2
RESTART_RESUME_COUNT_KEY = "restart_resume_count"
RESUMED_ERROR_REASON = "服务重启中断了这个任务，已自动重新排队处理，结果在新的那条记录里。"


def recover_stale_jobs() -> int:
    """Mark jobs stranded by a previous shutdown as failed.

    Local workers are process-local (hub + serial queue chain), so a
    queued/running job from a previous run can never resume by itself; leaving
    it would show as stuck forever. Failed state keeps the local retry action
    available when the stored source still exists.

    Each one is also stamped with ``restart_interruption`` so the app can tell
    the user once, on whichever page is open, that these were cut off by the
    restart — a failed card among many is easy to miss — and offer to re-run
    them. Whether it had started matters to the reader: a queued task lost
    nothing, a running one lost its progress.
    """
    recovered = 0
    interrupted_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
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
            metadata={
                RESTART_INTERRUPTION_KEY: {
                    "interrupted_at": interrupted_at,
                    "started": job.get("status") == "running",
                    "stage": job.get("stage"),
                    "progress": job.get("progress"),
                },
                # A job that was waiting its turn is not waiting any more.
                "queue_wait": None,
            },
        )
        recovered += 1
    return recovered


def _interrupted_on_this_start(task_ids: list[str]) -> list[dict]:
    jobs = [job for job in (get_job(task_id) for task_id in task_ids) if job]
    # Oldest first, so the queue comes back in the order it was submitted.
    return sorted(jobs, key=lambda job: str(job.get("created_at") or ""))


async def resume_interrupted_jobs(task_ids: list[str]) -> int:
    """Queue again, through the user's own retry path, what the restart cut off.

    Nearly every restart on record was an agent reloading code while its own
    batch was queued, and the user found out only by seeing a column of failed
    cards. Nothing about those tasks needed a decision, so none is asked for.

    What cannot be re-run here is left exactly as ``recover_stale_jobs`` wrote
    it, and the interruption notice still offers it to the user: the recording
    is gone, the task was a link that never finished downloading, or restarts
    have cut it off mid-run too many times in a row to try again unasked.

    A re-queued task is marked as told, so the notice does not ask about work
    that is already running again, and its row says where the result went.
    """
    resumed = 0
    for job in _interrupted_on_this_start(task_ids):
        task_id = str(job.get("task_id") or "")
        metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
        interruption = metadata.get(RESTART_INTERRUPTION_KEY)
        if not isinstance(interruption, dict):
            continue
        folder_intake = metadata.get("folder_intake") if isinstance(metadata.get("folder_intake"), dict) else {}
        count = int(metadata.get(RESTART_RESUME_COUNT_KEY) or folder_intake.get(RESTART_RESUME_COUNT_KEY) or 0)
        if interruption.get("started"):
            count += 1
        if count > RESTART_RESUME_LIMIT:
            logger.info("Startup left %s for the user: restarts cut it off %s times", task_id, count)
            _log_restart_interruption(job, interruption, outcome="left_for_user", reason="restart_limit", count=count)
            continue
        try:
            retried = await retry_task(
                task_id,
                client_id=job.get("client_id"),
                local_caller=True,
                carry={RESTART_RESUME_COUNT_KEY: count, "restart_resumed_from": task_id},
            )
        except HTTPException as exc:
            logger.info("Startup could not re-queue %s: %s", task_id, exc.detail)
            _log_restart_interruption(job, interruption, outcome="left_for_user", reason=str(exc.detail), count=count)
            continue
        except Exception as exc:
            logger.exception("Startup could not re-queue %s", task_id)
            _log_restart_interruption(job, interruption, outcome="left_for_user", reason=f"{type(exc).__name__}: {exc}", count=count)
            continue
        new_task_id = str(retried.get("task_id") or "")
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        upsert_job(
            task_id=task_id,
            status="failed",
            client_id=job.get("client_id"),
            error_reason=RESUMED_ERROR_REASON,
            metadata={
                RESTART_INTERRUPTION_KEY: {
                    **interruption,
                    "acknowledged_at": now,
                    "resumed_as": new_task_id,
                },
            },
        )
        _log_restart_interruption(job, interruption, outcome="resumed", resumed_as=new_task_id, count=count)
        resumed += 1
    return resumed


def _log_restart_interruption(
    job: dict, interruption: dict, *, outcome: str, count: int,
    reason: str | None = None, resumed_as: str | None = None,
) -> None:
    """One event per task a restart cut off, and what happened to it.

    Restarts that cut off queued work were the most common failure on record,
    and until this they showed up only as failed tasks, indistinguishable in the
    event log from a task that failed on its own.
    """
    log_event(
        task_id=str(job.get("task_id") or ""),
        event_name="task_interrupted_by_restart",
        source_type=str(job.get("source_type") or "") or None,
        stage=str(interruption.get("stage") or job.get("stage") or "") or None,
        success=outcome == "resumed",
        error_reason=reason,
        metadata=event_metadata(
            outcome=outcome,
            was_running=bool(interruption.get("started")),
            restart_count=count,
            resumed_as=resumed_as,
        ),
    )


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
    adopted = adopt_jobs_for_owner(LOCAL_OWNER_ID)
    if adopted:
        logger.info("Startup moved %s tasks filed under other client ids to the local owner", adopted)
    repaired = repair_truncated_display_titles()
    if repaired:
        logger.info("Startup restored %s task titles cut at their first dot", repaired)
    synced = sync_summary_status_column()
    if synced:
        logger.info("Startup corrected the note status of %s tasks", synced)
    if visual_note_channel.preferred_channel() == visual_note_channel.CHANNEL_SUBSCRIPTION:
        # Read the Claude login before anything is queued, so the first task
        # already knows whether its note can be written from the frames or has
        # to fall back to the text note. Off the event loop; a few seconds once.
        logged_in = await asyncio.to_thread(claude_code_note.login_state)
        if logged_in is False:
            logger.warning("Claude on this machine is not logged in; notes fall back to the text model")
    # Before anything is re-queued: a killed service left its link downloads'
    # scratch folders behind, and the yt-dlp writing into one kept running.
    leftovers = await asyncio.to_thread(clean_interrupted_downloads, _video_source_storage_dir())
    if leftovers["removed"] or leftovers["stopped"]:
        logger.info(
            "Startup removed %s interrupted link downloads (stopped %s still-running yt-dlp)",
            leftovers["removed"], leftovers["stopped"],
        )
    interrupted = [str(job.get("task_id")) for job in list_jobs_by_statuses(("queued", "running"))]
    recovered = recover_stale_jobs()
    if recovered:
        logger.info("Startup recovery marked %s stranded local jobs as failed", recovered)
    resume = None
    if interrupted:
        # Off the startup path: each re-queue probes its file, and the page
        # should not wait on that to load.
        async def _resume() -> None:
            resumed = await resume_interrupted_jobs(interrupted)
            if resumed:
                logger.info("Startup re-queued %s tasks the restart cut off", resumed)

        resume = asyncio.create_task(_resume())
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
    if resume is not None and not resume.done():
        resume.cancel()
    stopped = stop_running_notes()
    if stopped:
        logger.info("Shutdown stopped %s note writers that were still running", stopped)


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
