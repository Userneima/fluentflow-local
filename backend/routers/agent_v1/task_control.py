"""Agent routes that judge the cut and act on a task: the agent's cut and its
report, diagnosis, retry, the cut report's answers, cancel, stop and resume."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request

from backend.core.local_agent_package import note_generation_diagnosis
from backend.core.local_error_diagnostics import playback_audio_diagnosis
from backend.core import task_actions
from backend.core import agent_cut
from backend.core.local_request_scope import require_local_agent_access
from backend.core.task_submission import retry_for_request
from backend.routers.agent_v1 import common
from backend.routers.agent_v1.common import (
    _job_for_request,
    _task_brief,
)

router = APIRouter(prefix="/agent/v1", dependencies=[Depends(require_local_agent_access)])

def _agent_cut_call(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except agent_cut.AgentCutError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"读不出 JSON：{exc}") from exc


@router.get("/tasks/{task_id}/agent-cut/material")
def agent_cut_material(request: Request, task_id: str, part: int = 1) -> dict[str, Any]:
    """What the calling agent judges this task's cut from: the instructions
    FluentFlow's own judge gets, the user's earlier cut decisions, the
    timeline, and screenshots as paths on this machine; in parts when it is
    longer than a host shows of one answer."""
    _job_for_request(request, task_id)
    return {"ok": True, **_agent_cut_call(agent_cut.material, task_id, common._local_client_scope(request), part)}


@router.post("/tasks/{task_id}/agent-cut")
def save_agent_cut(request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    """The calling agent's decision: which long gaps to keep and which stretches
    with talk to remove, in the model's answer format. The cut follows in the
    queue; the answer lists the facts for the processing report."""
    body = payload or {}
    _job_for_request(request, task_id)
    answer = body.get("answer") if body.get("answer") is not None else {
        "keep_gaps": body.get("keep_gaps") or [], "remove": body.get("remove") or [],
    }
    return {"ok": True, **_agent_cut_call(
        agent_cut.decide, task_id, common._local_client_scope(request), answer, str(body.get("model") or ""),
    )}


@router.post("/tasks/{task_id}/agent-cut/report")
def save_agent_cut_report(request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    """The calling agent's processing report for the cut it judged."""
    body = payload or {}
    _job_for_request(request, task_id)
    answer = body.get("report") if body.get("report") is not None else body
    return {"ok": True, **_agent_cut_call(agent_cut.save_report, task_id, common._local_client_scope(request), answer)}


@router.post("/agent-cut/heartbeat")
def agent_cut_heartbeat(request: Request, payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    """An agent's MCP process reporting that its conversation is still open,
    for the tasks it submitted or is judging. Answers which to keep reporting."""
    ids = [str(item) for item in (payload or {}).get("task_ids") or [] if item]
    return {"ok": True, "watching": agent_cut.beat(ids, common._local_client_scope(request))}


@router.get("/tasks/{task_id}/diagnosis")
def get_agent_task_diagnosis(request: Request, task_id: str) -> dict[str, Any]:
    job = _job_for_request(request, task_id)
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    return {
        "ok": True,
        "task_id": task_id,
        "note": note_generation_diagnosis(job, result),
        # Why the editor has no audio to play, when making it failed; null otherwise.
        "playback_audio": playback_audio_diagnosis(result),
    }


@router.post("/tasks/{task_id}/retry")
async def retry_agent_task(request: Request, task_id: str) -> dict[str, Any]:
    # Optional JSON body {"speaker_diarization": true|false}: re-run the
    # recording with speaker separation switched on or off. Every other option
    # is the original task's.
    overrides: dict[str, str] = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - an empty or non-JSON body means no overrides
        body = None
    if isinstance(body, dict) and body.get("speaker_diarization") is not None:
        value = body.get("speaker_diarization")
        on = value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on"}
        overrides["speaker_diarization"] = "true" if on else "false"
    # {"picture_note": true}: a video no one speaks in, written up from its
    # on-screen text and picture even when it is long or nearly silent.
    picture_note = isinstance(body, dict) and body.get("picture_note") is True
    retried = await retry_for_request(request, task_id, overrides=overrides or None, picture_note=picture_note)
    next_task_id = str(retried.get("task_id") or "")
    job = retried.get("job") if isinstance(retried.get("job"), dict) else {}
    return {
        "ok": True,
        "source_task_id": task_id,
        "task_id": next_task_id,
        "status": job.get("status") or "queued",
        "package_url": f"/agent/v1/tasks/{next_task_id}/package",
        "task": _task_brief(job),
    }


def _action(call):
    """Run a task operation (``task_actions``); a refusal becomes 409 (404
    for a task that is not there) with a code and a sentence."""
    try:
        return call()
    except task_actions.Refused as exc:
        raise exc.as_http(agent=True) from exc


async def _async_action(call):
    try:
        return await call()
    except task_actions.Refused as exc:
        raise exc.as_http(agent=True) from exc


@router.get("/tasks/{task_id}/cut-report")
def get_agent_cut_report(request: Request, task_id: str, section: str = "") -> dict[str, Any]:
    return _action(lambda: task_actions.cut_report(task_id, common._local_client_scope(request), section or None))


@router.post("/tasks/{task_id}/cut-report/flip")
def flip_agent_cut_decision(
    request: Request, task_id: str, background_tasks: BackgroundTasks, payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    ref = str((payload or {}).get("ref") or "")
    return _action(lambda: task_actions.flip_cut_decision(task_id, ref, common._local_client_scope(request), background_tasks.add_task))


@router.post("/tasks/{task_id}/cut-report/verdict")
def answer_agent_cut_decisions(request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    body = payload or {}
    keys = [str(key) for key in body.get("keys") or [] if key]
    return _action(lambda: task_actions.answer_cut_decisions(task_id, keys, str(body.get("verdict") or ""), common._local_client_scope(request)))


@router.post("/tasks/{task_id}/cut-report/undo")
def undo_agent_cut_change(
    request: Request, task_id: str, background_tasks: BackgroundTasks, payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    try:
        index = int((payload or {}).get("index"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail={"code": "bad_index", "message": "index 是改动在 user_changes 里的编号"})
    return _action(lambda: task_actions.undo_cut_change(task_id, index, common._local_client_scope(request), background_tasks.add_task))


@router.post("/tasks/{task_id}/cancel")
async def cancel_agent_task(request: Request, task_id: str) -> dict[str, Any]:
    return await _async_action(lambda: task_actions.cancel_task(task_id, common._local_client_scope(request)))


@router.post("/tasks/{task_id}/stop")
async def stop_agent_task_step(request: Request, task_id: str) -> dict[str, Any]:
    return await _async_action(lambda: task_actions.stop_step(task_id, common._local_client_scope(request)))


@router.post("/tasks/{task_id}/resume")
async def resume_agent_task(request: Request, task_id: str) -> dict[str, Any]:
    return await _async_action(lambda: task_actions.resume_task(task_id, common._local_client_scope(request)))
