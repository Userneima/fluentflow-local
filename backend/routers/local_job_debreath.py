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
import threading
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Request

from backend.core import agent_cut, debreath_job, local_intake_flow, original_locator, survey_cut, task_activity, task_steps
from backend.core.job_store import get_job
from backend.core.request_scope import local_client_scope
from backend.core.task_queue import queue_is_busy
from backend.core.task_reruns import start_local_debreath

logger = logging.getLogger(__name__)

router = APIRouter()




@router.post("/jobs/{task_id}/debreath")
def start_job_debreath(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Remove the silent gaps from a finished task's source media."""
    return start_local_debreath(request, task_id, background_tasks, payload)


# Re-renders taken for a task and not finished yet, report and note included.
_accepted: set[str] = set()
_accepted_lock = threading.Lock()


def _accept_rerender(task_id: str, client_id: str | None, background_tasks: BackgroundTasks, work) -> dict[str, Any]:
    """Refuse a re-render in this response for the same reasons as any other
    render, else run ``work`` and then write the note again from the new cut."""
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if debreath_job.is_running(result):
        raise HTTPException(status_code=409, detail="这个任务正在重新导出，等这次导完")
    current = task_activity.activity(job)
    if current.busy and task_steps.NOTE in current.open_steps:
        raise HTTPException(status_code=409, detail="笔记正在写，等写完再改，免得两边的结果互相覆盖。")
    if current.busy:
        raise HTTPException(status_code=409, detail=f"这个任务{current.label or '还在处理'}，等它结束再改。")
    if queue_is_busy():
        raise HTTPException(status_code=409, detail="有任务正在处理中，等队列空下来再改。")
    with _accepted_lock:
        if task_id in _accepted:
            raise HTTPException(status_code=409, detail="这个任务正在重新导出，等这次导完")
        try:
            original_locator.locate(task_id, client_id)
            debreath_job.claim(task_id)
        except (debreath_job.DebreathError, original_locator.OriginalMissing) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # Held until the work, the report and the note are done, not only the
        # render: a second click while the report is written would start from
        # decisions this one is about to replace.
        _accepted.add(task_id)

    def _run() -> None:
        try:
            try:
                work(True)
            except Exception:  # noqa: BLE001 - recorded on the survey
                logger.exception("re-render of %s failed", task_id)
                return
            latest = (get_job(task_id, client_id=client_id) or {}).get("result") or {}
            # The note was written from the earlier cut; it is written again from
            # this one so it matches.
            if not latest.get("note_declined") and (latest.get("summary_markdown") or local_intake_flow.note_is_wanted(task_id, client_id)):
                local_intake_flow.mark_note_running(task_id, client_id)
                local_intake_flow.write_note(task_id, client_id)
        finally:
            debreath_job.release(task_id)
            with _accepted_lock:
                _accepted.discard(task_id)

    background_tasks.add_task(_run)
    return {"ok": True, "task_id": task_id, "accepted": True}


@router.post("/jobs/{task_id}/cut-report/{ref}/flip")
def flip_cut_report_item(
    request: Request, task_id: str, ref: str, background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Turn one decision of the processing report around: cut becomes kept,
    kept becomes cut (``survey_cut.flip``)."""
    client_id = local_client_scope(request)
    return _accept_rerender(task_id, client_id, background_tasks, lambda claimed: survey_cut.flip(task_id, ref, client_id=client_id, claimed=claimed))


@router.post("/jobs/{task_id}/cut-report/verdict")
def cut_report_verdict(
    request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Record that the user agrees with items of the report as they are, or
    take it back (``survey_cut.verdict``). Nothing is rendered."""
    client_id = local_client_scope(request)
    body = payload or {}
    keys = [str(key) for key in body.get("keys") or [] if key]
    try:
        return {"ok": True, **survey_cut.verdict(task_id, keys, str(body.get("verdict") or ""), client_id=client_id)}
    except survey_cut.ReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/jobs/{task_id}/cut-report/changes/{index}/undo")
def undo_cut_report_change(
    request: Request, task_id: str, index: int, background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Undo one of the user's earlier changes from the report's bin
    (``survey_cut.undo_change``)."""
    client_id = local_client_scope(request)
    return _accept_rerender(task_id, client_id, background_tasks, lambda claimed: survey_cut.undo_change(task_id, index, client_id=client_id, claimed=claimed))


@router.post("/jobs/{task_id}/agent-cut/self-judge")
def agent_cut_self_judge(request: Request, task_id: str) -> dict[str, Any]:
    """Stop waiting for the user's agent: FluentFlow judges the cut itself
    (``agent_cut``). The waiting task picks this up within seconds."""
    client_id = local_client_scope(request)
    if not get_job(task_id, client_id=client_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    if not agent_cut.give_up(task_id, client_id, "你在网页上选了让 FluentFlow 自己判断"):
        raise HTTPException(status_code=409, detail="这个任务已经不在等 agent 判断了。")
    return {"ok": True}
