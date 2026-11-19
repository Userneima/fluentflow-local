"""Agent routes that rerun a step or hand the agent its material: cutting
again, the visual note, and the agent's alignment, correction and note
(material, save, saved parts)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request

from backend.core import agent_alignment, agent_correction
from backend.core import agent_note, survey_cut, note_history, visual_note_job
from backend.core.local_keyframe_provider import extract_keyframes
from backend.core.local_request_scope import require_local_agent_access
from backend.core.task_reruns import start_local_debreath, start_local_visual_note
from backend.core.task_queue import queue_is_busy
from backend.routers.agent_v1 import common
from backend.routers.agent_v1.common import (
    _alignment_next,
    _current_versions,
    _job_for_request,
    _note_moved,
    _task_brief,
    _truthy_json,
)

router = APIRouter(prefix="/agent/v1", dependencies=[Depends(require_local_agent_access)])

@router.post("/tasks/{task_id}/debreath")
def debreath_agent_task(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Remove the silent gaps from a finished task's source media.

    Delegates to the local job route's entry, so an agent passes exactly the
    guards the page passes — ownership, completed-only, one render at a time,
    source present and in a supported container — instead of a second
    implementation that can drift from it.
    """
    accepted = start_local_debreath(request, task_id, background_tasks, payload)
    return {
        **accepted,
        "package_url": f"/agent/v1/tasks/{task_id}/package",
        "task": _task_brief(_job_for_request(request, task_id)),
    }


@router.post("/tasks/{task_id}/visual-note")
def visual_note_agent_task(
    request: Request,
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Write this task's note from its de-breathed media and remapped subtitles.

    Delegates to the local job route's entry, so an agent passes exactly the
    guards the page passes — ownership, completed-only, a cut file that exists,
    a cut list to remap the subtitles with, a reachable Claude, one run at a
    time — instead of a second implementation that can drift from it.

    ``preview: true`` is free and answers what would be sent and who pays, which
    is the call to make first: a plain call spends the machine owner's Claude
    allowance. ``replace_note: false`` writes the note without making it the
    task's note; ``restore_previous_note`` and ``use_generated_note`` switch
    between the two notes without a model call.
    """
    accepted = start_local_visual_note(request, task_id, background_tasks, payload)
    return {
        **accepted,
        "package_url": f"/agent/v1/tasks/{task_id}/package",
        "task": _task_brief(_job_for_request(request, task_id)),
    }


def _alignment_call(action, *args, **kwargs):
    try:
        return {"ok": True, **action(*args, **kwargs)}
    except agent_alignment.AlignmentError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/tasks/{task_id}/agent-alignment/material")
def agent_alignment_material(request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    body = payload or {}
    return _alignment_call(
        agent_alignment.material, task_id, client_id=common._local_client_scope(request),
        offset=body.get("offset", 0), material_version=body.get("material_version"),
        again=_truthy_json(body.get("again", False)),
    )


@router.post("/tasks/{task_id}/agent-alignment")
def save_agent_alignment(request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    body = payload or {}
    return _alignment_call(
        agent_alignment.save, task_id, client_id=common._local_client_scope(request),
        material_version=body.get("material_version"), sections=body.get("sections"), model=body.get("model"),
        again=_truthy_json(body.get("again", False)),
    )


def _correction_call(action, *args, **kwargs):
    try:
        return {"ok": True, **action(*args, **kwargs)}
    except agent_correction.CorrectionError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/tasks/{task_id}/agent-correction/material")
def agent_correction_material(request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    """One page of what the calling agent corrects the transcript or translates
    the subtitles from (``step``: subtitles, glossary, translation)."""
    body = payload or {}
    return _correction_call(
        agent_correction.material, task_id, body.get("step"), client_id=common._local_client_scope(request),
        page=body.get("page", 1),
    )


@router.post("/tasks/{task_id}/agent-correction")
def save_agent_correction(request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    """The calling agent's fixes (or translations) for one page; written into
    every copy of the transcript, the subtitle files and the output folder."""
    body = payload or {}
    answer = _correction_call(
        agent_correction.save, task_id, body.get("step"), client_id=common._local_client_scope(request),
        page=body.get("page"), material_version=body.get("material_version"), model=body.get("model"),
        fixes=body.get("fixes"), translations=body.get("translations"),
    )
    if isinstance(answer, dict) and answer.get("ok") is not False and not answer.get("next") \
            and answer.get("next_page") is None:
        # The last correction is in: the note, which waited for it, is next.
        job = _job_for_request(request, task_id)
        answer["next"] = agent_note.write_next(job.get("result") or {})
    return answer


@router.post("/tasks/{task_id}/agent-note/material")
def agent_note_material(
    request: Request,
    task_id: str,
    payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """What the calling agent writes this task's note from: the transcript (whole,
    or one small part with ``small_parts``), its frames as paths on this machine,
    and the note skill with the fixed contract.

    The first call for a video extracts the frames (seconds to a minute), so it
    waits for an idle queue the same way the frame note does.
    """
    part = int((payload or {}).get("part") or 1)
    asked = (payload or {}).get("small_parts")
    # Not given: as the note was being handed over (``agent_note.material``).
    small_parts = None if asked is None else bool(asked)
    # The writing rules alone, read once in small parts: no frames are taken.
    instructions_only = bool(small_parts if small_parts is not None else agent_note._small_parts(task_id)) and bool(
        (payload or {}).get("instructions_only"))
    job = _job_for_request(request, task_id)
    result = job.get("result") or {}
    # While this task's own cut renders it holds the queue, so nothing else
    # transcribes: taking frames now overlaps the render, as FluentFlow's own
    # note does (``survey_cut.ranges_while_rendering``).
    if (not instructions_only and not agent_note._stored_frames(task_id, result)
            and not survey_cut.ranges_while_rendering(result) and queue_is_busy()):
        raise HTTPException(
            status_code=409,
            detail="有任务正在处理中，等队列空下来再取材料，避免抽帧和转录同时跑。",
        )
    try:
        return {"ok": True, **agent_note.material(
            task_id, part=part, small_parts=small_parts, instructions_only=instructions_only,
            client_id=common._local_client_scope(request),
            keyframe_extractor=extract_keyframes,
        )}
    except visual_note_job.VisualNoteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/agent-note")
def save_agent_note(
    request: Request,
    task_id: str,
    payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Make the note the calling agent wrote this task's note.

    With ``part``, one part of a long recording is kept and the note is replaced
    only once every part is in. The note it replaces is kept and comes back for
    free through ``POST /tasks/{id}/visual-note`` with ``restore_previous_note``.
    """
    body = payload or {}
    part_notes = body.get("part_notes")
    checker = True if _truthy_json(body.get("model_check")) else None
    try:
        if body.get("part") is not None:
            saved = agent_note.save_part(
                task_id,
                part=int(body.get("part")),
                note_markdown=body.get("note_markdown"),
                basis_note=str(body.get("basis_note") or ""),
                model=str(body.get("model") or ""),
                client_id=common._local_client_scope(request),
                base_version=body.get("base_version"),
                checker=checker,
            )
            answer = {
                "ok": True,
                "task_id": task_id,
                "part": saved["part"],
                "total_parts": saved["total_parts"],
                "remaining_parts": saved["remaining_parts"],
                "completed": saved["completed"],
                "next": saved["next"],
                # What FluentFlow's check found this part does not say. Non-empty
                # asks the agent to write those points in and save the part again.
                "coverage": {
                    "checked": any(part.get("checked") for part in saved["coverage"]["parts"]),
                    "checked_by": saved["coverage"].get("checker") or None,
                    "missing": saved["coverage"]["missing"],
                    "parts": saved["coverage"]["parts"],
                },
            }
            if saved["job"] is not None:
                answer["previous_note_restorable"] = True
                answer["follows"] = note_history.follows(saved["job"].get("result") or {})
                answer["package_url"] = f"/agent/v1/tasks/{task_id}/package"
                align = _alignment_next(saved["job"])
                if align:
                    # Aligning reads the note at one version: a part changed
                    # after that makes the material stale (2026-10-27).
                    answer["next"] = f"{answer['next']} Once no part changes any more: {align}".strip()
            # The task's note as it is after this save: what the next save of a
            # part names (the note changes once every part is in).
            answer.update(_current_versions(task_id, request))
            return answer
        updated = agent_note.save(
            task_id,
            note_markdown=body.get("note_markdown"),
            part_notes=list(part_notes) if isinstance(part_notes, list) else None,
            basis_note=str(body.get("basis_note") or ""),
            model=str(body.get("model") or ""),
            client_id=common._local_client_scope(request),
            base_version=body.get("base_version"),
            checker=checker,
        )
    except visual_note_job.NoteMovedError as exc:
        raise _note_moved(exc) from exc
    except visual_note_job.VisualNoteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="part 要是一个整数") from exc
    coverage = visual_note_job.visual_note_state(updated.get("result")).get("coverage") or {}
    align = _alignment_next(updated)
    return {
        **({"next": align} if align else {}),
        "ok": True,
        "task_id": task_id,
        "previous_note_restorable": True,
        # What FluentFlow's own check found the note does not say. The note is
        # saved either way; a non-empty ``missing`` asks the agent to write
        # those points in and save again.
        "coverage": {
            "checked": any(part.get("checked") for part in coverage.get("parts") or []),
            # Who ran the check: ``text_model:<provider>/<model>``, or the Claude
            # channel. Empty when nothing checked.
            "checked_by": coverage.get("checker") or None,
            "missing": coverage.get("missing") or [],
            "parts": coverage.get("parts") or [],
        },
        "package_url": f"/agent/v1/tasks/{task_id}/package",
        # Where the note goes from here: the folder's md and PDF are queued,
        # not written yet when this returns (push review, 2026-11-14).
        "follows": note_history.follows(updated.get("result") or {}),
        "task": _task_brief(updated),
    }


@router.get("/tasks/{task_id}/agent-note/parts/{part}")
def get_agent_note_part(request: Request, task_id: str, part: int) -> dict[str, Any]:
    """One saved part of the agent's note, without its transcript: what the
    read-through reads, a part at a time."""
    try:
        return {"ok": True, "task_id": task_id, **agent_note.saved_part(
            task_id, part=part, client_id=common._local_client_scope(request),
        )}
    except visual_note_job.VisualNoteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
