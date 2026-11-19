"""Tools that follow and steer one task: status, waiting, the package,
diagnosis, retry, stop a step, carry on, cancel.
"""
from __future__ import annotations

from typing import Any

from . import core
from .core import tool
from .submit import _SPEAKER_DIARIZATION_SCHEMA


@tool(
    description=("Read lightweight task status from FluentFlow. done is true only once every step "
        "after the transcript has ended too (cut, note, compressed copy, Feishu export); "
        "note_pending says the note is still to come. activity names the step it is on, the steps "
        "that failed or were stopped (stopped=true reads 已停下), and resume_from, the step "
        "resume_task would carry it on from. note_version "
        "is the version of the note now: read it before you change the note, and pass it as "
        "base_version when you save (save_agent_note, regenerate_note, write_note_from_cut_media). "
        "note_alignment says whether the note's sections have playback times: state, section_count, "
        "missing (how many have none). "
        "model_usage is what the task's model calls used: total, then steps (each with label, "
        "models, calls, input_tokens, cached_input_tokens, output_tokens, cost and its parts in "
        "items). cost maps a currency to an amount (CNY for DeepSeek, USD for Anthropic), worked "
        "out from the providers' published prices; it is empty when no call had a price "
        "(unpriced_calls counts those). Calls through the Claude subscription are counted in "
        "subscription_calls and cost nothing per call (reported_cost_usd is the command line's "
        "API-price figure for them, not money spent). Calls answered by the fake model of a test "
        "run are counted in fake_calls and cost nothing. A step named unattributed holds calls "
        "that were not put in a step. page_url is the task's page in FluentFlow (note and playback): open "
        "it when the user wants to see the task; never build that address yourself."),
    input_schema={
        "type": "object",
        "properties": {"task_id": {"type": "string"}, "api_base": {"type": "string"}},
        "required": ["task_id"],
    },
)
def get_task(
    task_id: str,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return core._watching(core._agent_request("GET", f"/agent/v1/tasks/{task_id}", api_base=api_base, client_id=client_id),
                     task_id, api_base, client_id)


# One wait_task call holds this long by default: under the 300 seconds Codex
# gives one tool call unless its MCP server sets tool_timeout_sec (measured
# 2026-10-27, Codex 0.164), with room for the answer to come back. The
# service allows up to WAIT_MAX_SECONDS for a host that waits longer.
WAIT_DEFAULT_SECONDS = 240
WAIT_MAX_SECONDS = 600


@tool(
    description=("Wait for a task, cut and note included, without spending a turn on each check: one call "
        "holds up to timeout_seconds (240 to 600; less is taken as 240) and returns the moment you have something "
        "to do. Between calls do nothing and tell the user nothing; call again straight away. A task reads "
        "completed once its transcript is stored, but done stays false while the recording is being cut "
        "(cut_running=true), until the note is written, failed or skipped (note_pending=true), and while "
        "the compressed copy or the Feishu export is still running. It returns at once: done=true, with "
        "task saying where things stand (note status and length, who wrote it, the output folder, next "
        "actions; read the note itself with get_task_package); agent_cut (status waiting, next) when the "
        "task waits for you to judge its cut: judge it with get_agent_cut_material and save_agent_cut, "
        "then wait again; agent_note (status ready, next) once the cut is decided and rendering and the "
        "note is yours to write: start it now (get_agent_note_material) instead of waiting for the "
        "render; this comes once. With done=true, next asks you to align the sections of a note you wrote "
        "to playback times when they have none, and page_url, the task's page to open for the user. Otherwise it returns the current state when its time is up. Keep the "
        "default timeout on Codex: it stops waiting on one tool call after 300 seconds unless its "
        "tool_timeout_sec is raised."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "timeout_seconds": {"type": "number", "default": WAIT_DEFAULT_SECONDS, "maximum": WAIT_MAX_SECONDS},
            "poll_interval_seconds": {"type": "number", "default": 2},
            "api_base": {"type": "string"},
        },
        "required": ["task_id"],
    },
)
def wait_task(
    task_id: str,
    timeout_seconds: float = WAIT_DEFAULT_SECONDS,
    poll_interval_seconds: float = 2,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    # A shorter wait saves nothing (it returns the moment there is something
    # to do) and costs a turn each time: on 2026-11-05 Codex passed 55 seconds
    # on every call of a 3-hour lesson, turns that each resend the conversation.
    held = min(max(float(timeout_seconds if timeout_seconds is not None else WAIT_DEFAULT_SECONDS),
                   WAIT_DEFAULT_SECONDS), WAIT_MAX_SECONDS)
    # Reported for the whole wait, not only once it returns.
    core._watch(task_id, api_base, client_id)
    return core._watching(core._agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/wait",
        api_base=api_base,
        client_id=client_id,
        payload={"timeout_seconds": held, "poll_interval_seconds": poll_interval_seconds},
        timeout=held + 30,
    ), task_id, api_base, client_id)


@tool(
    description=("Read the Agent Task Package for a FluentFlow task: status, the note, where every output "
        "is, and what happened, with model_usage as in get_task. step_timings says how long each step "
        "took: rows for the queue and each step that ran (seconds, status, running; the cut split into "
        "judging, rendering and its report in parts; a note you wrote is timed from your first "
        "get_agent_note_material to your save_agent_note, by_agent=true), total_seconds for the whole run, "
        "overlapping when steps ran side by side. Answer the user's \"how long did it take\" from it. "
        "The transcript is described "
        "(chars, preview, file path), not included; to write a note use get_agent_note_material, "
        "which hands it over in parts. note_alignment says whether the note's sections have times "
        "(state, section_count, missing); the times themselves come with get_agent_alignment_material."),
    input_schema={
        "type": "object",
        "properties": {"task_id": {"type": "string"}, "api_base": {"type": "string"}},
        "required": ["task_id"],
    },
    # The whole note is in it: 76,371 characters on a 3.5-hour interview,
    # which Claude Code would otherwise save to a file past 50,000.
    meta={"anthropic/maxResultSizeChars": 500_000},
)
def get_task_package(
    task_id: str,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return core._agent_request("GET", f"/agent/v1/tasks/{task_id}/package", api_base=api_base, client_id=client_id)


@tool(
    description=("Explain task or note generation failure state in a machine-readable form."),
    input_schema={
        "type": "object",
        "properties": {"task_id": {"type": "string"}, "api_base": {"type": "string"}},
        "required": ["task_id"],
    },
)
def diagnose_task(
    task_id: str,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return core._agent_request("GET", f"/agent/v1/tasks/{task_id}/diagnosis", api_base=api_base, client_id=client_id)


@tool(
    description=("Run a task again as a NEW task: the answer's task_id is the new one (source_task_id "
        "is the old), and the old task stays as it was. Works for failed, cancelled and "
        "completed tasks; a queued or running task must be cancelled first. Re-reads the "
        "recording FluentFlow kept, or the user's file for a task submitted by path; a link "
        "task whose video is gone downloads the link again, with the current browser-login "
        "setting. speaker_diarization=true re-runs it with speaker separation (who said "
        "what) switched on; left out, the old task's choice is kept. picture_note=true is for a "
        "video that failed with code no_speech_quiet (no speech, almost no sound) or "
        "no_speech_long (no speech, over 10 minutes, FluentFlow writing the note): write it up "
        "from its on-screen text and picture anyway, when the user wants that. Follow with wait_task on the new task_id."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "speaker_diarization": _SPEAKER_DIARIZATION_SCHEMA,
            "picture_note": {"type": "boolean"},
            "api_base": {"type": "string"},
        },
        "required": ["task_id"],
    },
)
def retry_task(
    task_id: str,
    speaker_diarization: bool | None = None,
    picture_note: bool | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if speaker_diarization is not None:
        payload["speaker_diarization"] = bool(speaker_diarization)
    if picture_note:
        payload["picture_note"] = True
    return core._agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/retry",
        api_base=api_base,
        client_id=client_id,
        payload=payload or None,
        timeout=30,
    )


_TASK_ONLY = {
    "type": "object",
    "properties": {"task_id": {"type": "string"}, "api_base": {"type": "string"}},
    "required": ["task_id"],
}


@tool(
    description=("Stop the step a task is on now: the transcription, the cut's render, the note being "
        "written (its Claude is stopped; a request already sent to a text model is not waited for), "
        "the compressed copy or the Feishu export. The step is recorded as stopped and the task reads "
        "已停下; nothing it already did is lost. Use it when the user asks to stop or pause a task, not "
        "to cancel it for good. Carry it on later with resume_task, which starts from the stopped step. "
        "Refused with code not_working when no step is running, and rerun_not_stoppable while only a "
        "separately started re-cut or note rewrite runs."),
    input_schema=_TASK_ONLY,
)
def stop_task_step(task_id: str, api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return core._agent_request("POST", f"/agent/v1/tasks/{task_id}/stop", api_base=api_base, client_id=client_id)


@tool(
    description=("Carry a task on from the first step that did not finish, on the same task (not a new "
        "one): a step the user stopped, a step that failed (the page calls this 重做这一步), or one a "
        "restart cut off. Steps already done are not done again: a cut whose decision was already made "
        "is only rendered again, without asking a model, and a transcription is not repeated when only "
        "its corrections were left. The answer's resume_from names the step; get_task's activity says "
        "the same before you call (resume_from, stopped_steps, failed_steps). Follow with wait_task. "
        "Refused with code busy while the task is still working, still_stopping shortly after a stop (a "
        "task stopped while waiting its turn keeps its place until the task ahead is done), nothing_to_resume when every step is done, no_steps for a task processed before steps "
        "were recorded (use retry_task), source_missing when the recording it needs is gone."),
    input_schema=_TASK_ONLY,
)
def resume_task(task_id: str, api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return core._agent_request("POST", f"/agent/v1/tasks/{task_id}/resume", api_base=api_base, client_id=client_id)


@tool(
    description=("Cancel a task that is still queued or transcribing (status queued or running); the "
        "same as the page's cancel. Once the transcript is stored the task reads completed and cancel "
        "is refused (code already_finished): stop_task_step stops a later step instead."),
    input_schema=_TASK_ONLY,
)
def cancel_task(task_id: str, api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return core._agent_request("POST", f"/agent/v1/tasks/{task_id}/cancel", api_base=api_base, client_id=client_id)
