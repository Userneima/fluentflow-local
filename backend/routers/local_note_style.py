"""「我的笔记风格」: the user's own note skill, built from their corrections.

Read the skill and what is waiting on the user; ask Claude for candidate rules
from edited notes or from notes the user likes; turn chosen rules into a
pending change compared on one task; apply or discard it. Nothing changes the
skill except ``apply``, after the comparison has been shown.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from backend.core import note_skills, note_style, visual_note_channel
from backend.core import visual_note_job as vn
from backend.core.job_store import list_job_summaries
from backend.core.local_config import resolve_secret
from backend.core.request_scope import local_client_scope

router = APIRouter()

PREVIEW_TASK_LIMIT = 12


def _skill_payload(skill: note_skills.NoteSkill) -> dict[str, Any]:
    return {"text": skill.text, "version": skill.version, "is_default": skill.is_default}


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/note-style")
def read_note_style(request: Request) -> dict[str, Any]:
    client_id = local_client_scope(request)
    unread = note_style._unread(note_style.edited_notes(client_id=client_id))
    return {
        "skill": _skill_payload(note_skills.load_note_skill()),
        "default_skill": _skill_payload(note_skills.default_skill()),
        "history": [
            {"name": path.name, "saved_at": path.name[:15]} for path in note_skills.skill_history()
        ],
        "candidates": note_style.candidates(),
        "unread_edits": len(unread),
        "proposal": note_style.proposal(),
    }


@router.get("/note-style/preview-tasks")
def preview_tasks(request: Request) -> dict[str, Any]:
    """Recent tasks a comparison can be written on: they have a frame note and
    the recording it was written from is still here."""
    client_id = local_client_scope(request)
    channel = visual_note_channel.resolve_channel(resolve_secret(None, "anthropic_api_key"))
    tasks = []
    for summary in list_job_summaries(limit=200, client_id=client_id):
        if summary.get("status") != "completed":
            continue
        job = vn.get_job(summary["task_id"], client_id=client_id)
        result = (job or {}).get("result") or {}
        if not vn.visual_note_state(result).get("frames_sent"):
            continue
        if not vn.describe(summary["task_id"], job, api_key=None, channel=channel).get("eligible"):
            continue
        tasks.append({
            "task_id": summary["task_id"],
            "title": result.get("display_title") or summary.get("source_filename") or summary["task_id"],
            "updated_at": summary.get("updated_at"),
        })
        if len(tasks) >= PREVIEW_TASK_LIMIT:
            break
    return {"tasks": tasks}


@router.post("/note-style/candidates/from-edits")
def candidates_from_edits(request: Request) -> dict[str, Any]:
    try:
        added = note_style.propose_from_edits(client_id=local_client_scope(request))
    except note_style.NoteStyleError as exc:
        raise _fail(exc) from exc
    return {"added": added, "candidates": note_style.candidates()}


@router.post("/note-style/candidates/from-examples")
def candidates_from_examples(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    notes = payload.get("notes")
    if not isinstance(notes, list):
        raise HTTPException(status_code=400, detail="notes 要是笔记文本的列表")
    try:
        added = note_style.propose_from_examples([str(note) for note in notes])
    except note_style.NoteStyleError as exc:
        raise _fail(exc) from exc
    return {"added": added, "candidates": note_style.candidates()}


@router.post("/note-style/candidates/{candidate_id}/dismiss")
def dismiss_candidate(candidate_id: str) -> dict[str, Any]:
    note_style.dismiss(candidate_id)
    return {"candidates": note_style.candidates()}


@router.post("/note-style/proposal")
def create_proposal(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="先选一个用来对比的任务")
    restore = str(payload.get("restore") or "").strip()
    skill_text = None
    if restore:
        match = next((path for path in note_skills.skill_history() if path.name == restore), None)
        if match is None:
            raise HTTPException(status_code=404, detail="找不到这个旧版本")
        skill_text = match.read_text(encoding="utf-8")
    elif payload.get("use_default"):
        skill_text = note_skills.default_skill().text
    try:
        record = note_style.start_proposal(
            candidate_ids=[str(item) for item in payload.get("candidate_ids") or []],
            skill_text=skill_text,
            task_id=task_id,
            client_id=local_client_scope(request),
        )
    except note_style.NoteStyleError as exc:
        raise _fail(exc) from exc
    return {"proposal": record}


@router.post("/note-style/proposal/apply")
def apply_proposal() -> dict[str, Any]:
    try:
        skill = note_style.apply_proposal()
    except note_style.NoteStyleError as exc:
        raise _fail(exc) from exc
    return {"skill": _skill_payload(skill), "candidates": note_style.candidates()}


@router.post("/note-style/proposal/discard")
def discard_proposal() -> dict[str, Any]:
    note_style.discard_proposal()
    return {"proposal": None}
