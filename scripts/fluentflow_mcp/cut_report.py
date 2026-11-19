"""Tools on a cut task's processing report: read it, turn a decision
around, agree with decisions, undo an earlier turnaround.
"""
from __future__ import annotations

from typing import Any

from . import core
from .core import tool


@tool(
    description=("Read a cut task's processing report, the list the page shows under 处理说明: each "
        "decision with what was cut or kept (action removed or kept), why (text), its time span, its "
        "ref (to turn it around with flip_cut_decision) and its key (to agree with it with "
        "answer_cut_decisions); the section with to_judge=true is what the user should look at; when "
        "judge_released is set the user closed this judge's reminders and is not asked (say that sentence); "
        "reminder_offer means the page offers the user to close them: pass it on, only the user decides; "
        "user_changes are the user's earlier turnarounds (undo one with undo_cut_change by its index); "
        "agreed lists the keys already agreed. Sections the page folds away come as folded=true with "
        "item_count only; pass section=<its title> to read that one in full. Refused with code no_report "
        "for a task with no report."),
    input_schema={
        "type": "object",
        "properties": {"task_id": {"type": "string"}, "section": {"type": "string"}},
        "required": ["task_id"],
    },
)
def get_cut_report(
    task_id: str, section: str | None = None, api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    from urllib.parse import urlencode

    path = f"/agent/v1/tasks/{task_id}/cut-report"
    if section:
        path += "?" + urlencode({"section": section})
    return core._agent_request("GET", path, api_base=api_base, client_id=client_id)


@tool(
    description=("Turn one decision of the processing report around, as the page's 改判 does: a stretch "
        "that was cut is put back, one that was kept is cut. The cut video is rendered again from the "
        "original without asking a model, the transcript moved onto it, the report written again and "
        "the note rewritten from the new cut; it takes minutes, follow with wait_task. Only on the "
        "user's word: say which stretch (its time and text) and what will change, and do it when they "
        "agree. Refused (409) while the task is still working (busy, note_running), while a re-render "
        "of it runs (rerendering), while the queue works (queue_busy), or when the original is gone "
        "(cannot_render)."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "ref": {"type": "string", "description": "The decision's ref from get_cut_report."},
            "api_base": {"type": "string"},
        },
        "required": ["task_id", "ref"],
    },
)
def flip_cut_decision(task_id: str, ref: str, api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return core._agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/cut-report/flip",
        api_base=api_base, client_id=client_id, payload={"ref": ref},
    )


@tool(
    description=("Record that the user agrees with decisions of the processing report as they are "
        "(剪得对 / 确实该保留), verdict agree, or take that back, verdict pending. Nothing is rendered. "
        "Use it when the user has looked at the decisions and says they are right; an item nobody "
        "answered counts as not looked at."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "keys": {"type": "array", "items": {"type": "string"}, "description": "Decision keys from get_cut_report."},
            "verdict": {"type": "string", "enum": ["agree", "pending"]},
            "api_base": {"type": "string"},
        },
        "required": ["task_id", "keys", "verdict"],
    },
)
def answer_cut_decisions(
    task_id: str, keys: list[str], verdict: str, api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    return core._agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/cut-report/verdict",
        api_base=api_base, client_id=client_id, payload={"keys": keys, "verdict": verdict},
    )


@tool(
    description=("Undo one of the user's earlier turnarounds (get_cut_report's user_changes, by index): "
        "the model's decision for that stretch applies again and the cut is rendered again, as "
        "flip_cut_decision does. Refused for the same reasons."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "index": {"type": "integer", "description": "The change's index in user_changes."},
            "api_base": {"type": "string"},
        },
        "required": ["task_id", "index"],
    },
)
def undo_cut_change(task_id: str, index: int, api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return core._agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/cut-report/undo",
        api_base=api_base, client_id=client_id, payload={"index": index},
    )
