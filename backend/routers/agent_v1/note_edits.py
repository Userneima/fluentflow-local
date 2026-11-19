"""Agent routes that change a finished note or transcript: note versions and
restoring one, replacing text, and the subtitle and word-list corrections."""

from __future__ import annotations

import threading
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from backend.core.job_store import save_result_changes
from backend.core import transcript_files
from backend.core.local_config import resolve_secret
from backend.core import correction_notes, glossary_correction, model_usage, subtitle_correction, task_activity, task_steps
from backend.core import note_history
from backend.core.local_request_scope import require_local_agent_access
from backend.routers.agent_v1 import common
from backend.routers.agent_v1.common import _job_for_request

router = APIRouter(prefix="/agent/v1", dependencies=[Depends(require_local_agent_access)])

def _history_call(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except note_history.NoteHistoryError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/tasks/{task_id}/note/versions")
def list_note_versions(request: Request, task_id: str) -> dict[str, Any]:
    """The note now and its kept versions, newest first, with what replaced each."""
    return _history_call(note_history.list_versions, task_id, client_id=common._local_client_scope(request))


@router.get("/tasks/{task_id}/note/versions/{version_id}")
def read_note_version(request: Request, task_id: str, version_id: str) -> dict[str, Any]:
    return _history_call(note_history.read_version, task_id, version_id, client_id=common._local_client_scope(request))


@router.post("/tasks/{task_id}/note/versions/{version_id}/restore")
def restore_note_version(
    request: Request, task_id: str, version_id: str, payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Put a kept version back as the note, through the note's ordinary save."""
    return _history_call(
        note_history.restore_version, task_id, version_id,
        base_version=(payload or {}).get("base_version"), client_id=common._local_client_scope(request),
    )


@router.post("/tasks/{task_id}/note/replace")
def replace_note_text(request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None)) -> dict[str, Any]:
    """Change passages of the note: each ``old_text``, found exactly once,
    becomes its ``new_text`` (or several pairs in ``replacements``), through
    the same write a restore uses."""
    body = payload or {}
    return _history_call(
        note_history.replace_text, task_id, body.get("old_text"), body.get("new_text"),
        base_version=body.get("base_version"), replacements=body.get("replacements"), reason=body.get("reason"),
        client_id=common._local_client_scope(request),
    )


def _corrections_view(records: list[Any], include_reverted: bool) -> dict[str, Any]:
    """What an agent reads of a task's correction records: the ones still in
    force unless it asks for the reverted too (half the records of a long
    recording were undone and would double its size), and each word-list line
    once, in ``entries``, with the record pointing at it by ``entry_ref``."""
    kept = [r for r in records if isinstance(r, dict) and (include_reverted or not r.get("reverted"))]
    entries: list[str] = []
    shown = []
    for record in kept:
        entry = record.get("entry")
        if isinstance(entry, str) and entry:
            if entry not in entries:
                entries.append(entry)
            record = {key: value for key, value in record.items() if key != "entry"}
            record["entry_ref"] = entries.index(entry)
        shown.append(record)
    view: dict[str, Any] = {
        "corrections": shown,
        "reverted_count": sum(1 for r in records if isinstance(r, dict) and r.get("reverted")),
    }
    if entries:
        view["entries"] = entries
    return view


@router.get("/tasks/{task_id}/subtitle-corrections")
def list_subtitle_corrections(request: Request, task_id: str, include_reverted: bool = False) -> dict[str, Any]:
    """The words fixed from the subtitles in the picture, and how the step went."""
    result = _job_for_request(request, task_id).get("result") or {}
    return {
        "ok": True,
        "task_id": task_id,
        "subtitle_correction": result.get("subtitle_correction"),
        **_corrections_view(result.get("subtitle_corrections") or [], include_reverted),
    }


@router.post("/tasks/{task_id}/subtitle-corrections/revert")
def revert_subtitle_corrections(
    request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Put the named corrections (all, without ids) back to what the
    transcript said; the subtitle files and the output folder follow."""
    client_id = common._local_client_scope(request)
    job = _job_for_request(request, task_id)
    # The pipeline and the note step write the whole result back when they
    # finish; an undo made meanwhile would be overwritten without a word.
    if str(job.get("status") or "") != "completed" or task_activity.busy_reason(job) or task_id in _glossary_running:
        raise HTTPException(status_code=409, detail={
            "code": "task_busy",
            "message": "这个任务还在处理（转录、剪辑或写笔记），等它结束再撤回，否则撤回会被处理结果盖掉。",
        })
    before = dict(job.get("result") or {})
    ids = (payload or {}).get("ids")
    try:
        result, reverted, missing = subtitle_correction.revert(
            before, [str(i) for i in ids] if isinstance(ids, list) else None,
        )
    except subtitle_correction.RevertError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if reverted:
        # The words put back are FluentFlow's own, not an edit of the user's.
        result = common._attach_result_artifacts(task_id, glossary_correction.remember(before, result), parts=("transcript",))
    save_result_changes(task_id, before, result, client_id=client_id)
    return {"ok": True, "task_id": task_id, "reverted": reverted, "not_found": missing}


BUSY = {
    "code": "task_busy",
    "message": "这个任务还在处理（转录、剪辑或写笔记），等它结束再改转录稿，否则会被处理结果盖掉。",
}
_glossary_running: set[str] = set()
_glossary_lock = threading.Lock()


def _glossary_chat() -> tuple[Any, str | None]:
    """Who reads the transcript for misheard words on an existing task: the
    Claude subscription when this machine opted into it, the configured key
    taking over when it fails (decisions 4.12, 4.14); else the text model;
    else Claude through the note's channel."""
    from backend.core import ai_summarizer, claude_text

    settings = common.local_ai_kwargs()
    keyed = ai_summarizer.text_model_chat(**settings)
    subscribed = claude_text.subscription_first(keyed)
    if subscribed is not None:
        return subscribed
    if keyed is not None:
        return keyed, f"text_model:{ai_summarizer.text_model_name(**settings)}"
    found = claude_text.claude_chat(resolve_secret(None, "anthropic_api_key"))
    return found if found is not None else (None, None)


@router.get("/tasks/{task_id}/glossary-corrections")
def list_glossary_corrections(request: Request, task_id: str, include_reverted: bool = False) -> dict[str, Any]:
    """The words fixed from the word list and the context, and how the step went."""
    result = _job_for_request(request, task_id).get("result") or {}
    return {
        "ok": True,
        "task_id": task_id,
        "glossary_correction": result.get(glossary_correction.META),
        **_corrections_view(glossary_correction.listing(result), include_reverted),
    }


@router.post("/tasks/{task_id}/glossary-corrections/run")
def run_glossary_correction(request: Request, task_id: str) -> dict[str, Any]:
    """Run the glossary step again on a finished task. Sentences the user
    edited are left alone; the new changes join the same records and undo."""
    client_id = common._local_client_scope(request)
    job = _job_for_request(request, task_id)
    if str(job.get("status") or "") != "completed" or task_activity.busy_reason(job):
        raise HTTPException(status_code=409, detail=BUSY)
    with _glossary_lock:
        if task_id in _glossary_running:
            raise HTTPException(status_code=409, detail={"code": "already_running", "message": "这个任务的词表纠正已经在跑了。"})
        _glossary_running.add(task_id)
    try:
        before = dict(job.get("result") or {})
        segments = [dict(s) for s in before.get("raw_segments") or [] if isinstance(s, dict)]
        chat, channel = _glossary_chat()
        glossary = correction_notes.for_task(task_id, client_id)
        with model_usage.scope(task_id, task_steps.CORRECT):
            outcome = glossary_correction.correct(
                segments, glossary, chat, protected=glossary_correction.edited_by_user(before),
            )
        result = dict(before)
        result[glossary_correction.META] = outcome.meta(channel)
        if outcome.corrections:
            result = glossary_correction.onto_result(result, outcome.segments, outcome.corrections)
        # Written only if the transcript is still the one the model read: an
        # edit made meanwhile is the user's and stands, and so do its files.
        saved = transcript_files.save(task_id, before, result, client_id=client_id, unless_moved=("transcript_text",))
        if saved is None:
            raise HTTPException(status_code=409, detail={
                "code": "transcript_moved",
                "message": "纠正期间转录稿被改过，这次的改动没有写入。再跑一次即可。",
            })
    finally:
        with _glossary_lock:
            _glossary_running.discard(task_id)
    return {
        "ok": True,
        "task_id": task_id,
        "glossary_correction": outcome.meta(channel),
        "corrections": [
            {key: r.get(key) for key in ("id", "from", "to", "basis", "entry", "guess", "why", "start")}
            for r in outcome.corrections
        ],
    }


@router.post("/tasks/{task_id}/glossary-corrections/revert")
def revert_glossary_corrections(
    request: Request, task_id: str, payload: Optional[dict[str, Any]] = Body(None),
) -> dict[str, Any]:
    """Put the named glossary corrections (all, without ids) back to what the
    transcript said; the subtitle files and the output folder follow."""
    client_id = common._local_client_scope(request)
    job = _job_for_request(request, task_id)
    if str(job.get("status") or "") != "completed" or task_activity.busy_reason(job) or task_id in _glossary_running:
        raise HTTPException(status_code=409, detail=BUSY)
    before = dict(job.get("result") or {})
    ids = (payload or {}).get("ids")
    try:
        result, reverted, missing = glossary_correction.revert(
            before, [str(i) for i in ids] if isinstance(ids, list) else None,
        )
    except subtitle_correction.RevertError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if reverted:
        result = common._attach_result_artifacts(task_id, result, parts=("transcript",))
    save_result_changes(task_id, before, result, client_id=client_id)
    return {"ok": True, "task_id": task_id, "reverted": reverted, "not_found": missing}
