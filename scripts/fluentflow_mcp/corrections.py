"""Tools on a task's transcript corrections (from on-screen subtitles and
from the word list): list them, revert some, run the word-list pass.
"""
from __future__ import annotations

from typing import Any

from . import core
from .core import tool


# Where each source's records live; written out whole so the route check in
# tests/test_agent_surface.py sees every path a tool calls.
_CORRECTION_ROUTES = {
    "subtitles": "/agent/v1/tasks/{task_id}/subtitle-corrections",
    "glossary": "/agent/v1/tasks/{task_id}/glossary-corrections",
}
_CORRECTION_REVERTS = {
    "subtitles": "/agent/v1/tasks/{task_id}/subtitle-corrections/revert",
    "glossary": "/agent/v1/tasks/{task_id}/glossary-corrections/revert",
}


def _correction_route(source: str, routes: dict[str, str], task_id: str) -> str | None:
    template = routes.get(str(source or "").strip().lower())
    return template.format(task_id=task_id) if template else None


def _bad_source(source: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "code": "bad_source",
        "error": f"source 只能是 subtitles（画面字幕修正）或 glossary（词表和上下文纠正），收到的是 {source!r}。",
    }


@tool(
    description=("Words FluentFlow changed in the task's transcript after transcription and before the note. "
        "source=subtitles: replaced from the subtitles burned into the picture; each has from (what the "
        "transcript said), to (the subtitle's spelling) and the subtitle line it rests on; "
        "subtitle_correction says how that step went (skipped with its reason, partial while the agent has "
        "pages left, no_changes, completed). source=glossary: corrected from the series' word list "
        "(FluentFlow 校正文档.md in the recording's folder or above) and the surrounding sentences; each has "
        "sentence (its number in the transcript now; null when edited since), from, to, basis (glossary: "
        "the word list's spelling, entry_ref indexes the line in entries; context: why says how; doubt: the "
        "word was kept and marked （存疑）, guess holds the likeliest reading) and the "
        "sentence before and after the change; glossary_correction says how that step went. Every record "
        "has an id and the time; by=agent with the model when the agent made it (save_agent_correction). "
        "Only corrections still in force are listed; reverted_count says how many were undone, and "
        "include_reverted=true lists those too. Show the user these when they say a name or term in the "
        "transcript or note was changed wrongly."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "source": {"type": "string", "enum": ["subtitles", "glossary"]},
            "include_reverted": {"type": "boolean", "default": False},
        },
        "required": ["task_id", "source"],
    },
)
def list_transcript_corrections(
    task_id: str, source: str, include_reverted: bool = False,
    api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    path = _correction_route(source, _CORRECTION_ROUTES, task_id)
    if path is None:
        return _bad_source(source)
    if include_reverted:
        path += "?include_reverted=true"
    return core._agent_request("GET", path, api_base=api_base, client_id=client_id)


@tool(
    description=("Undo transcript corrections of one source (subtitles or glossary): each named one (ids from "
        "list_transcript_corrections with the same source, or all of that source when ids is empty) goes "
        "back to what the transcript said, in every copy of the transcript, the subtitle files and the "
        "output folder. Ask the user first. Refused with code task_busy while the task is still "
        "transcribing, cutting or writing its note: wait for it to finish. A note already written is not "
        "rewritten (regenerate_note does that if the user wants it). not_found lists ids whose sentence "
        "was edited since and could not be found."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "source": {"type": "string", "enum": ["subtitles", "glossary"]},
            "ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["task_id", "source"],
    },
)
def revert_transcript_corrections(
    task_id: str, source: str, ids: list[str] | None = None,
    api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    path = _correction_route(source, _CORRECTION_REVERTS, task_id)
    if path is None:
        return _bad_source(source)
    return core._agent_request("POST", path, api_base=api_base, client_id=client_id, payload={"ids": ids or []})


@tool(
    description=("Run the word-list correction on a finished task again, for a task processed before the step "
        "existed or after the user added to the word list. Reads the whole transcript with the word list "
        "found for the recording's folder (with no word list, only what the context makes certain is "
        "changed). Sentences the user edited in the editor are left alone. Each change replaces a misheard "
        "word, never a sentence; the new changes join the task's records and undo the same way "
        "(list_transcript_corrections, revert_transcript_corrections with source=glossary). The transcript, its subtitle files and "
        "the output folder follow; a note already written is not rewritten (regenerate_note does that if "
        "the user wants it). Uses the Claude subscription first when this machine opted into it, else the "
        "configured text model; takes a minute or two for an hour of recording. Refused with code "
        "task_busy while the task is still being processed, already_running when a run is under way, and "
        "transcript_moved when the transcript was edited while it ran (nothing written; run it again)."),
    input_schema={
        "type": "object",
        "properties": {"task_id": {"type": "string"}, "api_base": {"type": "string"}},
        "required": ["task_id"],
    },
)
def run_glossary_correction(task_id: str, api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return core._held("run_glossary_correction", {"task_id": task_id}, lambda: core._agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/glossary-corrections/run",
        api_base=api_base, client_id=client_id, payload={}, timeout=900,
    ))
