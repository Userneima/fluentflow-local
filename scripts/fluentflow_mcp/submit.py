"""Tools that start a task: a recording by path, a video link, a transcript.

Also the schemas the submit tools share (who judges the cut, cut or not,
who writes the note, speaker labels), which ``retry_task`` reuses.
"""
from __future__ import annotations

from typing import Any

from backend.core.task_intelligence import agent_options
from scripts.local_agent_client import normalize_api_base

from . import core
from .core import _options, tool


_CUT_JUDGE_SCHEMA = {
    "type": "string",
    "enum": ["auto", "agent", "fluentflow"],
    "default": "agent",
    "description": (
        "Who decides what to cut. With intelligence=agent this is always agent. "
        "For explicit background delegation, auto: FluentFlow's own model when it has one, otherwise the task "
        "waits after transcription for you to judge (get_agent_cut_material, save_agent_cut). "
        "agent: always you. fluentflow: always FluentFlow (only the loudness when it has no model)."
    ),
}


# Who writes the note on a media submission, said once for both submit tools.
_MEDIA_NOTE_FLOW = (
    "The current agent does the judging by default, even when model keys are configured; never "
    "wait for Claude to fail first. FluentFlow transcribes the original, then YOU decide what to cut "
    "(breath gaps, waiting, sound checks, repeated talk) and write the note. Defaults are "
    "cut_judge=agent and skip_summary=true. Returns at once; follow with wait_task, which comes back "
    "whenever you have something to do (judge the cut, correct the transcript, write the note from "
    "get_agent_note_material) and names the tools in next. intelligence=fluentflow hands the work to "
    "the configured background models: only when you cannot do it, failed, or the user asked, and "
    "tell the user whose allowance or API key it uses."
)

_CUT_SCHEMA = {
    "type": "boolean", "default": True,
    "description": (
        "false: transcript only. Nothing is cut and no cut waits for you; any note is from the "
        "original. Not the same as save_agent_cut with empty lists (cut by defaults)."
    ),
}

_INTELLIGENCE_SCHEMA = {
    "type": "string", "enum": ["agent", "fluentflow"], "default": "agent",
    "description": "Current agent first. fluentflow explicitly delegates to configured background models as fallback.",
}

_SPEAKER_DIARIZATION_SCHEMA = {
    "type": "boolean",
    "description": (
        "Label who spoke (说话人 A, B, ...) in the transcript and the note. Left out: what the user "
        "saved in Settings (off unless they turned it on). Turn it on for a meeting, interview or "
        "panel; it adds little for a lecture or one speaker."
    ),
}

_TEXT_MODEL_ONLY = "Only used when a text model writes the note (not Claude's frame note)."


@tool(
    description=("Submit one audio or video file that stays where it is, by absolute path "
        "on this machine. One file per call; loop for a folder. "
        + _MEDIA_NOTE_FLOW),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to the recording."},
            "title": {"type": "string"},
            "skip_summary": {"type": "boolean", "default": True},
            "intelligence": _INTELLIGENCE_SCHEMA,
            "note_mode": {"type": "string", "description": _TEXT_MODEL_ONLY},
            "prompt_preset": {"type": "string", "description": "A label recorded with the note. " + _TEXT_MODEL_ONLY},
            "system_prompt": {"type": "string", "description": "Instructions for the note. " + _TEXT_MODEL_ONLY},
            "stt_model": {"type": "string"},
            "speaker_diarization": _SPEAKER_DIARIZATION_SCHEMA,
            "cut_judge": _CUT_JUDGE_SCHEMA,
            "cut": _CUT_SCHEMA,
            "api_base": {"type": "string"},
        },
        "required": ["path"],
    },
)
def submit_local_media(
    path: str,
    title: str | None = None,
    skip_summary: bool | None = None,
    note_mode: str | None = None,
    prompt_preset: str | None = None,
    stt_model: str | None = None,
    speaker_diarization: bool | None = None,
    system_prompt: str | None = None,
    cut_judge: str | None = None,
    cut: bool | None = None,
    intelligence: str = "agent",
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    refusal = core._require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    return core._watching(core._agent_request(
        "POST",
        "/agent/v1/tasks",
        api_base=api_base,
        client_id=client_id,
        payload={
            "path": path,
            "input_type": "local_path",
            "title": title,
            "options": agent_options(_options(
                intelligence=intelligence,
                cut_judge=cut_judge,
                cut=None if cut is None or cut else "false",
                skip_summary=None if skip_summary is None else ("true" if skip_summary else "false"),
                note_mode=note_mode,
                prompt_preset=prompt_preset,
                system_prompt=system_prompt,
                stt_model=stt_model,
                # Left out unless the caller asks: the backend default is off.
                speaker_diarization=None if speaker_diarization is None else ("true" if speaker_diarization else "false"),
            )),
        },
    ), None, api_base, client_id)


@tool(
    description=("Submit a video URL or copied share text to FluentFlow. "
        + _MEDIA_NOTE_FLOW
        + " The same link is never processed twice by accident (tracking parameters "
        "such as utm_*, share_source, spm_id_from, vd_source are ignored when "
        "comparing; Bilibili ?p= still counts): if that link is already queued or "
        "running, no new task is made and the existing one comes back with "
        "duplicate_of_active=true; if it was already completed, the call fails with "
        "HTTP 409, code duplicate_link and existing_task_id — read that task, or ask "
        "the user and resubmit with allow_duplicate=true to process it again. A "
        "failed or cancelled earlier task never blocks a submission."),
    input_schema={
        "type": "object",
        "properties": {
            "input_text": {"type": "string", "description": "Video URL or copied share text."},
            "title": {"type": "string"},
            "stt_provider": {"type": "string", "default": "auto"},
            "skip_summary": {"type": "boolean", "default": True},
            "intelligence": _INTELLIGENCE_SCHEMA,
            "note_mode": {"type": "string", "description": _TEXT_MODEL_ONLY},
            "prompt_preset": {"type": "string", "description": "A label recorded with the note. " + _TEXT_MODEL_ONLY},
            "system_prompt": {"type": "string", "description": "Instructions for the note. " + _TEXT_MODEL_ONLY},
            "speaker_diarization": _SPEAKER_DIARIZATION_SCHEMA,
            "cut_judge": _CUT_JUDGE_SCHEMA,
            "cut": _CUT_SCHEMA,
            "allow_duplicate": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Process this link again although a completed task already has it. "
                    "Only after the user confirmed they want a second copy."
                ),
            },
            "api_base": {"type": "string"},
        },
        "required": ["input_text"],
    },
)
def submit_video_link(
    input_text: str,
    title: str | None = None,
    stt_provider: str = "auto",
    skip_summary: bool | None = None,
    note_mode: str | None = None,
    prompt_preset: str | None = None,
    allow_duplicate: bool = False,
    speaker_diarization: bool | None = None,
    system_prompt: str | None = None,
    cut_judge: str | None = None,
    cut: bool | None = None,
    intelligence: str = "agent",
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return core._watching(core._agent_request(
        "POST",
        "/agent/v1/tasks",
        api_base=api_base,
        client_id=client_id,
        payload={
            "input": input_text,
            "input_type": "video_link",
            "title": title,
            "allow_duplicate": bool(allow_duplicate),
            "options": agent_options(_options(
                intelligence=intelligence,
                cut_judge=cut_judge,
                cut=None if cut is None or cut else "false",
                stt_provider=stt_provider,
                skip_summary=None if skip_summary is None else ("true" if skip_summary else "false"),
                note_mode=note_mode,
                prompt_preset=prompt_preset,
                system_prompt=system_prompt,
                speaker_diarization=None if speaker_diarization is None else ("true" if speaker_diarization else "false"),
            )),
        },
        timeout=30,
    ), None, api_base, client_id)


@tool(
    description=("Store transcript text for the current agent to write its note. "
        "Defaults to intelligence=agent and skip_summary=true; follow with wait_task, "
        "get_agent_note_material and save_agent_note. Only delegate with intelligence=fluentflow "
        "when the agent cannot do it, failed, or the user asked; that uses configured models. "
        "note_mode, system_prompt and prompt_preset reach the text model on that fallback."),
    input_schema={
        "type": "object",
        "properties": {
            "transcript_text": {"type": "string"},
            "title": {"type": "string", "default": "Transcript"},
            "skip_summary": {"type": "boolean", "default": True},
            "intelligence": _INTELLIGENCE_SCHEMA,
            "note_mode": {"type": "string"},
            "prompt_preset": {"type": "string", "description": "A label recorded with the note; the instructions are system_prompt."},
            "system_prompt": {"type": "string", "description": "Instructions for the note."},
            "api_base": {"type": "string"},
        },
        "required": ["transcript_text"],
    },
)
def submit_transcript(
    transcript_text: str,
    title: str = "Transcript",
    skip_summary: bool | None = None,
    note_mode: str | None = None,
    prompt_preset: str | None = None,
    system_prompt: str | None = None,
    intelligence: str = "agent",
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return core._agent_request(
        "POST",
        "/agent/v1/tasks",
        api_base=api_base,
        client_id=client_id,
        payload={
            "input_type": "transcript",
            "transcript_text": transcript_text,
            "title": title,
            "wait": False,
            "options": agent_options(_options(
                intelligence=intelligence,
                skip_summary=None if skip_summary is None else ("true" if skip_summary else "false"),
                note_mode=note_mode,
                prompt_preset=prompt_preset,
                system_prompt=system_prompt,
            )),
        },
        timeout=30,
    )
