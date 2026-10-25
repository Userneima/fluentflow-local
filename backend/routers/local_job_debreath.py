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

from backend.core import agent_cut, task_actions
from backend.core.job_store import get_job
from backend.core.request_scope import local_client_scope
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


@router.post("/jobs/{task_id}/cut-report/{ref}/flip")
def flip_cut_report_item(
    request: Request, task_id: str, ref: str, background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Turn one decision of the processing report around (``task_actions``)."""
    try:
        return task_actions.flip_cut_decision(task_id, ref, local_client_scope(request), background_tasks.add_task)
    except task_actions.Refused as exc:
        raise exc.as_http() from exc


@router.post("/jobs/{task_id}/cut-report/verdict")
def cut_report_verdict(
    request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Agree with items of the report as they are, or take it back
    (``task_actions``). Nothing is rendered."""
    body = payload or {}
    keys = [str(key) for key in body.get("keys") or [] if key]
    try:
        return task_actions.answer_cut_decisions(task_id, keys, str(body.get("verdict") or ""), local_client_scope(request))
    except task_actions.Refused as exc:
        raise exc.as_http() from exc


@router.post("/jobs/{task_id}/cut-report/changes/{index}/undo")
def undo_cut_report_change(
    request: Request, task_id: str, index: int, background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Undo one of the user's earlier changes from the report's bin (``task_actions``)."""
    try:
        return task_actions.undo_cut_change(task_id, index, local_client_scope(request), background_tasks.add_task)
    except task_actions.Refused as exc:
        raise exc.as_http() from exc


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
