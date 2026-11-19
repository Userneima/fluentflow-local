"""Tools that redo a finished task's outputs: rewrite the note, cut the
breath gaps again, write the note from the cut media, export.
"""
from __future__ import annotations

from typing import Any

from scripts.local_agent_client import normalize_api_base

from . import core
from .core import _options, tool


@tool(
    description=("Rewrite a finished task's note. Which model writes it: if the current note "
        "was written by Claude from the video frames and that can still run for this "
        "task (Claude configured, the cut file still there, the queue idle), Claude "
        "rewrites it from the frames in the background, exactly as the editor does — "
        "the answer says writer=claude_frames and returns at once; follow with "
        "wait_task or get_task_package. Prompt and note_mode do not apply to that "
        "path. Otherwise the configured text model rewrites it from the transcript "
        "before answering (writer=text_model), using the system_prompt and note_mode "
        "given here, or the ones the task was submitted with. The old note is never "
        "lost: it is kept and can be put back for free with write_note_from_cut_media "
        "restore_previous_note=true. If the rewrite fails the task stays completed "
        "with its old note, and summary_status=failed plus summary_error say why. With "
        "base_version, an edit the user makes while it is rewritten wins: the rewrite is "
        "refused (stale) instead of replacing it."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "note_mode": {"type": "string", "description": "Text-model path only. Default: the task's own."},
            "prompt_preset": {
                "type": "string",
                "description": "A label recorded with the note; the instructions themselves are system_prompt.",
            },
            "system_prompt": {
                "type": "string",
                "description": "Text-model path only: instructions for the note. Default: the task's own.",
            },
            "base_version": {
                "type": "string",
                "description": (
                    "The note_version you read the note at (get_task, get_agent_note_material). "
                    "When the note changed since, nothing is saved and the answer has code stale: "
                    "read the note again, merge your change into it, save with the new version. "
                    "Leaving it out saves as before, over any change made meanwhile."
                ),
            },
            "api_base": {"type": "string"},
        },
        "required": ["task_id"],
    },
)
def regenerate_note(
    task_id: str,
    note_mode: str | None = None,
    prompt_preset: str | None = None,
    system_prompt: str | None = None,
    base_version: str | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return core._agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/note/regenerate",
        api_base=api_base,
        client_id=client_id,
        payload=_options(note_mode=note_mode, prompt_preset=prompt_preset, system_prompt=system_prompt,
                         base_version=base_version),
        timeout=180,
    )


@tool(
    description=("Redo the breath-gap cut on an existing task; submitting already cuts, so this "
        "is not the normal path. The server refuses (HTTP 409) a task whose transcript "
        "was made from a cut file, since cutting again would put the media and the "
        "transcript on different timelines; to cut differently, resubmit the original "
        "with different settings. Acoustic detection only. "
        "Writes a cut list always and a rendered file when render is true; poll "
        "get_task_package and read its debreath block for progress, counts, and warnings."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "min_silence_seconds": {
                "type": "number",
                "description": (
                    "Shortest silence to remove, 0.02-60. Default 0.25. Raise it "
                    "(e.g. 1.0) for faintly recorded material, where the default cuts "
                    "close to speech."
                ),
            },
            "noise_db": {
                "type": "number",
                "description": (
                    "Silence threshold in dBFS, -90 to 0. Leave it out to have it measured "
                    "from the material (recommended; passing -30 also means measure). Any "
                    "other value is used as given."
                ),
            },
            "padding_seconds": {
                "type": "number",
                "description": "Sliver of each removed stretch kept at both ends, 0-2. Default 0.1.",
            },
            "render": {
                "type": "boolean",
                "default": True,
                "description": "False produces only the cut list — no encode, no CPU spent.",
            },
            "api_base": {"type": "string"},
        },
        "required": ["task_id"],
    },
)
def debreath_task(
    task_id: str,
    min_silence_seconds: float | None = None,
    noise_db: float | None = None,
    padding_seconds: float | None = None,
    render: bool = True,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return core._agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/debreath",
        api_base=api_base,
        client_id=client_id,
        payload={
            **_options(
                min_silence_seconds=min_silence_seconds,
                noise_db=noise_db,
                padding_seconds=padding_seconds,
            ),
            "render": bool(render),
        },
        timeout=60,
    )


@tool(
    description=("Rewrite an existing task's note with Claude from the frames and transcript; "
        "submitting already writes this note, so use it only to redo one (e.g. after a "
        "failed note). It reads the file the transcript describes: the cut file from "
        "submitting or debreath_task(render=True) (frames from it, subtitles moved onto "
        "its clock), or the original recording when the cut "
        "was declined or found nothing to remove. It refuses only when there is no usable "
        "media (cut still running or failed, a cut list with no rendered file, the file "
        "gone from this machine), and says which step to take. "
        "preview defaults to true and is "
        "free (which file, how much transcript, how many frames, whose Claude "
        "allowance pays); preview=false spends that allowance, so ask first. The note "
        "becomes the task's note by default, keeping the previous one restorable."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "preview": {
                "type": "boolean",
                "default": True,
                "description": "True answers what would be sent and who pays, for free. False runs it.",
            },
            "replace_note": {
                "type": "boolean",
                "default": True,
                "description": (
                    "True makes the finished note the task's note, keeping the previous "
                    "one on record. False writes it without touching the task's note."
                ),
            },
            "restore_previous_note": {
                "type": "boolean",
                "default": False,
                "description": "Put back the note a run replaced. Free, no model call.",
            },
            "use_generated_note": {
                "type": "boolean",
                "default": False,
                "description": "Make the already-written note the task's note again. Free.",
            },
            "base_version": {
                "type": "string",
                "description": (
                    "The note_version you read the note at (get_task, get_agent_note_material). "
                    "When the note changed since, nothing is saved and the answer has code stale: "
                    "read the note again, merge your change into it, save with the new version. "
                    "Leaving it out saves as before, over any change made meanwhile."
                ),
            },
            "api_base": {"type": "string"},
        },
        "required": ["task_id"],
    },
)
def write_note_from_cut_media(
    task_id: str,
    preview: bool = True,
    replace_note: bool = True,
    restore_previous_note: bool = False,
    use_generated_note: bool = False,
    base_version: str | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    refusal = core._require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    payload: dict[str, Any] = {}
    if restore_previous_note:
        payload["restore_previous_note"] = True
    elif use_generated_note:
        payload["use_generated_note"] = True
    elif preview:
        payload["preview"] = True
    else:
        payload["replace_note"] = bool(replace_note)
    if base_version and not payload.get("preview"):
        payload["base_version"] = base_version
    return core._agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/visual-note",
        api_base=api_base,
        client_id=client_id,
        payload=payload,
        timeout=60,
    )


@tool(
    description=("Export a completed task note; target is required. A task with an output folder "
        "(output_folder.path in its package) already has its PDF and md there, kept up to date: "
        "name that folder instead of exporting another PDF. target=pdf writes a separate PDF with its "
        "screenshots to output_dir (default ~/Documents/FluentFlow 笔记), for a task with no output "
        "folder or when the user asks, and returns pdf_path; it needs Chrome or Edge on the "
        "machine, and without one returns html_path to print instead. target=lark exports to Feishu/Lark, "
        "only when the user reads in Feishu. "
        "For Feishu: without lark_export_route or "
        "folder_token the user's export settings apply (route, then folder), else auto: "
        "lark-cli when it is signed in, otherwise the user's Feishu app. The document "
        "link is recorded on the task, so the editor shows it too."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "target": {"type": "string", "enum": ["pdf", "lark"]},
            "output_dir": {"type": "string", "description": "pdf only: folder to write the PDF in."},
            "title": {"type": "string"},
            "lark_export_route": {
                "type": "string",
                "enum": ["auto", "local_cli", "openapi"],
                "description": "Override the user's export route for this call only.",
            },
            "folder_token": {
                "type": "string",
                "description": "Feishu folder for the app route; default: the user's setting.",
            },
            "api_base": {"type": "string"},
        },
        "required": ["task_id", "target"],
    },
)
def export_result(
    task_id: str,
    target: str,
    title: str | None = None,
    lark_export_route: str | None = None,
    folder_token: str | None = None,
    output_dir: str | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return core._agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/exports",
        api_base=api_base,
        client_id=client_id,
        payload=_options(
            target=target, title=title, lark_export_route=lark_export_route, folder_token=folder_token,
            output_dir=output_dir,
        ),
        timeout=120,
    )
