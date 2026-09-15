#!/usr/bin/env python3
from __future__ import annotations

import os
import inspect
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.local_agent_client import (
    DEFAULT_API_BASE,
    DEFAULT_CLIENT_ID,
    FluentFlowApiError,
    api_request,
    normalize_api_base,
)

PROTOCOL_VERSION = "2025-06-18"


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
        return {
            "ok": False,
            "error": str(exc),
            "status": exc.status,
            "payload": exc.payload if isinstance(exc.payload, dict) else None,
        }


def _options(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in (None, "")}


def submit_local_media(
    path: str,
    title: str | None = None,
    skip_summary: bool = False,
    note_mode: str | None = None,
    prompt_preset: str | None = None,
    stt_model: str | None = None,
    speaker_diarization: bool = True,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Submit one recording that stays where it is, by absolute path on this machine.

    Local edition only, and the backend refuses it from anywhere but localhost. One
    file per call on purpose: a task has one id, and a call that queued five of them
    would have nothing to return and nothing to point at when one failed. For a whole
    folder, loop over this call.
    """
    refusal = _require_local_edition(normalize_api_base(api_base))
    if refusal is not None:
        return refusal
    return _agent_request(
        "POST",
        "/agent/v1/tasks",
        api_base=api_base,
        client_id=client_id,
        payload={
            "path": path,
            "input_type": "local_path",
            "title": title,
            "options": _options(
                skip_summary="true" if skip_summary else "false",
                note_mode=note_mode,
                prompt_preset=prompt_preset,
                stt_model=stt_model,
                speaker_diarization="true" if speaker_diarization else "false",
            ),
        },
    )


def submit_video_link(
    input_text: str,
    title: str | None = None,
    stt_provider: str = "auto",
    skip_summary: bool = False,
    note_mode: str | None = None,
    prompt_preset: str | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Submit a video/share link to FluentFlow and return the created task."""
    return _agent_request(
        "POST",
        "/agent/v1/tasks",
        api_base=api_base,
        client_id=client_id,
        payload={
            "input": input_text,
            "input_type": "video_link",
            "title": title,
            "options": _options(
                stt_provider=stt_provider,
                skip_summary="true" if skip_summary else "false",
                note_mode=note_mode,
                prompt_preset=prompt_preset,
            ),
        },
        timeout=30,
    )


def submit_transcript(
    transcript_text: str,
    title: str = "Transcript",
    skip_summary: bool = False,
    note_mode: str | None = None,
    prompt_preset: str | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Submit transcript text directly and optionally generate a note."""
    return _agent_request(
        "POST",
        "/agent/v1/tasks",
        api_base=api_base,
        client_id=client_id,
        payload={
            "input_type": "transcript",
            "transcript_text": transcript_text,
            "title": title,
            "options": _options(
                skip_summary="true" if skip_summary else "false",
                note_mode=note_mode,
                prompt_preset=prompt_preset,
            ),
        },
        timeout=120,
    )


def get_task(
    task_id: str,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Read lightweight task status from FluentFlow."""
    return _agent_request("GET", f"/agent/v1/tasks/{task_id}", api_base=api_base, client_id=client_id)


def wait_task(
    task_id: str,
    timeout_seconds: float = 30,
    poll_interval_seconds: float = 2,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Wait for a task to finish or return the current running state."""
    return _agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/wait",
        api_base=api_base,
        client_id=client_id,
        payload={"timeout_seconds": timeout_seconds, "poll_interval_seconds": poll_interval_seconds},
        timeout=max(5, min(float(timeout_seconds or 30) + 10, 75)),
    )


def get_task_package(
    task_id: str,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Read the stable Agent Task Package for a FluentFlow task."""
    return _agent_request("GET", f"/agent/v1/tasks/{task_id}/package", api_base=api_base, client_id=client_id)


def diagnose_task(
    task_id: str,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Explain note generation or task failure state in a machine-readable form."""
    return _agent_request("GET", f"/agent/v1/tasks/{task_id}/diagnosis", api_base=api_base, client_id=client_id)


def retry_task(
    task_id: str,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Retry a failed task when FluentFlow still retains its source media."""
    return _agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/retry",
        api_base=api_base,
        client_id=client_id,
        timeout=30,
    )


def regenerate_note(
    task_id: str,
    note_mode: str = "auto",
    prompt_preset: str | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Regenerate a task note from the stored transcript."""
    return _agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/note/regenerate",
        api_base=api_base,
        client_id=client_id,
        payload=_options(note_mode=note_mode, prompt_preset=prompt_preset),
        timeout=180,
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
    """Remove silent gaps from a completed task's source media, mechanically.

    A distinct action rather than a package field: it starts work, and it takes
    parameters the caller has to choose. Returns as soon as the work is accepted —
    rendering takes minutes, so poll ``get_task_package`` and read its
    ``debreath`` block.

    ``render=False`` produces only the cut list, which is the cheap way to see how
    much would be removed and to check the ``warnings`` before spending an encode.
    """
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


def write_note_from_cut_media(
    task_id: str,
    preview: bool = True,
    replace_note: bool = True,
    restore_previous_note: bool = False,
    use_generated_note: bool = False,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Write a task's note from its de-breathed media, after ``debreath_task``.

    The second half of one flow: ``debreath_task`` produces the shortened file
    and the cut list, and this writes the note from that file — frames taken from
    it, subtitles moved onto its clock. It refuses if no cut file exists yet
    rather than reading the original recording, so call ``debreath_task`` with
    ``render=True`` first.

    ``preview`` defaults to **true** and costs nothing: it answers which file
    would be read, how much transcript after remapping, how many frames, and
    whose Claude allowance pays. Pass ``preview=False`` to actually run it — that
    spends the machine owner's Claude allowance, so do not do it unasked.

    By default the finished note becomes the task's note and the previous one is
    kept and restorable. ``replace_note=False`` leaves the task's note alone;
    ``restore_previous_note`` and ``use_generated_note`` switch between the two
    without a model call. Poll ``get_task_package`` and read its
    ``cut_media_note`` block: ``basis`` there is measured from the note's own
    citations, so ``transcript_only`` means nothing was written from a picture.
    """
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
    return _agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/visual-note",
        api_base=api_base,
        client_id=client_id,
        payload=payload,
        timeout=60,
    )


def export_result(
    task_id: str,
    target: str = "lark",
    title: str | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    """Export a completed task note to a supported target such as Lark."""
    return _agent_request(
        "POST",
        f"/agent/v1/tasks/{task_id}/exports",
        api_base=api_base,
        client_id=client_id,
        payload=_options(target=target, title=title),
        timeout=120,
    )


TOOL_FUNCTIONS = {
    "submit_video_link": submit_video_link,
    "submit_transcript": submit_transcript,
    "get_task": get_task,
    "wait_task": wait_task,
    "get_task_package": get_task_package,
    "diagnose_task": diagnose_task,
    "retry_task": retry_task,
    "submit_local_media": submit_local_media,
    "regenerate_note": regenerate_note,
    "debreath_task": debreath_task,
    "write_note_from_cut_media": write_note_from_cut_media,
    "export_result": export_result,
}


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "submit_video_link",
        "description": "Submit a video URL or copied share text to FluentFlow.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "input_text": {"type": "string", "description": "Video URL or copied share text."},
                "title": {"type": "string"},
                "stt_provider": {"type": "string", "default": "auto"},
                "skip_summary": {"type": "boolean", "default": False},
                "note_mode": {"type": "string"},
                "prompt_preset": {"type": "string"},
                "api_base": {"type": "string"},
                "client_id": {"type": "string"},
            },
            "required": ["input_text"],
        },
    },
    {
        "name": "submit_transcript",
        "description": "Submit transcript text directly and optionally generate a note.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "transcript_text": {"type": "string"},
                "title": {"type": "string", "default": "Transcript"},
                "skip_summary": {"type": "boolean", "default": False},
                "note_mode": {"type": "string"},
                "prompt_preset": {"type": "string"},
                "api_base": {"type": "string"},
                "client_id": {"type": "string"},
            },
            "required": ["transcript_text"],
        },
    },
    {
        "name": "submit_local_media",
        "description": (
            "Submit one audio or video file that stays where it is, by absolute path "
            "on this machine. Local edition only; the backend accepts it from localhost "
            "only, and this tool says so plainly when the other edition is the one "
            "answering. One file per call — loop for a folder."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the recording."},
                "title": {"type": "string"},
                "skip_summary": {"type": "boolean", "default": False},
                "note_mode": {"type": "string"},
                "prompt_preset": {"type": "string"},
                "stt_model": {"type": "string"},
                "api_base": {"type": "string"},
                "client_id": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_task",
        "description": "Read lightweight task status from FluentFlow.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "api_base": {"type": "string"}, "client_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "wait_task",
        "description": "Wait for a task to finish or return the current running state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "timeout_seconds": {"type": "number", "default": 30},
                "poll_interval_seconds": {"type": "number", "default": 2},
                "api_base": {"type": "string"},
                "client_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "get_task_package",
        "description": "Read the stable Agent Task Package for a FluentFlow task.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "api_base": {"type": "string"}, "client_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "diagnose_task",
        "description": "Explain task or note generation failure state in a machine-readable form.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "api_base": {"type": "string"}, "client_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "retry_task",
        "description": "Retry a failed task from its retained source media when available.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "api_base": {"type": "string"}, "client_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "regenerate_note",
        "description": "Regenerate a task note from the stored transcript.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "note_mode": {"type": "string", "default": "auto"},
                "prompt_preset": {"type": "string"},
                "api_base": {"type": "string"},
                "client_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "debreath_task",
        "description": (
            "Mechanically remove silent gaps from a completed task's source media. "
            "Acoustic detection only — no model decides which pause matters. Writes a cut "
            "list artifact always and a rendered file when render is true; poll "
            "get_task_package and read its debreath block for progress, counts, and warnings."
        ),
        "inputSchema": {
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
                    "description": "Silence threshold in dBFS, -90 to 0. Default -30.",
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
                "client_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "write_note_from_cut_media",
        "description": (
            "Write a task's note from its de-breathed media — the second half of the flow "
            "that starts with debreath_task(render=True). Frames come from the shortened "
            "file and the subtitles are moved onto its clock; with no cut file it refuses "
            "instead of reading the original recording. preview defaults to true and is "
            "free (which file, how much transcript, how many frames, whose Claude "
            "allowance pays); preview=false spends that allowance, so ask first. The note "
            "becomes the task's note by default, keeping the previous one restorable."
        ),
        "inputSchema": {
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
                "api_base": {"type": "string"},
                "client_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "export_result",
        "description": "Export a completed task note to a supported target such as Lark.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "target": {"type": "string", "default": "lark"},
                "title": {"type": "string"},
                "api_base": {"type": "string"},
                "client_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
]


def _result(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "structuredContent": payload}


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
    run_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
