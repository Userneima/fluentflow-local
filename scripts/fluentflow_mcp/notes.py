"""Tools about how notes are written and kept: FluentFlow's status, the
note skill and the user's feedback on notes, listing tasks, note
versions, and replacing passages of a note by their exact text.
"""
from __future__ import annotations

from typing import Any

from . import core
from .core import _options, tool


@tool(
    description=("How FluentFlow on this machine handles a task you submit, step by step (getting the material, "
        "cutting, transcription, notes, outputs, agent connection), and anything the user has to fix first. "
        "The same list the Settings page shows. Use it when the user asks what FluentFlow is set up to do. "
        "model_usage_outside_tasks adds up the model calls that belong to no task (the settings page's key "
        "check, the note style, a call that lost its task), shaped like a task's model_usage with items by kind."),
    input_schema={"type": "object", "properties": {"api_base": {"type": "string"}}},
)
def get_status(api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return core._agent_request("GET", "/agent/v1/status", api_base=api_base, client_id=client_id)


@tool(
    description=("The note-writing rules in effect (text, version, default or the user's own) and every rule added from feedback, with the feedback each came from."),
    input_schema={"type": "object", "properties": {"api_base": {"type": "string"}}},
)
def get_note_skill(api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return core._agent_request("GET", "/agent/v1/note-skill", api_base=api_base, client_id=client_id)


@tool(
    description=("Keep what the user said about a note, at once, every time: kind=one_off for a single "
        "error or omission (a misspelt name, a point left out), pattern for a way of writing that would recur, "
        "fine when they say the note needed nothing. Record each error or omission they point out when they "
        "say it, not only once it becomes a rule. study_id: the studied record (list_note_feedback) you found "
        "it in; once you have been through a studied record, record what you found with its study_id, or "
        "kind=fine when it showed no problem with how notes are written. A pattern needs "
        "criterion: how a checker tells the problem is fixed. With task_id the transcript of that "
        "part is frozen into the case. A one-off fact correction is not a lasting writing preference. "
        "Propose a preference from the user's feedback or edits and confirm its effect and scope before "
        "changing future notes; an explicit request to always write that way already counts. "
        "Check a short affected excerpt for wording or formatting; use representative excerpts for "
        "content selection or structure. Do not run three rewrites or full-note comparisons for every change. "
        "After verification, merge the rule into get_note_skill's latest text and save with apply_note_rule."),
    input_schema={
        "type": "object",
        "properties": {
            "problem": {"type": "string", "description": "What the user said, in their words (or, from a studied record, what the edit shows)."},
            "kind": {"type": "string", "enum": ["one_off", "pattern", "fine"]},
            "study_id": {"type": "string"},
            "task_id": {"type": "string"},
            "criterion": {"type": "string", "description": "Pass / fail: what a fixed note does."},
            "note_excerpt": {"type": "string", "description": "The note text the problem is in."},
            "part": {"type": "integer"},
            "transcript_excerpt": {"type": "string", "description": "Only the relevant stretch; default the whole part."},
            "api_base": {"type": "string"},
        },
        "required": ["problem", "kind"],
    },
)
def record_note_feedback(
    problem: str,
    kind: str,
    task_id: str | None = None,
    criterion: str = "",
    note_excerpt: str = "",
    part: int | None = None,
    transcript_excerpt: str = "",
    study_id: str | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return core._agent_request(
        "POST", "/agent/v1/note-feedback", api_base=api_base, client_id=client_id,
        payload=_options(task_id=task_id, problem=problem, kind=kind, criterion=criterion,
                         note_excerpt=note_excerpt, part=part, transcript_excerpt=transcript_excerpt,
                         study_id=study_id),
    )


@tool(
    description=("Mark a task's note studied when the user says they finished studying it (the page's 学完了 "
        "button does the same), or studied=false to take it back. It unpins the task and moves it to the "
        "学完的 list, grouped by its recording's folder. verdict, only if the user said: good (好用), wrong "
        "(有错), missing (有漏). It keeps a studied record of the user's changes to the note since it was "
        "last written whole; read it with list_note_feedback."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "studied": {"type": "boolean", "default": True},
            "verdict": {"type": "string", "enum": ["good", "wrong", "missing"]},
        },
        "required": ["task_id"],
    },
)
def mark_studied(
    task_id: str,
    studied: bool = True,
    verdict: str | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return core._agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/studied", api_base=api_base, client_id=client_id,
        payload=_options(studied=studied, verdict=verdict),
    )


@tool(
    description=("Everything recorded about notes, newest first: problems the user reported (rule_id when a rule "
        "came of it), notes said to be fine, and kind=studied records kept when the user finished studying a "
        "note: verdict, changes_total, edited_by, the first changes (chapter, before, after) and changes_file "
        "with all of them. reviewed=false means no agent has been through it yet. Read it to tell whether a "
        "new report is a problem that came back, and before working on how notes are written."),
    input_schema={"type": "object", "properties": {"api_base": {"type": "string"}}},
)
def list_note_feedback(api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return core._agent_request("GET", "/agent/v1/note-feedback", api_base=api_base, client_id=client_id)


@tool(
    description=("Have FluentFlow's own model (the one that wrote the task's note) write one part with "
        "candidate rules, unsaved, to verify a rule when FluentFlow writes the user's notes. With no "
        "model configured, write the part yourself from get_agent_note_material with the candidate rules."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "rules": {"type": "string", "description": "The whole candidate skill text."},
            "part": {"type": "integer", "default": 1},
            "api_base": {"type": "string"},
        },
        "required": ["task_id", "rules"],
    },
)
def draft_note_part(
    task_id: str,
    rules: str,
    part: int = 1,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    payload = {"rules": rules, "part": int(part or 1)}
    return core._held("draft_note_part", {"task_id": task_id, **payload}, lambda: core._agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/note-draft", api_base=api_base, client_id=client_id,
        payload=payload, timeout=600,
    ))


@tool(
    description=("Write a user-confirmed preference you verified into the user's note skill. skill_text is the whole new skill "
        "with the rule merged where it belongs; the previous skill is kept and can be restored from "
        "the settings page without a model call. Requires base_version from get_note_skill. "
        "rounds: the actual number of successful sample checks (one is enough for a small wording change); evidence: one or two "
        "sentences on what was checked."),
    input_schema={
        "type": "object",
        "properties": {
            "skill_text": {"type": "string"},
            "rule": {"type": "string", "description": "The rule, as the user will be told it."},
            "feedback_ids": {"type": "array", "items": {"type": "string"}},
            "rounds": {"type": "integer"},
            "evidence": {"type": "string"},
            "base_version": {
                "type": "string",
                "description": "The skill version get_note_skill returned, the one you merged the rule into. "
                               "The write is refused if the skill changed since; read it again and merge.",
            },
            "api_base": {"type": "string"},
        },
        "required": ["skill_text", "rule", "feedback_ids", "rounds", "evidence", "base_version"],
    },
)
def apply_note_rule(
    skill_text: str,
    rule: str,
    feedback_ids: list[str],
    rounds: int,
    evidence: str,
    base_version: str,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return core._agent_request(
        "POST", "/agent/v1/note-skill/rules", api_base=api_base, client_id=client_id,
        payload={"skill_text": skill_text, "rule": rule, "feedback_ids": list(feedback_ids),
                 "rounds": int(rounds), "evidence": evidence, "base_version": base_version},
    )


@tool(
    description=("Find tasks: newest first, or only those whose title, note title (the note's first heading) "
        "or file name contains query. Use it to find which task a screenshot of a note came from: "
        "search a few characters of the title in the screenshot. Returns task_id, title, note_title, "
        "created_at, status, has_note; at most 100 a call, and has_more=true when more match (narrow the "
        "query)."),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 20, "maximum": 100},
            "api_base": {"type": "string"},
        },
    },
)
def list_tasks(
    query: str = "",
    limit: int = 20,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    from urllib.parse import urlencode

    return core._agent_request(
        "GET", "/agent/v1/tasks?" + urlencode({"query": query, "limit": int(limit or 20)}),
        api_base=api_base, client_id=client_id,
    )



@tool(
    description=("List the earlier versions FluentFlow kept of a task's note. Use it when the user says the "
        "note lost content, got much shorter, was overwritten, or asks for an earlier version. Before "
        "anything writes over the note (an edit on the page, the md edited in Obsidian, an agent's "
        "note, a rewrite, Feishu, a restore), the version being replaced is kept; the Feishu document "
        "only when it was edited in Feishu since the last push. What each change changed (time, who, "
        "chapter, words before and after) is in 过程文件/修改记录.md beside the note's versions, "
        "newest first (its path is change_log in the answer): read it to find which change lost "
        "something. Returns current "
        "(the note now: note_version, chars, chars_change, changed_by) and versions, newest first: "
        "version_id, saved_at, side (where that version lived: 本地 = the md in the output folder, "
        "FluentFlow = the task's note, 飞书 = the Feishu document), replaced_by (what wrote over it: "
        "web_edit, obsidian, agent_note, agent_edit, rewrite, generated, feishu_pull, export, restore, switch_back, adjust; "
        "null for versions kept before this was recorded, shown as 未记录), chars, chars_change "
        "(against the previous version on the same side; the 飞书 side is Feishu's export format and "
        "runs a few hundred characters longer than the others) and sharp_drop (at least 1,000 characters and half the "
        "length lost at once, the sign of a note saved over by a fragment). Tell the user what "
        "you found before restoring anything."),
    input_schema={
        "type": "object",
        "properties": {"task_id": {"type": "string"}, "api_base": {"type": "string"}},
        "required": ["task_id"],
    },
)
def list_note_versions(task_id: str, api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return core._agent_request(
        "GET", f"/agent/v1/tasks/{task_id}/note/versions", api_base=api_base, client_id=client_id,
    )


@tool(
    description=("Read one kept version of a task's note in full (markdown), by the version_id "
        "list_note_versions gave. Use it to check a version is the one the user wants before "
        "restoring it, or to copy a lost passage back by hand. Versions with side 本地, and FluentFlow "
        "versions kept when an Obsidian edit came in, point their screenshots at 过程文件/截图 beside "
        "the md (a restore turns them back into the task's own pictures); side 飞书 is Feishu's own "
        "export format."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "version_id": {"type": "string"},
            "api_base": {"type": "string"},
        },
        "required": ["task_id", "version_id"],
    },
)
def read_note_version(
    task_id: str, version_id: str, api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    from urllib.parse import quote

    return core._agent_request(
        "GET", f"/agent/v1/tasks/{task_id}/note/versions/{quote(version_id, safe='')}",
        api_base=api_base, client_id=client_id,
    )


@tool(
    description=("Make a kept version the task's note again. Ask the user first: it replaces the note "
        "everywhere it is kept. The current note is kept as a version first (replaced_by=restore), "
        "so a restore can itself be undone. It then goes through the note's ordinary save: the "
        "task's note changes at once; the md and PDF in the output folder, and the Feishu document "
        "of a note kept in step with Feishu (changed chapters only), are queued to follow, usually "
        "within seconds to a minute. The answer's follows lists where it applies; if one did not "
        "follow, get_task says why (output_folder.error, lark_sync_error). base_version is current.note_version from "
        "list_note_versions: when the note changed since (the user editing in Obsidian or on the "
        "page, Feishu, another agent), the restore is refused with code stale and nothing changes; "
        "list again and confirm with the user. Versions with side 飞书 cannot be restored (Feishu's "
        "format loses screenshots and chapter links); restore the 本地 or FluentFlow version from "
        "the same moment instead."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "version_id": {"type": "string"},
            "base_version": {
                "type": "string",
                "description": "current.note_version from list_note_versions.",
            },
            "api_base": {"type": "string"},
        },
        "required": ["task_id", "version_id", "base_version"],
    },
)
def restore_note_version(
    task_id: str, version_id: str, base_version: str, api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    from urllib.parse import quote

    return core._agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/note/versions/{quote(version_id, safe='')}/restore",
        api_base=api_base, client_id=client_id, payload={"base_version": base_version},
    )


_REPLACEMENT = {
    "type": "object",
    "properties": {"old_text": {"type": "string"}, "new_text": {"type": "string"}},
    "required": ["old_text", "new_text"],
}


@tool(
    description=("Change passages of a note by their exact text: the follow-along note (note=follow, default) "
        "or the review note (note=review). For fixing words, or deleting one kind of content in many places "
        "(pass replacements, one call); revise_review_note / save_agent_note only for a rewrite or a change "
        "needing judgement. old_text, copied exactly (get_task_package has the follow-along note; read the "
        "review md), becomes new_text (empty deletes). Every old_text must occur exactly once, none "
        "overlapping, else nothing changes: code not_found_in_note, ambiguous (add surrounding text) or "
        "overlapping. base_version: follow, get_task's note_version (required); review, optional "
        "review_version from an earlier answer. stale: the user changed the note, read it again. The old "
        "note is kept as a version; md, PDF and Feishu follow. note_path: review in a moved folder."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "note": {"type": "string", "enum": ["follow", "review"], "default": "follow"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
            "replacements": {"type": "array", "items": _REPLACEMENT},
            "base_version": {"type": "string"},
            "note_path": {"type": "string"},
            "reason": {"type": "string", "description": "Optional: one line on why, kept in the change log."},
        },
    },
)
def replace_note_text(
    task_id: str | None = None, old_text: str | None = None, new_text: str | None = None,
    base_version: str | None = None, reason: str | None = None, note: str = "follow",
    replacements: list[dict[str, Any]] | None = None, note_path: str | None = None,
    api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"base_version": base_version}
    if replacements is not None:
        payload["replacements"] = replacements
    else:
        payload.update({"old_text": old_text, "new_text": new_text})
    if reason:
        payload["reason"] = reason
    if note == "review":
        return core._agent_request("POST", "/agent/v1/review-notes/replace", api_base=api_base, client_id=client_id,
                              payload={**payload, "task_id": task_id, "note_path": note_path})
    if note != "follow":
        return {"ok": False, "code": "bad_note", "message": "note is follow (the default) or review."}
    if not task_id:
        return {"ok": False, "code": "task_id_required", "message": "Give task_id: the follow-along note is the task's."}
    return core._agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/note/replace", api_base=api_base, client_id=client_id, payload=payload,
    )
