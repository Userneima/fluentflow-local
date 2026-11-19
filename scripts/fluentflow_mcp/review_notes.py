"""Tools that write and revise a review note beside the follow-along
note, by the current agent or FluentFlow's model.
"""
from __future__ import annotations

from typing import Any

from . import core
from .core import tool


_REVIEW_WRITER = {
    "type": "string", "enum": ["agent", "fluentflow"], "default": "agent",
    "description": "agent (default): you write it from the material. fluentflow: FluentFlow's own model does.",
}
_REVIEW_AGENT_FIELDS = {
    "part": {"type": "integer", "default": 1, "description": "Which part of the material to read, 1 to total_parts."},
    "note_markdown": {"type": "string", "description": "The whole note you wrote; leave out to read the material."},
    "material_version": {"type": "string", "description": "From the material, required with note_markdown."},
    "model": {"type": "string", "description": "Which model wrote it, recorded on the note."},
}


def _review_note(
    payload: dict[str, Any], writer: str, part: int, note_markdown: str | None, material_version: str | None,
    model: str, model_route: str, model_timeout: float, tool_name: str, api_base: str | None, client_id: str | None,
) -> dict[str, Any]:
    """The review note written by the agent at hand (the default, decision
    5.47) or by FluentFlow's model; the same material and checks either way."""
    if writer == "fluentflow":
        return core._held(tool_name, payload, lambda: core._agent_request(
            "POST", model_route, api_base=api_base, client_id=client_id, payload=payload, timeout=model_timeout,
        ))
    if note_markdown:
        return core._agent_request(
            "POST", "/agent/v1/review-notes/agent", api_base=api_base, client_id=client_id, timeout=300,
            payload={**payload, "note_markdown": note_markdown, "material_version": material_version, "model": model},
        )
    answer = core._agent_request(
        "POST", "/agent/v1/review-notes/agent-material", api_base=api_base, client_id=client_id, timeout=120,
        payload={**payload, "part": int(part or 1)},
    )
    if answer.get("ok") is not False:
        last = answer.get("part") == answer.get("total_parts")
        answer["next"] = (
            f"Read every part first (part=2 up to {answer.get('total_parts')}). " if not last else ""
        ) + (f"Write the whole note as the rules in part 1 say, then call {tool_name} again with the same "
             "arguments plus note_markdown, material_version and model.")
    return core._with_length(answer)


@tool(
    description=("Write the review note (复习笔记): a second note beside the follow-along note (<名>_笔记.md), "
        "reorganised by topic for learning it again, written from the follow-along md as it is on disk now, "
        "with the user's corrections. Only when the user asks. By default you write it: call without "
        "note_markdown for the material (part 1 has the writing rules, then the note and "
        "the cut video's transcript), read every part, write the note as the rules say and call again with "
        "note_markdown and material_version. Hand this to a new subagent with a fresh context. The body has no sources (the user asks you where a point came from). FluentFlow "
        "writes <名>_复习笔记.md with a PDF and keeps an earlier one in 过程文件/历史版本. writer=fluentflow has "
        "FluentFlow's own model write it instead (about ten minutes for three hours). Give task_id, or "
        "note_path (the follow-along md) when the user moved the output folder. Refused with code stale when "
        "a note changed after you read the material (read it again), review_note_failed with the reason "
        "(nothing written), already_running."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "note_path": {"type": "string", "description": "The follow-along note md, when the output folder was moved."},
            "writer": _REVIEW_WRITER,
            **_REVIEW_AGENT_FIELDS,
        },
    },
)
def write_review_note(
    task_id: str | None = None, note_path: str | None = None, writer: str = "agent", part: int = 1,
    note_markdown: str | None = None, material_version: str | None = None, model: str = "",
    api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    return _review_note(
        {"task_id": task_id, "note_path": note_path}, writer, part, note_markdown, material_version, model,
        "/agent/v1/review-notes", 2700, "write_review_note", api_base, client_id,
    )


@tool(
    description=("Change an existing review note as the user asked instead of writing it again: instruction is "
        "the user's feedback (what to change, what to leave). Works like write_review_note: by default you "
        "read the material in parts (the rules, the feedback, the current note; one written before sources "
        "were dropped shows them as 〔编号 时间〕 and 〔L1〕: leave them unless the feedback says to remove them), "
        "write the whole changed note and call again with "
        "note_markdown and material_version; writer=fluentflow has FluentFlow's model do it. The previous "
        "version goes to 过程文件/历史版本 and the PDF is printed again. Give task_id, or note_path (the review "
        "note or the follow-along note)."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "note_path": {"type": "string"},
            "instruction": {"type": "string"},
            "writer": _REVIEW_WRITER,
            **_REVIEW_AGENT_FIELDS,
        },
        "required": ["instruction"],
    },
)
def revise_review_note(
    instruction: str, task_id: str | None = None, note_path: str | None = None, writer: str = "agent",
    part: int = 1, note_markdown: str | None = None, material_version: str | None = None, model: str = "",
    api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    return _review_note(
        {"task_id": task_id, "note_path": note_path, "instruction": instruction}, writer, part, note_markdown,
        material_version, model, "/agent/v1/review-notes/revise", 1800, "revise_review_note", api_base, client_id,
    )
