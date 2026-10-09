"""Local breath-gap removal route.

The work is ffmpeg on this machine. The single render slot in ``debreath_job``
is the guard that matters, and it is claimed while the caller is still waiting
on the response.

Three refusals happen in the response the caller is waiting on, not several
minutes into a background task where the only trace is a status field:

- a task that is not ``completed``, which has no finished source to cut;
- a source that is missing or in a container ffmpeg cannot cut here;
- a render already in progress;
- a transcription running or queued, which the render must not run beside;
- a note being written for the same task, which writes the whole result back
  when it finishes and would overwrite the cut (or be overwritten by it);
- a task whose transcript was made from the automatically cut file: cutting
  that file again would leave the transcript and the note on a clock that no
  longer matches any file.

The render slot is claimed here rather than inside the worker. FastAPI runs
background tasks after the response is sent, so two quick submissions could both
pass an "is anything running?" read and both be told yes; the second would then
be refused inside a worker nobody is watching, having already answered
``accepted``. Claiming in the request path means the refusal reaches the caller.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Request

from backend.core import debreath_job, silence_cuts, visual_note_job
from backend.core.job_store import get_job
from backend.core.result_artifacts import TRANSCRIPT_MEDIA_CUT
from backend.core.request_scope import local_client_scope
from backend.routers.local_processing import queue_is_busy

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


router = APIRouter()


def note_or_transcript_conflict(job: dict[str, Any]) -> str | None:
    """Why this finished task must not be cut now, or None.

    The note and the de-breath each write the whole task result back when they
    finish; run side by side, whichever lands second erases the other. And a
    task transcribed from its own cut file has subtitles and a note timed to
    that file: cutting it again moves every gap without moving them.
    """
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    summary_status = str(result.get("summary_status") or job.get("summary_status") or "")
    if visual_note_job.is_running(result) or summary_status == "pending":
        return "这个任务的笔记正在写，等笔记写完再去气口，免得两边的结果互相覆盖。"
    state = result.get("debreath") if isinstance(result.get("debreath"), dict) else {}
    # Refused only when the transcript really was made from the cut file. A
    # declined automatic cut followed by a manual one leaves a transcript made
    # from the recording, which the remapping handles like any other re-cut.
    if (
        str(result.get("transcript_media") or "") == TRANSCRIPT_MEDIA_CUT
        and state.get("used_for_transcription") is True
        and state.get("ran_before_transcription") is True
    ):
        return (
            "这个任务转写时已经去过气口，再剪一次会和转写稿、笔记的时间点对不上；"
            "请重新提交原文件。"
        )
    return None


def start_local_debreath(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Accept a de-breath for one task, or refuse it in this response.

    The local Agent API calls this too, so an agent goes through the same guards
    the page does rather than a parallel implementation that can drift.

    Returns as soon as the work is accepted. Rendering re-encodes and takes
    minutes, so progress is written into the job result and read back through the
    ordinary job/task endpoints rather than held open on this request.
    """
    client_id = local_client_scope(request)
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    status = str(job.get("status") or "")
    if status not in DEBREATH_ELIGIBLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"只有已完成的任务能去气口，这个任务现在是 {status or '未知状态'}",
        )
    if debreath_job.is_running(job.get("result")):
        raise HTTPException(status_code=409, detail="这个任务的去气口正在进行中")
    refusal = note_or_transcript_conflict(job)
    if refusal:
        raise HTTPException(status_code=409, detail=refusal)
    if queue_is_busy():
        # The render is the memory peak of the whole product, and the serial
        # queue exists so that nothing runs beside a transcription. This entry
        # cannot join that queue (it is a background task on a finished job),
        # so it is refused while the queue is working rather than started
        # alongside it.
        raise HTTPException(
            status_code=409,
            detail="有任务正在处理中，等队列空下来再去气口，避免两件重活同时跑。",
        )

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


@router.post("/jobs/{task_id}/debreath")
def start_job_debreath(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Remove the silent gaps from a finished task's source media."""
    return start_local_debreath(request, task_id, background_tasks, payload)
