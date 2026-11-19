"""Tools that hand the current agent its material and take its answer
back: aligning note sections to playback times, correcting the
transcript, writing the note (whole or by parts), judging the cut and
its report.
"""
from __future__ import annotations

from typing import Any

from scripts.local_agent_client import normalize_api_base

from . import core
from .core import _options, tool


@tool(
    description=("Align a finished note's sections to the played recording yourself, in the current agent "
        "(Codex, Claude Code or any other host). No FluentFlow model call, Claude login or API key is needed. "
        "Claims alignment for this task so background/page retries do not call Claude. Returns tagged note "
        "headings and the timed transcript in material, with primary/others instructions. Read every page "
        "using next_offset as offset and the same material_version before judging. Then save_agent_alignment "
        "with that version and one answer per section. A stale version means re-read and judge again. "
        "It asks only about sections without a time; when the existing times are wrong or were not judged "
        "from this material, pass again=true to align every section anew (pass it on every page and to "
        "save_agent_alignment too); the whole table is replaced. "
        "Only the time table changes; the note, files and record ordering are untouched."),
    input_schema={
        "type": "object", "properties": {
            "task_id": {"type": "string"},
            "offset": {"type": "integer", "default": 0, "minimum": 0},
            "material_version": {"type": "string", "description": "Required when reading a later page."},
            "again": {"type": "boolean", "default": False,
                      "description": "Align every section anew, replacing the existing table."},
            "api_base": {"type": "string"},
        }, "required": ["task_id"],
    },
)
def get_agent_alignment_material(
    task_id: str, offset: int = 0, material_version: str | None = None, again: bool = False,
    api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    return core._agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/agent-alignment/material",
        api_base=api_base, client_id=client_id,
        payload=_options(offset=offset, material_version=material_version, again=again or None),
    )


@tool(
    description=("Save the section times you judged from ALL pages of get_agent_alignment_material. "
        "Pass its material_version, the sections array (id, primary {start,end}, others [{start,end}]) "
        "and your actual model. Times are seconds or H:MM:SS on the PLAYED recording. Every requested "
        "id must appear exactly once, each span finite, within the recording and end > start. "
        "If the note, transcript, cut timeline or table changed meanwhile, returns stale and writes "
        "nothing: get fresh material and judge again. Pass again=true when the material was read with "
        "again=true. Never calls a model or edits the note or its files."),
    input_schema={
        "type": "object", "properties": {
            "task_id": {"type": "string"}, "material_version": {"type": "string"},
            "sections": {"type": "array", "items": {
                "type": "object", "properties": {
                    "id": {"type": "string"},
                    "primary": {"type": "object", "properties": {
                        "start": {"type": ["number", "string"]}, "end": {"type": ["number", "string"]},
                    }, "required": ["start", "end"]},
                    "others": {"type": "array", "items": {"type": "object", "properties": {
                        "start": {"type": ["number", "string"]}, "end": {"type": ["number", "string"]},
                    }, "required": ["start", "end"]}},
                }, "required": ["id", "primary", "others"],
            }},
            "model": {"type": "string"}, "again": {"type": "boolean", "default": False},
            "api_base": {"type": "string"},
        }, "required": ["task_id", "material_version", "sections", "model"],
    },
)
def save_agent_alignment(
    task_id: str, material_version: str, sections: list[dict[str, Any]], model: str, again: bool = False,
    api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    payload = {"material_version": material_version, "sections": sections, "model": model}
    if again:
        payload["again"] = True
    return core._agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/agent-alignment",
        api_base=api_base, client_id=client_id, payload=payload,
    )


@tool(
    description=("Correct a finished task's transcript or translate its subtitles yourself, in the current agent; "
        "FluentFlow calls no model. step=subtitles: misheard words against the subtitles burned into the "
        "picture (FluentFlow reads them locally; the first call may answer state=reading, ask again after "
        "retry_after_seconds). step=glossary: misheard names and terms against the series' word list and the "
        "context. step=translation: Chinese for an English recording's subtitles. Do them in that order, "
        "before writing the note, when wait_task's next or get_task's agent_corrections_left names them; on "
        "any finished task when the user asks. The transcript comes in pages of fixed sentence ranges: "
        "returns page, pages, next_page, pages_done, material_version, the page's items or sentences and "
        "instructions. Judge one page, save it with save_agent_correction and that page's material_version, "
        "then take next_page. Sentences the user edited are left out (subtitles) or marked locked (glossary). "
        "state=nothing_to_do (no subtitles in the picture, not English) says so; for subtitles, save once "
        "with fixes=[] to record the step done. Refused with task_busy while the task is still processing. "
        "Do it in a subagent with a fresh context when your host has them."),
    input_schema={
        "type": "object", "properties": {
            "task_id": {"type": "string"},
            "step": {"type": "string", "enum": ["subtitles", "glossary", "translation"]},
            "page": {"type": "integer", "default": 1, "minimum": 1},
            "api_base": {"type": "string"},
        }, "required": ["task_id", "step"],
    },
)
def get_agent_correction_material(
    task_id: str, step: str, page: int = 1, api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    return core._agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/agent-correction/material",
        api_base=api_base, client_id=client_id, payload={"step": step, "page": page},
    )


@tool(
    description=("Save one page you judged from get_agent_correction_material. Pass the step, page and that page's "
        "material_version, your actual model, and for subtitles/glossary fixes=[{id, from, to}] (glossary adds "
        "basis glossary|context|doubt, why, and nth when from occurs more than once; doubt keeps the word and marks "
        "it 「（存疑）」, with to as your guess), for translation "
        "translations=[{start_id, end_id, text_en, text_zh}]. An empty list is a page with nothing to change. "
        "Each fix is checked the way a background model's is: a misheard word replaced, never a sentence, the "
        "new text in a subtitle line or backed by the word list or a reason; applied lists what went in, "
        "refused what did not hold. Each change goes into every copy of the transcript, the subtitle files and "
        "the output folder, recorded with by=agent and your model, and is undone like the background's "
        "(revert_transcript_corrections). Translations replace the bilingual "
        "lines of the sentences they cover; the rest keep theirs (the background's included), and a sentence "
        "the user edited keeps its English. A note already written is not rewritten. If a sentence of the "
        "page changed since "
        "the material was read, returns stale and writes nothing: read the page again. The answer gives "
        "pages_left, next_page and, when another step is left, next."),
    input_schema={
        "type": "object", "properties": {
            "task_id": {"type": "string"},
            "step": {"type": "string", "enum": ["subtitles", "glossary", "translation"]},
            "page": {"type": "integer", "minimum": 1},
            "material_version": {"type": "string"},
            "fixes": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "integer"}, "from": {"type": "string"}, "to": {"type": "string"},
                "nth": {"type": "integer"}, "basis": {"type": "string", "enum": ["glossary", "context", "doubt"]},
                "why": {"type": "string"},
            }, "required": ["id", "from", "to"]}},
            "translations": {"type": "array", "items": {"type": "object", "properties": {
                "start_id": {"type": "integer"}, "end_id": {"type": "integer"},
                "text_en": {"type": "string"}, "text_zh": {"type": "string"},
            }, "required": ["start_id", "end_id", "text_zh"]}},
            "model": {"type": "string"},
            "api_base": {"type": "string"},
        }, "required": ["task_id", "step", "page", "material_version", "model"],
    },
)
def save_agent_correction(
    task_id: str, step: str, page: int, material_version: str, model: str,
    fixes: list[dict[str, Any]] | None = None, translations: list[dict[str, Any]] | None = None,
    api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"step": step, "page": page, "material_version": material_version, "model": model}
    if fixes is not None:
        payload["fixes"] = fixes
    if translations is not None:
        payload["translations"] = translations
    return core._agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/agent-correction",
        api_base=api_base, client_id=client_id, payload=payload,
    )


@tool(
    description=("Write a task's note yourself instead of a model FluentFlow is configured to pay for "
        "(the way to get a note on the user's own subscription). Returns the whole timestamped "
        "transcript, the frames as local file paths with their moments, and instructions (the "
        "note skill in effect plus the fixed contract). Open the frames you need, write the whole "
        "note, then call save_agent_note once. Do this in a subagent with a fresh context when "
        "your host has them (one that does not inherit this conversation), so the transcript does "
        "not fill the user's conversation and the subagent does not resend it: give it the task_id "
        "and have it fetch, write and save. It works as soon as wait_task returns agent_note ready, "
        "while the cut video still renders: the transcript is already on the cut video's clock, the "
        "frames are taken at the moments the cut will show, and both stay the same once it is "
        "rendered, so the note is saved as usual. On a host that shows about 10,000 tokens of a result "
        "(Codex) start with small_parts=true: call once with instructions_only=true for the writing "
        "rules, then fetch, write and save the total_parts parts one at a time. "
        "The first call for a video extracts frames (up to a minute). note_version is the task's "
        "note as you read it: pass it to save_agent_note as base_version."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "part": {"type": "integer", "default": 1, "description": "Which part, from 1 to total_parts (small_parts only)."},
            "small_parts": {
                "type": "boolean",
                "description": "Hand the recording over in parts of about 7,000 characters, for a host whose tool results cannot hold the whole transcript. Left out: as last time.",
            },
            "instructions_only": {"type": "boolean", "description": "small_parts only: the writing rules, once, without a part."},
            "api_base": {"type": "string"},
        },
        "required": ["task_id"],
    },
    meta={"anthropic/maxResultSizeChars": 500_000},
)
def get_agent_note_material(
    task_id: str,
    part: int = 1,
    small_parts: bool | None = None,
    instructions_only: bool = False,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    refusal = core._require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    return core._with_length(core._agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/agent-note/material",
        api_base=api_base,
        client_id=client_id,
        payload={"part": int(part or 1), "instructions_only": bool(instructions_only),
                 **({} if small_parts is None else {"small_parts": bool(small_parts)})},
        timeout=300,
    ))


@tool(
    description=("Save the note you wrote from get_agent_note_material. Check it against the "
        "whole transcript yourself before saving. No model is called by default. "
        "model_check=true explicitly delegates coverage checking to configured models as fallback; "
        "coverage.missing then lists points to consider adding once. "
        "To change a few words of a saved note use replace_note_text instead. "
        "Use note_markdown for a whole note, or part to save parts before joining; a part far shorter "
        "than what was said in it is kept and the answer's short and next ask you once to check it "
        "for the speaker's reasons and the questions asked, then save it again. "
        "Cite frames by file name only. Too-short notes are refused. Pass base_version: "
        "a concurrent edit returns stale without replacing it; read, merge and save again "
        "with the new note_version. Previous notes stay restorable. The output folder's md and PDF "
        "are queued to follow, usually within seconds; the answer's follows lists where the note "
        "goes. FluentFlow does not match the "
        "sections of a note you wrote to playback times: once the task is done, align them with "
        "get_agent_alignment_material (the answer's next says so while times are missing)."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "note_markdown": {"type": "string"},
            "part": {"type": "integer", "description": "Which part this note is, from 1 to total_parts."},
            "part_notes": {"type": "array", "items": {"type": "string"}},
            "basis_note": {
                "type": "string",
                "description": "One or two sentences: which parts rest on the frames, which on the transcript only.",
            },
            "model": {"type": "string", "description": "Which model wrote it, recorded on the note."},
            "base_version": {
                "type": "string",
                "description": (
                    "The note_version you read the note at (get_task, get_agent_note_material). "
                    "When the note changed since, nothing is saved and the answer has code stale: "
                    "read the note again, merge your change into it, save with the new version. "
                    "Leaving it out saves as before, over any change made meanwhile."
                ),
            },
            "model_check": {"type": "boolean", "default": False, "description": "Explicitly delegate coverage checking to background models; otherwise check yourself."},
            "api_base": {"type": "string"},
        },
        "required": ["task_id"],
    },
)
def save_agent_note(
    task_id: str,
    note_markdown: str | None = None,
    part: int | None = None,
    part_notes: list[str] | None = None,
    basis_note: str = "",
    model: str = "",
    base_version: str | None = None,
    model_check: bool = False,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    refusal = core._require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    payload = _options(note_markdown=note_markdown, part=part, part_notes=part_notes, basis_note=basis_note,
                       model=model, base_version=base_version, model_check=model_check)

    def save() -> dict[str, Any]:
        return core._agent_request(
            "POST", f"/agent/v1/tasks/{task_id}/agent-note", api_base=api_base, client_id=client_id,
            # A delegated model check may read each full transcript part.
            payload=payload, timeout=600,
        )

    return core._held("save_agent_note", {"task_id": task_id, **payload}, save) if model_check else save()


@tool(
    description=("Read back one part of the note you saved with save_agent_note(part=...), without its "
        "transcript. Used for the read-through once every part is saved: read the parts one at a "
        "time, then save again only the parts you change."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "part": {"type": "integer"},
            "api_base": {"type": "string"},
        },
        "required": ["task_id", "part"],
    },
)
def get_agent_note_part(
    task_id: str,
    part: int,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    refusal = core._require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    return core._agent_request(
        "GET",
        f"/agent/v1/tasks/{task_id}/agent-note/parts/{int(part)}",
        api_base=api_base,
        client_id=client_id,
    )


@tool(
    description=("When a task waits for you to judge its cut (wait_task or get_task shows agent_cut.status "
        "waiting): the same material FluentFlow's own model gets. instructions (what to keep, what "
        "to remove, the JSON to answer with), cases (the user's earlier cut decisions), timeline "
        "(timestamped transcript with numbered long gaps and whether the picture moved), shots "
        "(screenshots of the long gaps whose picture moved, as local paths; open them before deciding "
        "those gaps). A long recording comes in total_parts parts, each small enough for a host that "
        "shows about 10,000 tokens of a result (Codex): call again, one part per call, with part set to "
        "next_part until it is null, then judge and call save_agent_cut. Call it in a subagent with a fresh context when "
        "your host has them (give it the task_id): read in the user's conversation, the material is "
        "resent on every later turn. If you do not answer within decide_within_seconds, or this "
        "conversation closes, FluentFlow judges the cut itself."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "part": {"type": "integer", "description": "Which part, from 1 (the default)."},
            "api_base": {"type": "string"},
        },
        "required": ["task_id"],
    },
    meta={"anthropic/maxResultSizeChars": 500_000},
)
def get_agent_cut_material(
    task_id: str, part: int = 1, api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    refusal = core._require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    return core._with_length(core._watching(core._agent_request(
        "GET", f"/agent/v1/tasks/{task_id}/agent-cut/material?part={int(part)}",
        api_base=api_base, client_id=client_id,
    ), task_id, api_base, client_id))


@tool(
    description=("Send your decision on a task's cut, as the instructions of get_agent_cut_material ask: "
        "keep_gaps (gap ids from the timeline, each with a reason) and remove (stretches with talk, "
        "from/to in seconds as the timeline gives them, to null for the end, each with a reason). "
        "FluentFlow applies the same rules as to its own model: spoken words are never cut, and "
        "removals that together take more than a quarter of what was said are not applied but put "
        "to the user one by one. A wrong gap id is refused, so you can fix it and send again. The "
        "answer lists what was done as numbered facts with report_instructions: organise them and "
        "call save_agent_cut_report. Then tell the user the cut is done and how many places need "
        "their look (save_agent_cut_report's to_judge) on the task page in FluentFlow; do not ask them "
        "in chat (they need the video)."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "keep_gaps": {"type": "array", "items": {"type": "object"}},
            "remove": {"type": "array", "items": {"type": "object"}},
            "model": {"type": "string", "description": "Which model judged, recorded with the cut."},
            "api_base": {"type": "string"},
        },
        "required": ["task_id"],
    },
)
def save_agent_cut(
    task_id: str,
    keep_gaps: list[dict[str, Any]] | None = None,
    remove: list[dict[str, Any]] | None = None,
    model: str = "",
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    refusal = core._require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    return core._watching(core._agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/agent-cut", api_base=api_base, client_id=client_id,
        payload={"keep_gaps": keep_gaps or [], "remove": remove or [], "model": model},
    ), task_id, api_base, client_id)


@tool(
    description=("Send the processing report for the cut you judged: the facts save_agent_cut listed, organised "
        "as its report_instructions say (summary, and sections of items that cite facts by ref; mark "
        "unsure with a doubt only where you are not sure). It is shown on the task page and kept when "
        "the user changes a cut later. The answer's to_judge is how many places the page asks the user "
        "to judge (yours plus the ones FluentFlow always asks about, such as removed talk): tell the user "
        "that number, not your own count. When the user kept agreeing with your model's cuts, to_judge "
        "is 0 and not_asked says why: tell the user that sentence instead."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "summary": {"type": "string"},
            "sections": {"type": "array", "items": {"type": "object"}},
            "api_base": {"type": "string"},
        },
        "required": ["task_id", "sections"],
    },
)
def save_agent_cut_report(
    task_id: str,
    summary: str = "",
    sections: list[dict[str, Any]] | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    refusal = core._require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    return core._agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/agent-cut/report", api_base=api_base, client_id=client_id,
        payload={"report": {"summary": summary, "sections": sections or []}},
    )
