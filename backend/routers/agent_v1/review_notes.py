"""Agent routes for the review note written beside the follow-along note:
write, the agent's material and save, revise, replace text."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from backend.core.local_request_scope import require_local_agent_access
from backend.routers.agent_v1.common import _job_for_request

router = APIRouter(prefix="/agent/v1", dependencies=[Depends(require_local_agent_access)])

def _review_note_call(request: Request, payload: dict[str, Any], run: Any) -> dict[str, Any]:
    """Shared by the two review-note routes: the task (when named) must be
    this caller's; a reason the note was not written comes back as 422, and
    the task itself is never failed (decision 5.37)."""
    from backend.core import review_note

    task_id = str(payload.get("task_id") or "").strip() or None
    note_path = str(payload.get("note_path") or "").strip() or None
    if task_id:
        _job_for_request(request, task_id)
    try:
        return {"ok": True, **run(review_note, task_id, note_path)}
    except review_note.ReviewNoteError as exc:
        conflict = ("already_running", "stale", "not_found_in_note", "ambiguous", "overlapping")
        raise HTTPException(status_code=409 if exc.code in conflict else 422,
                            detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/review-notes")
def write_review_note(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Write the review note beside a task's follow-along note, or beside the
    md at ``note_path`` when the output folder was moved."""
    return _review_note_call(request, payload, lambda core, task_id, note_path: core.generate(task_id, note_path))


def _instruction(payload: dict[str, Any]) -> str | None:
    """Revising when the agent passes the user's feedback, writing when not."""
    value = payload.get("instruction")
    return None if value is None else str(value)


@router.post("/review-notes/agent-material")
def review_note_agent_material(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """One part of the review note's material, for the agent to write (or,
    with ``instruction``, revise) it itself (decision 5.47)."""
    return _review_note_call(request, payload, lambda core, task_id, note_path: core.agent_material(
        task_id, note_path, _instruction(payload), int(payload.get("part") or 1)))


@router.post("/review-notes/agent")
def save_review_note_from_agent(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Write the review note the agent wrote from that material."""
    return _review_note_call(request, payload, lambda core, task_id, note_path: core.save_from_agent(
        str(payload.get("note_markdown") or ""), str(payload.get("material_version") or ""),
        task_id=task_id, note_path=note_path, instruction=_instruction(payload), model=str(payload.get("model") or "")))


@router.post("/review-notes/revise")
def revise_review_note(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Change an existing review note as the user asked (``instruction``),
    without writing it again from the follow-along note."""
    instruction = str(payload.get("instruction") or "")

    return _review_note_call(
        request, payload,
        lambda core, task_id, note_path: core.revise(note_path, instruction, task_id=task_id),
    )


@router.post("/review-notes/replace")
def replace_review_note_text(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Change passages of the review note by their exact text, the way
    ``/tasks/{id}/note/replace`` does for the follow-along note."""
    return _review_note_call(request, payload, lambda core, task_id, note_path: core.replace_text(
        payload.get("old_text"), payload.get("new_text"), task_id=task_id, note_path=note_path,
        replacements=payload.get("replacements"), base_version=payload.get("base_version"),
        reason=payload.get("reason")))
