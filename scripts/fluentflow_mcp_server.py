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


SERVER_INFO = {"name": "fluentflow", "version": _app_version()}


def _client_id(value: str | None = None) -> str:
    return (value or os.environ.get("FLUENTFLOW_CLIENT_ID") or DEFAULT_CLIENT_ID).strip() or DEFAULT_CLIENT_ID


def _agent_request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    api_base: str | None = None,
    client_id: str | None = None,
    timeout: float = 60,
) -> dict[str, Any]:
    try:
        return api_request(
            method,
            normalize_api_base(api_base),
            path,
            payload=payload,
            client_id=_client_id(client_id),
            access_token=os.environ.get("FLUENTFLOW_ACCESS_TOKEN"),
            timeout=timeout,
        )
    except FluentFlowApiError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "status": exc.status,
            "payload": exc.payload if isinstance(exc.payload, dict) else None,
        }


def _options(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in (None, "")}


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
    "regenerate_note": regenerate_note,
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
