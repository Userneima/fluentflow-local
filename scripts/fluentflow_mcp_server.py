#!/usr/bin/env python3
from __future__ import annotations

import os
import inspect
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.local_agent_client import (
    DEFAULT_CLIENT_ID,
    FluentFlowApiError,
    api_request,
    normalize_api_base,
)

PROTOCOL_VERSION = "2025-06-18"


def _token_from_project_env() -> None:
    """Use the access token the installer wrote into the repository .env.

    The MCP client's own configuration may pass it, and then that value wins; when
    it does not, the token comes from the same file the backend reads, so the two
    sides cannot disagree and registering the server needs no secret.
    """
    if (os.environ.get("FLUENTFLOW_ACCESS_TOKEN") or "").strip():
        return
    try:
        lines = (PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        key, _, value = line.strip().partition("=")
        if key.strip() == "FLUENTFLOW_ACCESS_TOKEN" and value.strip():
            os.environ["FLUENTFLOW_ACCESS_TOKEN"] = value.strip().strip("'\"")
            return


_token_from_project_env()


def _app_version() -> str:
    try:
        return (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


SERVER_INFO = {"name": "fluentflow-local", "version": _app_version()}


def _client_id(value: str | None = None) -> str:
    return (value or os.environ.get("FLUENTFLOW_CLIENT_ID") or DEFAULT_CLIENT_ID).strip() or DEFAULT_CLIENT_ID


# Which FluentFlow is answering, asked rather than assumed.
#
# Two editions serve the same ``/agent/v1`` routes with different intake, and for
# a while both answered on 127.0.0.1:8000. The rejection a path submission gets
# from the hosted edition lists the inputs that one accepts and never says which
# backend replied, so on 2026-09-15 it was read as "the MCP tool is newer than
# this backend" and a job this edition could have done fell back to standalone
# scripts. ``/health`` now declares the edition; only the tools that actually
# need one ask, so pointing this client at either backend still works for the
# ten tools both editions serve.
_START_HINT = "请双击桌面上的「FluentFlow Local」启动它，等它把浏览器打开之后重试。"
# A 401 is almost always two copies of one token that drifted: the backend reads
# FLUENTFLOW_ACCESS_TOKEN from the repository .env, the MCP client sends whatever
# its own config says, and nobody can see both at once from inside a chat.
_UNAUTHORIZED_HINT = (
    "（后端拒绝了访问令牌。请核对仓库根目录 .env 里的 FLUENTFLOW_ACCESS_TOKEN "
    "和 MCP 客户端配置（例如 ~/.claude.json 或 Codex 的 config.toml）里的 "
    "FLUENTFLOW_ACCESS_TOKEN 是否完全一致；改过 .env 之后要重启 FluentFlow Local 才生效。）"
)


def _unreachable(api_base: str, reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"FluentFlow Local 没有在 {api_base} 应答（{reason}）。{_START_HINT}",
        "status": None,
        "payload": None,
    }


def _backend_edition(api_base: str) -> tuple[str, dict[str, Any] | None]:
    """The edition answering at ``api_base``, or the error payload explaining why not.

    Probed per call rather than cached: the case this exists for is a backend
    being swapped on a port, and a cached identity is wrong exactly then. One
    loopback GET is nothing beside submitting a media job.
    """
    try:
        health = api_request("GET", api_base, "/health", timeout=5)
    except FluentFlowApiError as exc:
        return "", _unreachable(api_base, str(exc))
    edition = str(health.get("edition") or "").strip().lower()
    if not edition:
        # A backend from before /health declared its edition: runtime.execution
        # was the only discriminator then, and only the local edition set it.
        runtime = health.get("runtime") if isinstance(health.get("runtime"), dict) else {}
        edition = "local" if str(runtime.get("execution") or "").strip().lower() == "local" else "hosted"
    return edition, None


def _require_local_edition(api_base: str) -> dict[str, Any] | None:
    """Why this tool cannot run against ``api_base``, or None when it can."""
    edition, failure = _backend_edition(api_base)
    if failure is not None:
        return failure
    if edition != "local":
        return {
            "ok": False,
            "error": (
                f"{api_base} 上应答的是 FluentFlow Hosted，这个工具要的是 FluentFlow Local。"
                "托管版按设计不收本机文件路径，它拒绝提交并不代表本地处理这个功能不存在。"
                f"{_START_HINT}"
            ),
            "status": None,
            "payload": None,
        }
    return None


def _agent_request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
    timeout: float = 60,
) -> dict[str, Any]:
    base = normalize_api_base(api_base)
    try:
        return api_request(
            method,
            base,
            path,
            payload=payload,
            client_id=_client_id(client_id),
            access_token=os.environ.get("FLUENTFLOW_ACCESS_TOKEN"),
            timeout=timeout,
        )
    except FluentFlowApiError as exc:
        if exc.status is None:
            # No HTTP status means nothing answered. Say how to start it instead of
            # handing the caller a bare "Connection refused" to interpret.
            return _unreachable(base, str(exc))
        message = str(exc)
        if exc.status == 401:
            message += _UNAUTHORIZED_HINT
        return {
            "ok": False,
            "error": message,
            "status": exc.status,
            "payload": exc.payload if isinstance(exc.payload, dict) else None,
        }


def _options(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in (None, "")}


# Which tasks this conversation is responsible for, reported to FluentFlow
# every half minute while this process lives. Every agent conversation starts
# its own MCP process and the process ends with the conversation, so when the
# reports stop, a task waiting for this agent to judge its cut stops waiting
# (``backend/core/agent_cut.py``).
HEARTBEAT_SECONDS = 30.0
_watched: dict[str, tuple[str | None, str | None]] = {}
_watch_lock = threading.Lock()
_heart: threading.Thread | None = None
# Only a real MCP process (``main``) reports; imported for tests it does not.
_heartbeat_enabled = False


def _watch(task_id: Any, api_base: str | None, client_id: str | None) -> None:
    global _heart
    if not task_id:
        return
    with _watch_lock:
        _watched[str(task_id)] = (api_base, client_id)
        if _heartbeat_enabled and (_heart is None or not _heart.is_alive()):
            _heart = threading.Thread(target=_heartbeat_loop, name="fluentflow-heartbeat", daemon=True)
            _heart.start()


def _heartbeat_once() -> None:
    with _watch_lock:
        groups: dict[tuple[str | None, str | None], list[str]] = {}
        for task_id, where in _watched.items():
            groups.setdefault(where, []).append(task_id)
    for (api_base, client_id), ids in groups.items():
        answer = _agent_request(
            "POST", "/agent/v1/agent-cut/heartbeat", api_base=api_base, client_id=client_id,
            payload={"task_ids": ids}, timeout=10,
        )
        if not isinstance(answer, dict) or answer.get("ok") is False or not isinstance(answer.get("watching"), list):
            continue  # FluentFlow not answering (restarting): keep them all
        keep = {str(item) for item in answer["watching"]}
        with _watch_lock:
            for task_id in ids:
                if task_id not in keep:
                    _watched.pop(task_id, None)


def _heartbeat_loop() -> None:
    while True:
        try:
            _heartbeat_once()
        except Exception:  # noqa: BLE001 - a missed beat is retried
            pass
        with _watch_lock:
            if not _watched:
                return
        time.sleep(HEARTBEAT_SECONDS)


def _watching(answer: dict[str, Any], task_id: Any, api_base: str | None, client_id: str | None) -> dict[str, Any]:
    if isinstance(answer, dict) and answer.get("ok") is not False:
        _watch(task_id or answer.get("task_id"), api_base, client_id)
    return answer


_CUT_JUDGE_SCHEMA = {
    "type": "string",
    "enum": ["auto", "agent", "fluentflow"],
    "default": "auto",
    "description": (
        "Who decides what to cut. auto: FluentFlow's own model when it has one, otherwise the task "
        "waits after transcription for you to judge (get_agent_cut_material, save_agent_cut). "
        "agent: always you. fluentflow: always FluentFlow (only the loudness when it has no model)."
    ),
}


# ── tools ────────────────────────────────────────────────────────────────────
#
# What an agent is told about a tool is written once, on the tool: the
# ``tool`` decorator right above each function holds its description and its
# input schema, and the lists the server answers with are made from them.

_TOOLS: dict[str, tuple[Any, dict[str, Any]]] = {}


def tool(*, description: str, input_schema: dict[str, Any], meta: dict[str, Any] | None = None):
    """Register the function below as a tool, described here and nowhere else."""
    def register(func):
        definition = {"name": func.__name__, "description": description, "inputSchema": input_schema}
        if meta:
            definition["_meta"] = meta
        _TOOLS[func.__name__] = (func, definition)
        return func
    return register


# Who writes the note on a media submission, said once for both submit tools.
_MEDIA_NOTE_FLOW = (
    "By default the task transcribes the original, then a model reads the whole transcript "
    "(and screenshots of long silent stretches where the picture moves) and decides what to "
    "cut: breath gaps, waiting before the start, leftover after the end, sound checks, a "
    "sentence said twice. The cut is rendered once and the transcript moved onto it; "
    "FluentFlow picks that model itself (Claude, then a picture model set in "
    "FLUENTFLOW_VISION_*, then the text model). Its processing report, with the cuts the "
    "user should judge, is on the task page. After the cut, Claude writes the note from "
    "the frames and the transcript, paid by the user's own Anthropic API key (the local "
    "Claude subscription only when FLUENTFLOW_VISUAL_NOTE_CHANNEL=subscription is set); "
    "without a usable Claude the configured text model writes it from the cut's transcript. "
    "Who decides the cut is cut_judge: by default FluentFlow's own model, and when it has none "
    "the task stops after transcription and waits for you (wait_task then returns agent_cut "
    "with status waiting): call get_agent_cut_material, judge, save_agent_cut, then "
    "save_agent_cut_report. "
    "To write the note yourself, on your own subscription, pass skip_summary=true, wait for "
    "done, then use get_agent_note_material and save_agent_note; with no model at all the "
    "task also stops at the transcript for you to do that. note_mode, prompt_preset and "
    "system_prompt only apply when a text model writes the note; Claude's frame note "
    "ignores them. The text model, speaker separation and auto export to Feishu follow what "
    "the user saved in FluentFlow's Settings, the same as a recording submitted on the page; "
    "speaker_diarization=true or false overrides it for this task. Returns at once; follow "
    "with wait_task."
)

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
        "on this machine. Local edition only; the backend accepts it from localhost "
        "only, and this tool says so plainly when the other edition is the one "
        "answering. One file per call — loop for a folder. "
        + _MEDIA_NOTE_FLOW),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to the recording."},
            "title": {"type": "string"},
            "skip_summary": {"type": "boolean", "default": False},
            "note_mode": {"type": "string", "description": _TEXT_MODEL_ONLY},
            "prompt_preset": {"type": "string", "description": "A label recorded with the note. " + _TEXT_MODEL_ONLY},
            "system_prompt": {"type": "string", "description": "Instructions for the note. " + _TEXT_MODEL_ONLY},
            "stt_model": {"type": "string"},
            "speaker_diarization": _SPEAKER_DIARIZATION_SCHEMA,
            "cut_judge": _CUT_JUDGE_SCHEMA,
            "api_base": {"type": "string"},
        },
        "required": ["path"],
    },
)
def submit_local_media(
    path: str,
    title: str | None = None,
    skip_summary: bool = False,
    note_mode: str | None = None,
    prompt_preset: str | None = None,
    stt_model: str | None = None,
    speaker_diarization: bool | None = None,
    system_prompt: str | None = None,
    cut_judge: str = "auto",
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    refusal = _require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    return _watching(_agent_request(
        "POST",
        "/agent/v1/tasks",
        api_base=api_base,
        client_id=client_id,
        payload={
            "path": path,
            "input_type": "local_path",
            "title": title,
            "options": _options(
                cut_judge=cut_judge,
                skip_summary="true" if skip_summary else "false",
                note_mode=note_mode,
                prompt_preset=prompt_preset,
                system_prompt=system_prompt,
                stt_model=stt_model,
                # Left out unless the caller asks: the backend default is off.
                speaker_diarization=None if speaker_diarization is None else ("true" if speaker_diarization else "false"),
            ),
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
            "skip_summary": {"type": "boolean", "default": False},
            "note_mode": {"type": "string", "description": _TEXT_MODEL_ONLY},
            "prompt_preset": {"type": "string", "description": "A label recorded with the note. " + _TEXT_MODEL_ONLY},
            "system_prompt": {"type": "string", "description": "Instructions for the note. " + _TEXT_MODEL_ONLY},
            "speaker_diarization": _SPEAKER_DIARIZATION_SCHEMA,
            "cut_judge": _CUT_JUDGE_SCHEMA,
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
    skip_summary: bool = False,
    note_mode: str | None = None,
    prompt_preset: str | None = None,
    allow_duplicate: bool = False,
    speaker_diarization: bool | None = None,
    system_prompt: str | None = None,
    cut_judge: str = "auto",
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return _watching(_agent_request(
        "POST",
        "/agent/v1/tasks",
        api_base=api_base,
        client_id=client_id,
        payload={
            "input": input_text,
            "input_type": "video_link",
            "title": title,
            "allow_duplicate": bool(allow_duplicate),
            "options": _options(
                cut_judge=cut_judge,
                stt_provider=stt_provider,
                skip_summary="true" if skip_summary else "false",
                note_mode=note_mode,
                prompt_preset=prompt_preset,
                system_prompt=system_prompt,
                speaker_diarization=None if speaker_diarization is None else ("true" if speaker_diarization else "false"),
            ),
        },
        timeout=30,
    ), None, api_base, client_id)


@tool(
    description=("Submit transcript text and have the configured text model write a note from it "
        "(Claude's frame note needs media, so it does not apply here). Returns the task_id "
        "at once; the note is written in the background, typically within a minute or two "
        "and capped at FLUENTFLOW_NOTE_DEADLINE_SECONDS (default an hour) — follow with "
        "wait_task. note_mode, system_prompt and prompt_preset reach the model. "
        "skip_summary=true stores the transcript without a note."),
    input_schema={
        "type": "object",
        "properties": {
            "transcript_text": {"type": "string"},
            "title": {"type": "string", "default": "Transcript"},
            "skip_summary": {"type": "boolean", "default": False},
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
    skip_summary: bool = False,
    note_mode: str | None = None,
    prompt_preset: str | None = None,
    system_prompt: str | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return _agent_request(
        "POST",
        "/agent/v1/tasks",
        api_base=api_base,
        client_id=client_id,
        payload={
            "input_type": "transcript",
            "transcript_text": transcript_text,
            "title": title,
            "wait": False,
            "options": _options(
                skip_summary="true" if skip_summary else "false",
                note_mode=note_mode,
                prompt_preset=prompt_preset,
                system_prompt=system_prompt,
            ),
        },
        timeout=30,
    )


@tool(
    description=("Read lightweight task status from FluentFlow. done is true only once every step "
        "after the transcript has ended too (cut, note, compressed copy, Feishu export); "
        "note_pending says the note is still to come. activity names the step it is on, the steps "
        "that failed or were stopped (stopped=true reads 已停下), and resume_from, the step "
        "resume_task would carry it on from. note_version "
        "is the version of the note now: read it before you change the note, and pass it as "
        "base_version when you save (save_agent_note, regenerate_note, write_note_from_cut_media). "
        "model_usage is what the task's model calls used: total, then steps (each with label, "
        "models, calls, input_tokens, cached_input_tokens, output_tokens, cost and its parts in "
        "items). cost maps a currency to an amount (CNY for DeepSeek, USD for Anthropic), worked "
        "out from the providers' published prices; it is empty when no call had a price "
        "(unpriced_calls counts those). Calls through the Claude subscription are counted in "
        "subscription_calls and cost nothing per call (reported_cost_usd is the command line's "
        "API-price figure for them, not money spent). A step named unattributed holds calls "
        "that were not put in a step."),
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
    return _watching(_agent_request("GET", f"/agent/v1/tasks/{task_id}", api_base=api_base, client_id=client_id),
                     task_id, api_base, client_id)


@tool(
    description=("Wait for a task, cut and note included: a task reads completed once its transcript "
        "is stored, but done stays false while the recording is being cut (cut_running=true) "
        "and until the note is written, failed or skipped (note_pending=true), and while the "
        "compressed copy or the Feishu export is still running. When done, task says where things stand (note status "
        "and length, who wrote it, the output folder, next actions); read the note itself "
        "with get_task_package. Otherwise the current state; call again until done. When the "
        "task is waiting for you to judge its cut it returns at once with agent_cut (status "
        "waiting, next): judge it with get_agent_cut_material and save_agent_cut, then wait again."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "timeout_seconds": {"type": "number", "default": 30},
            "poll_interval_seconds": {"type": "number", "default": 2},
            "api_base": {"type": "string"},
        },
        "required": ["task_id"],
    },
)
def wait_task(
    task_id: str,
    timeout_seconds: float = 30,
    poll_interval_seconds: float = 2,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return _watching(_agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/wait",
        api_base=api_base,
        client_id=client_id,
        payload={"timeout_seconds": timeout_seconds, "poll_interval_seconds": poll_interval_seconds},
        timeout=max(5, min(float(timeout_seconds or 30) + 10, 75)),
    ), task_id, api_base, client_id)


@tool(
    description=("Read the Agent Task Package for a FluentFlow task: status, the note, where every output "
        "is, and what happened, with model_usage as in get_task. The transcript is described "
        "(chars, preview, file path), not included; to write a note use get_agent_note_material, "
        "which hands it over in parts."),
    input_schema={
        "type": "object",
        "properties": {"task_id": {"type": "string"}, "api_base": {"type": "string"}},
        "required": ["task_id"],
    },
)
def get_task_package(
    task_id: str,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return _agent_request("GET", f"/agent/v1/tasks/{task_id}/package", api_base=api_base, client_id=client_id)


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
    return _agent_request("GET", f"/agent/v1/tasks/{task_id}/diagnosis", api_base=api_base, client_id=client_id)


@tool(
    description=("Run a task again as a NEW task: the answer's task_id is the new one (source_task_id "
        "is the old), and the old task stays as it was. Works for failed, cancelled and "
        "completed tasks; a queued or running task must be cancelled first. Re-reads the "
        "recording FluentFlow kept, or the user's file for a task submitted by path; a link "
        "task whose video is gone downloads the link again, with the current browser-login "
        "setting. speaker_diarization=true re-runs it with speaker separation (who said "
        "what) switched on; left out, the old task's choice is kept. Follow with wait_task "
        "on the new task_id."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "speaker_diarization": _SPEAKER_DIARIZATION_SCHEMA,
            "api_base": {"type": "string"},
        },
        "required": ["task_id"],
    },
)
def retry_task(
    task_id: str,
    speaker_diarization: bool | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    payload = None if speaker_diarization is None else {"speaker_diarization": bool(speaker_diarization)}
    return _agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/retry",
        api_base=api_base,
        client_id=client_id,
        payload=payload,
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
    return _agent_request("POST", f"/agent/v1/tasks/{task_id}/stop", api_base=api_base, client_id=client_id)


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
    return _agent_request("POST", f"/agent/v1/tasks/{task_id}/resume", api_base=api_base, client_id=client_id)


@tool(
    description=("Cancel a task that is still queued or transcribing (status queued or running); the "
        "same as the page's cancel. Once the transcript is stored the task reads completed and cancel "
        "is refused (code already_finished): stop_task_step stops a later step instead."),
    input_schema=_TASK_ONLY,
)
def cancel_task(task_id: str, api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return _agent_request("POST", f"/agent/v1/tasks/{task_id}/cancel", api_base=api_base, client_id=client_id)


@tool(
    description=("Read a cut task's processing report, the list the page shows under 处理说明: each "
        "decision with what was cut or kept (action removed or kept), why (text), its time span, its "
        "ref (to turn it around with flip_cut_decision) and its key (to agree with it with "
        "answer_cut_decisions); the section with to_judge=true is what the user should look at; "
        "user_changes are the user's earlier turnarounds (undo one with undo_cut_change by its index); "
        "agreed lists the keys already agreed. Refused with code no_report for a task with no report."),
    input_schema=_TASK_ONLY,
)
def get_cut_report(task_id: str, api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return _agent_request("GET", f"/agent/v1/tasks/{task_id}/cut-report", api_base=api_base, client_id=client_id)


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
    return _agent_request(
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
    return _agent_request(
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
    return _agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/cut-report/undo",
        api_base=api_base, client_id=client_id, payload={"index": index},
    )


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
    return _agent_request(
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
    return _agent_request(
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
    refusal = _require_local_edition(normalize_api_base(api_base))
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
    return _agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/visual-note",
        api_base=api_base,
        client_id=client_id,
        payload=payload,
        timeout=60,
    )


@tool(
    description=("Export a completed task note. target=pdf writes a PDF with its screenshots to output_dir "
        "(default ~/Documents/FluentFlow 笔记) and returns pdf_path; it needs Chrome or Edge on the "
        "machine, and without one returns html_path to print instead. target=lark exports to Feishu/Lark. "
        "For Feishu: without lark_export_route or "
        "folder_token the user's export settings apply (route, then folder), else auto: "
        "lark-cli when it is signed in, otherwise the user's Feishu app. The document "
        "link is recorded on the task, so the editor shows it too."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "target": {"type": "string", "enum": ["lark", "pdf"], "default": "lark"},
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
        "required": ["task_id"],
    },
)
def export_result(
    task_id: str,
    target: str = "lark",
    title: str | None = None,
    lark_export_route: str | None = None,
    folder_token: str | None = None,
    output_dir: str | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return _agent_request(
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


@tool(
    description=("Write a task's note yourself instead of a model FluentFlow is configured to pay for "
        "(the way to get a note on the user's own subscription). Returns the whole timestamped "
        "transcript, the frames as local file paths with their moments, and instructions (the "
        "note skill in effect plus the fixed contract). Open the frames you need, write the whole "
        "note, then call save_agent_note once. Do this in a subagent with a fresh context when "
        "your host has them, so the transcript does not fill the user's conversation: give it the "
        "task_id and have it fetch, write and save. If the result came back truncated or saved to "
        "a file because it was too large for one tool result, call again with small_parts=true: "
        "the recording then comes in total_parts parts to fetch, write and save one at a time. "
        "The first call for a video extracts frames (up to a minute). note_version is the task's "
        "note as you read it: pass it to save_agent_note as base_version."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "part": {"type": "integer", "default": 1, "description": "Which part, from 1 to total_parts (small_parts only)."},
            "small_parts": {
                "type": "boolean", "default": False,
                "description": "Hand the recording over in parts of about 15,000 characters, for a host whose tool results are too small for the whole transcript.",
            },
            "api_base": {"type": "string"},
        },
        "required": ["task_id"],
    },
    meta={"anthropic/maxResultSizeChars": 500_000},
)
def get_agent_note_material(
    task_id: str,
    part: int = 1,
    small_parts: bool = False,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    refusal = _require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    return _agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/agent-note/material",
        api_base=api_base,
        client_id=client_id,
        payload={"part": int(part or 1), "small_parts": bool(small_parts)},
        timeout=300,
    )


@tool(
    description=("Save the note you wrote from get_agent_note_material as the task's note. The previous "
        "note is kept and restorable (write_note_from_cut_media restore_previous_note=true). "
        "note_markdown for a one-part recording. For several parts, save each as soon as it is "
        "written with part and note_markdown: saved parts stay in FluentFlow across conversations, "
        "the task's note is replaced once every part is in, and the answer's next says what to do "
        "(the remaining parts, then a read-through). note_markdown without part on such a recording "
        "is a whole edited note. Cite "
        "frames by file name only. A note far too short for its transcript is refused and "
        "nothing is replaced. The first save of each part is checked against its transcript for "
        "knowledge points the note never mentions (when FluentFlow has a model to check with) and "
        "they come back in coverage.missing, by part: when it is not empty, write in the ones worth "
        "keeping and save that part again once. A part saved again is not checked again. "
        "Pass base_version (note_version from the material or get_task): if the user changed the "
        "note in the page, Obsidian or Feishu while you wrote, the save is refused with code stale "
        "and nothing is replaced; get_task_package for the current note, merge your note into it "
        "and save again with the new note_version. Each answer carries note_version as the note "
        "is after it; use that one for the next save of a part."),
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
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    refusal = _require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    return _agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/agent-note",
        api_base=api_base,
        client_id=client_id,
        payload=_options(note_markdown=note_markdown, part=part, part_notes=part_notes, basis_note=basis_note,
                         model=model, base_version=base_version),
        # Saving runs FluentFlow's coverage check on each part, a model request
        # over that part's whole transcript.
        timeout=600,
    )


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
    refusal = _require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    return _agent_request(
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
        "those gaps). Then call save_agent_cut. If you do not answer within decide_within_seconds, or "
        "this conversation closes, FluentFlow judges the cut itself."),
    input_schema={
        "type": "object",
        "properties": {"task_id": {"type": "string"}, "api_base": {"type": "string"}},
        "required": ["task_id"],
    },
    meta={"anthropic/maxResultSizeChars": 500_000},
)
def get_agent_cut_material(task_id: str, api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    refusal = _require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    return _watching(_agent_request(
        "GET", f"/agent/v1/tasks/{task_id}/agent-cut/material", api_base=api_base, client_id=client_id,
    ), task_id, api_base, client_id)


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
    refusal = _require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    return _watching(_agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/agent-cut", api_base=api_base, client_id=client_id,
        payload={"keep_gaps": keep_gaps or [], "remove": remove or [], "model": model},
    ), task_id, api_base, client_id)


@tool(
    description=("Send the processing report for the cut you judged: the facts save_agent_cut listed, organised "
        "as its report_instructions say (summary, and sections of items that cite facts by ref; mark "
        "unsure with a doubt only where you are not sure). It is shown on the task page and kept when "
        "the user changes a cut later. The answer's to_judge is how many places the page asks the user "
        "to judge (yours plus the ones FluentFlow always asks about, such as removed talk): tell the user "
        "that number, not your own count."),
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
    refusal = _require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    return _agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/agent-cut/report", api_base=api_base, client_id=client_id,
        payload={"report": {"summary": summary, "sections": sections or []}},
    )


@tool(
    description=("How FluentFlow on this machine handles a task you submit, step by step (getting the material, "
        "cutting, transcription, notes, outputs, agent connection), and anything the user has to fix first. "
        "The same list the Settings page shows. Use it when the user asks what FluentFlow is set up to do."),
    input_schema={"type": "object", "properties": {"api_base": {"type": "string"}}},
)
def get_status(api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return _agent_request("GET", "/agent/v1/status", api_base=api_base, client_id=client_id)


@tool(
    description=("The note-writing rules in effect (text, version, default or the user's own) and every rule added from feedback, with the feedback each came from."),
    input_schema={"type": "object", "properties": {"api_base": {"type": "string"}}},
)
def get_note_skill(api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return _agent_request("GET", "/agent/v1/note-skill", api_base=api_base, client_id=client_id)


@tool(
    description=("Keep a problem the user reported about a note as a test case. kind=one_off for a single "
        "error (a misspelt name) or pattern for a way of writing that would recur. A pattern needs "
        "criterion: how a checker tells the problem is fixed. With task_id the transcript of that "
        "part is frozen into the case. When the user points out a problem in a note (often with a screenshot): fix that note first (save_agent_note). If the problem would come back in other notes, record it with record_note_feedback kind=pattern, check list_note_feedback for the same problem reported before (if so, show the user the existing rule and the old and new versions before changing it), then draft a rule, rewrite the affected part several times with it, have a separate checker that did not write it judge only whether the problem is still there, and when it passes call apply_note_rule and tell the user the rule in one sentence."),
    input_schema={
        "type": "object",
        "properties": {
            "problem": {"type": "string", "description": "What the user said is wrong, in their words."},
            "kind": {"type": "string", "enum": ["one_off", "pattern"]},
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
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return _agent_request(
        "POST", "/agent/v1/note-feedback", api_base=api_base, client_id=client_id,
        payload=_options(task_id=task_id, problem=problem, kind=kind, criterion=criterion,
                         note_excerpt=note_excerpt, part=part, transcript_excerpt=transcript_excerpt),
    )


@tool(
    description=("Every problem the user has reported, newest first, with rule_id when a rule came of it. Read it to tell whether a new report is a problem that came back."),
    input_schema={"type": "object", "properties": {"api_base": {"type": "string"}}},
)
def list_note_feedback(api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return _agent_request("GET", "/agent/v1/note-feedback", api_base=api_base, client_id=client_id)


@tool(
    description=("Have FluentFlow's own model write one part of a task's note with candidate rules, without "
        "saving it, for verifying a rule when FluentFlow writes the user's notes. When no model is "
        "configured, write the part yourself from get_agent_note_material with the candidate rules."),
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
    return _agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/note-draft", api_base=api_base, client_id=client_id,
        payload={"rules": rules, "part": int(part or 1)}, timeout=600,
    )


@tool(
    description=("Write a rule you verified into the user's note skill. skill_text is the whole new skill "
        "with the rule merged where it belongs; the previous skill is kept and can be restored from "
        "the settings page. rounds: how many rewrites passed the checker; evidence: one or two "
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
        "required": ["skill_text", "rule", "feedback_ids", "rounds", "evidence"],
    },
)
def apply_note_rule(
    skill_text: str,
    rule: str,
    feedback_ids: list[str],
    rounds: int,
    evidence: str,
    base_version: str | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    return _agent_request(
        "POST", "/agent/v1/note-skill/rules", api_base=api_base, client_id=client_id,
        payload={"skill_text": skill_text, "rule": rule, "feedback_ids": list(feedback_ids),
                 "rounds": int(rounds), "evidence": evidence, "base_version": base_version},
    )


@tool(
    description=("Find tasks: newest first, or only those whose title, note title (the note's first heading) "
        "or file name contains query. Use it to find which task a screenshot of a note came from: "
        "search a few characters of the title in the screenshot. Returns task_id, title, note_title, "
        "created_at, status, has_note."),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
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

    return _agent_request(
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
        "web_edit, obsidian, agent_note, rewrite, generated, feishu_pull, export, restore, switch_back, adjust; "
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
    return _agent_request(
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

    return _agent_request(
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

    return _agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/note/versions/{quote(version_id, safe='')}/restore",
        api_base=api_base, client_id=client_id, payload={"base_version": base_version},
    )


@tool(
    description=("Words in the task's transcript that FluentFlow replaced from the subtitles burned into the "
        "video picture, right after transcription and before the note was written. Each has an id, the "
        "time, from (what the transcript said), to (the subtitle's spelling), and the subtitle line it "
        "rests on. subtitle_correction says how the step went: skipped with its reason (no subtitles in "
        "the picture, no text recognition on this machine, no picture, no model), no_changes or completed. "
        "Show the user these when they say a name or term in the transcript or note was changed wrongly."),
    input_schema={
        "type": "object",
        "properties": {"task_id": {"type": "string"}, "api_base": {"type": "string"}},
        "required": ["task_id"],
    },
)
def list_subtitle_corrections(task_id: str, api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return _agent_request("GET", f"/agent/v1/tasks/{task_id}/subtitle-corrections", api_base=api_base, client_id=client_id)


@tool(
    description=("Undo subtitle corrections: each named one (ids from list_subtitle_corrections, or all when ids "
        "is empty) goes back to what the transcript said. Ask the user first. Refused with code "
        "task_busy while the task is still transcribing, cutting or writing its note: wait for it to "
        "finish. The task's transcript (every copy of it), "
        "its subtitle files and the output folder follow; a note already written is not rewritten "
        "(regenerate_note does that if the user wants it). not_found lists ids whose sentence was "
        "edited since and could not be found."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "ids": {"type": "array", "items": {"type": "string"}},
            "api_base": {"type": "string"},
        },
        "required": ["task_id"],
    },
)
def revert_subtitle_corrections(
    task_id: str, ids: list[str] | None = None, api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    return _agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/subtitle-corrections/revert",
        api_base=api_base, client_id=client_id, payload={"ids": ids or []},
    )


@tool(
    description=("Words in the task's transcript that FluentFlow corrected from the series' word list "
        "(FluentFlow 校正文档.md in the recording's folder or above) and the surrounding sentences, "
        "after transcription (and after the subtitle check) and before the note was written. Each has an "
        "id, the time, sentence (its number in the transcript now; null when that sentence was edited "
        "since), from, to, basis (glossary: the word list's spelling, entry is that line; context: the "
        "sentences around it decided, why says how) and the sentence before and after. "
        "glossary_correction says how the step went: skipped with its reason (no transcript, no model), "
        "failed with the model's error, no_changes or completed, with counts by basis, the channel "
        "(subscription or a key) and the seconds it took."),
    input_schema={
        "type": "object",
        "properties": {"task_id": {"type": "string"}, "api_base": {"type": "string"}},
        "required": ["task_id"],
    },
)
def list_glossary_corrections(task_id: str, api_base: str | None = None, client_id: str | None = None) -> dict[str, Any]:
    return _agent_request("GET", f"/agent/v1/tasks/{task_id}/glossary-corrections", api_base=api_base, client_id=client_id)


@tool(
    description=("Run the word-list correction on a finished task again, for a task processed before the step "
        "existed or after the user added to the word list. Reads the whole transcript with the word list "
        "found for the recording's folder (with no word list, only what the context makes certain is "
        "changed). Sentences the user edited in the editor are left alone. Each change replaces a misheard "
        "word, never a sentence; the new changes join the task's records and undo the same way "
        "(list_glossary_corrections, revert_glossary_corrections). The transcript, its subtitle files and "
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
    return _agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/glossary-corrections/run",
        api_base=api_base, client_id=client_id, payload={}, timeout=900,
    )


@tool(
    description=("Undo glossary corrections: each named one (ids from list_glossary_corrections, or all when ids "
        "is empty) goes back to what the transcript said, in every copy of the transcript, the subtitle "
        "files and the output folder. Ask the user first. Refused with code task_busy while the task is "
        "still being processed. not_found lists ids whose sentence was edited since and could not be found."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "ids": {"type": "array", "items": {"type": "string"}},
            "api_base": {"type": "string"},
        },
        "required": ["task_id"],
    },
)
def revert_glossary_corrections(
    task_id: str, ids: list[str] | None = None, api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    return _agent_request(
        "POST", f"/agent/v1/tasks/{task_id}/glossary-corrections/revert",
        api_base=api_base, client_id=client_id, payload={"ids": ids or []},
    )


@tool(
    description=("Write the review note (复习笔记) for a recording: a second note, beside the follow-along note "
        "(<名>_笔记.md), reorganised by topic for learning it again later. It opens with the main line and a "
        "map of the chapters; each point appears once with its sources as links back to the follow-along "
        "note's chapters plus the time in the cut video; the user's own question boxes are folded into the "
        "points they ask about; the speaker's predictions are marked as the judgement at recording time; "
        "then a term list, 10 to 15 self-test questions, what is still unclear, and which version of the "
        "follow-along note it was written from. Written as <名>_复习笔记.md with a PDF beside it; an earlier "
        "one is kept in 过程文件/历史版本. The base is the follow-along md as it is on disk now, with the "
        "user's corrections; later edits to it do not change the review note until it is written again. "
        "Give task_id, or note_path (the follow-along md's path) when the user moved the output folder and "
        "FluentFlow can no longer find it; with note_path only, the task is found from the folder when it "
        "can be (given both, they must be the same note). note_path must be a follow-along note in the "
        "user's home folder. A second task's note on the same recording (<名>_笔记 (2).md) gets "
        "<名>_复习笔记 (2).md. Only when the user asks for one. Uses the Claude subscription first when this machine "
        "opted into it, the user's key taking over if it fails; takes about ten minutes for a three-hour "
        "recording. Refused with code review_note_failed and the reason (no model, the note not found, an "
        "incomplete answer: nothing is written and the task is not affected), already_running while one is "
        "being written."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "note_path": {"type": "string", "description": "The follow-along note md, when the output folder was moved."},
            "api_base": {"type": "string"},
        },
    },
)
def write_review_note(
    task_id: str | None = None, note_path: str | None = None, api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    return _agent_request(
        "POST", "/agent/v1/review-notes",
        api_base=api_base, client_id=client_id, payload={"task_id": task_id, "note_path": note_path}, timeout=2700,
    )


@tool(
    description=("Change an existing review note as the user asked, without writing it again from the "
        "follow-along note: pass the user's feedback as instruction (what to change and what to leave). "
        "The rest of the note, its sources, question boxes and term list stay as they are; the links back to "
        "the follow-along note are kept exactly. The previous version goes to 过程文件/历史版本 and the PDF "
        "is printed again. Give task_id, or note_path (the review note or the follow-along note beside it). "
        "Takes a few minutes. Refused with code review_note_failed and the reason when there is no review "
        "note yet or the answer was incomplete (nothing is written). unknown_sources lists citations the "
        "model got wrong (written as （出处待核）); lost_links lists old links it dropped."),
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "note_path": {"type": "string"},
            "instruction": {"type": "string"},
            "api_base": {"type": "string"},
        },
        "required": ["instruction"],
    },
)
def revise_review_note(
    instruction: str, task_id: str | None = None, note_path: str | None = None,
    api_base: str | None = None, client_id: str | None = None,
) -> dict[str, Any]:
    return _agent_request(
        "POST", "/agent/v1/review-notes/revise",
        api_base=api_base, client_id=client_id, timeout=1800,
        payload={"task_id": task_id, "note_path": note_path, "instruction": instruction},
    )


# Every tool, in the order written: each is described where it is defined (``tool``).
TOOL_FUNCTIONS: dict[str, Any] = {name: func for name, (func, _definition) in _TOOLS.items()}


TOOL_DEFINITIONS: list[dict[str, Any]] = [definition for _func, definition in _TOOLS.values()]


def _result(payload: Any) -> dict[str, Any]:
    # The JSON once, as text. No tool declares an outputSchema, so
    # structuredContent is optional, and sending it too doubled every answer:
    # Codex showed both copies and cut the note material off at its display
    # limit (2026-10-16), so it wrote the note in two parts it did not need.
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


def _call_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    func = TOOL_FUNCTIONS.get(name)
    if func is None:
        raise KeyError(f"Unknown tool: {name}")
    args = arguments if isinstance(arguments, dict) else {}
    signature = inspect.signature(func)
    accepted = {key: value for key, value in args.items() if key in signature.parameters}
    payload = func(**accepted)
    result = _result(payload)
    if isinstance(payload, dict) and payload.get("ok") is False:
        result["isError"] = True
    return result


def _success(message_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": message_id, "error": error}


def handle_jsonrpc_message(message: dict[str, Any]) -> dict[str, Any] | None:
    message_id = message.get("id")
    method = str(message.get("method") or "")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    is_notification = "id" not in message

    if is_notification:
        return None
    if method == "initialize":
        return _success(
            message_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": "Use FluentFlow tools to submit video or transcript tasks, wait for them, inspect task packages, diagnose failures, regenerate notes, and export results.",
            },
        )
    if method == "server/discover":
        return _success(
            message_id,
            {"supportedVersions": [PROTOCOL_VERSION], "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO},
        )
    if method == "ping":
        return _success(message_id, {})
    if method == "tools/list":
        return _success(message_id, {"tools": TOOL_DEFINITIONS})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            return _success(message_id, _call_tool(name, arguments))
        except KeyError as exc:
            return _error(message_id, -32602, str(exc))
        except TypeError as exc:
            return _error(message_id, -32602, f"Invalid tool arguments: {exc}")
        except Exception as exc:
            return _error(message_id, -32603, f"Tool call failed: {exc}")
    return _error(message_id, -32601, f"Method not found: {method}")


def run_stdio() -> None:
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, f"Parse error: {exc}")
        else:
            if not isinstance(message, dict):
                response = _error(None, -32600, "Invalid request")
            else:
                response = handle_jsonrpc_message(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


def main() -> int:
    global _heartbeat_enabled
    _heartbeat_enabled = True
    run_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
