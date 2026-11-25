"""Agent routes for how notes are written: the note skill and its rules, the
user's feedback on notes, and drafting one part with candidate rules."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from backend.core import note_feedback, note_skills, study_record, visual_note_job
from backend.core.local_request_scope import require_local_agent_access
from backend.routers.agent_v1 import common
from backend.routers.agent_v1.common import _job_for_request

router = APIRouter(prefix="/agent/v1", dependencies=[Depends(require_local_agent_access)])

def _feedback_call(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except (note_feedback.NoteFeedbackError, study_record.StudyError, visual_note_job.VisualNoteError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/note-skill")
def get_note_skill() -> dict[str, Any]:
    """The note skill in effect and every rule that was added to it from feedback."""
    skill = note_skills.load_note_skill()
    return {
        "ok": True,
        "text": skill.text,
        "rules": skill.rules,
        "is_default": skill.is_default,
        "version": skill.version,
        "path": str(skill.path),
        "rules_log": note_feedback.rules_log(),
    }


@router.post("/note-skill/rules")
def apply_note_rule(payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    """Write a rule the agent has verified into the user's skill."""
    body = payload or {}
    ids = body.get("feedback_ids")
    return {"ok": True, **_feedback_call(
        note_feedback.apply_rule,
        skill_text=str(body.get("skill_text") or ""),
        rule=str(body.get("rule") or ""),
        feedback_ids=[str(i) for i in ids] if isinstance(ids, list) else [],
        rounds=int(body.get("rounds") or 0),
        evidence=str(body.get("evidence") or ""),
        base_version=str(body.get("base_version") or "") or None,
    )}


@router.get("/note-feedback")
def list_note_feedback() -> dict[str, Any]:
    reviewed = note_feedback.reviewed_studies()
    return {"ok": True, "feedback": [note_feedback.public_view(item, reviewed) for item in note_feedback.list_feedback()]}


@router.post("/note-feedback")
def record_note_feedback(request: Request, payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    """Keep one problem the user reported about a note as a test case."""
    body = payload or {}
    task_id = str(body.get("task_id") or "")
    title = ""
    if task_id:
        result = _job_for_request(request, task_id).get("result") or {}
        title = str(result.get("display_title") or result.get("filename") or "")
    part = body.get("part")
    return {"ok": True, "feedback": note_feedback.public_view(_feedback_call(
        note_feedback.record_feedback,
        task_id=task_id,
        problem=str(body.get("problem") or ""),
        kind=str(body.get("kind") or ""),
        criterion=str(body.get("criterion") or ""),
        note_excerpt=str(body.get("note_excerpt") or ""),
        part=int(part) if part else None,
        transcript_excerpt=str(body.get("transcript_excerpt") or ""),
        title=title,
        study_id=str(body.get("study_id") or ""),
    ))}


@router.post("/tasks/{task_id}/studied")
def mark_studied(request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    """The user said they finished studying this note (or took it back)."""
    body = payload or {}
    _job_for_request(request, task_id)
    studied = body.get("studied", True)
    if not isinstance(studied, bool):
        raise HTTPException(status_code=422, detail="studied must be true or false")
    return {"ok": True, **_feedback_call(
        study_record.mark, task_id, studied=studied, verdict=body.get("verdict") or None,
        client_id=common._local_client_scope(request),
    )}


@router.post("/tasks/{task_id}/note-draft")
def draft_note_part(request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    """One part of the note written by the product's own writer with candidate rules; not saved."""
    body = payload or {}
    return {"ok": True, **_feedback_call(
        note_feedback.draft_part,
        task_id,
        rules=str(body.get("rules") or ""),
        part=int(body.get("part") or 1),
        client_id=common._local_client_scope(request),
    )}
