"""Read the user's note-writing rules and restore a kept version.

Preference changes belong to the user's agent. Reading this page never reads
recordings or task results and never calls a model.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from backend.core import note_feedback, note_skills

router = APIRouter()


def _saved_at(name: str) -> str:
    stamp = name[:15]
    if len(stamp) == 15 and stamp[8] == "-":
        return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}"
    return name


@router.get("/note-style")
def read_note_style() -> dict[str, Any]:
    skill = note_skills.load_note_skill()
    return {
        "skill": {"text": skill.text, "version": skill.version, "is_default": skill.is_default},
        "history": [
            {"name": path.name, "saved_at": _saved_at(path.name)} for path in note_skills.skill_history()
        ],
        "rules_log": note_feedback.rules_log(),
    }


@router.post("/note-style/restore")
def restore_note_style(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        note_skills.restore_user_skill(
            name=str(payload.get("name") or "") or None,
            use_default=payload.get("use_default") is True,
            base_version=str(payload.get("base_version") or ""),
        )
    except note_skills.NoteSkillConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return read_note_style()


# Old tabs must not start the retired model calls. Keep their runtime files
# (candidates/proposal/analyzed) untouched; they are no longer read or applied.
@router.get("/note-style/preview-tasks")
@router.post("/note-style/candidates/from-edits")
@router.post("/note-style/candidates/from-examples")
@router.post("/note-style/candidates/{candidate_id}/dismiss")
@router.post("/note-style/proposal")
@router.post("/note-style/proposal/apply")
@router.post("/note-style/proposal/discard")
def retired_note_style_flow() -> None:
    raise HTTPException(status_code=410, detail="修改笔记写法请告诉你的 agent；设置页可查看当前写法和撤回修改。")
