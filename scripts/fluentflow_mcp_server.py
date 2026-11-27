#!/usr/bin/env python3
"""FluentFlow Local's MCP server: the entry point agents run over stdio.

The tools live in ``scripts/fluentflow_mcp/`` (one module per group, the
connection and hold machinery in ``core``); this file loads them in order and
answers JSON-RPC.
"""
from __future__ import annotations

import inspect
import json
import sys
import threading
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fluentflow_mcp import core
from scripts.fluentflow_mcp.core import _TOOLS, SERVER_INFO

# Imported for the tools they register, in this order: it is the order of
# TOOL_FUNCTIONS and of tools/list.
from scripts.fluentflow_mcp import (  # noqa: F401
    submit,
    tasks,
    cut_report,
    note_output,
    agent_material,
    notes,
    corrections,
    review_notes,
)

PROTOCOL_VERSION = "2025-06-18"


# Every tool, in the order written: each is described where it is defined (``tool``).
TOOL_FUNCTIONS: dict[str, Any] = {name: func for name, (func, _definition) in _TOOLS.items()}


TOOL_DEFINITIONS: list[dict[str, Any]] = [definition for _func, definition in _TOOLS.values()]

# Each tool is also a name here (``fluentflow_mcp_server.get_task``), as before the split.
globals().update(TOOL_FUNCTIONS)


def _result(payload: Any) -> dict[str, Any]:
    # The JSON once, as text. No tool declares an outputSchema, so
    # structuredContent is optional, and sending it too doubled every answer:
    # Codex showed both copies and cut the note material off at its display
    # limit (2026-10-16), so it wrote the note in two parts it did not need.
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


def _for_agent(value: Any) -> Any:
    """The backend's answers name the package by its HTTP path, for scripts;
    an agent reads it with a tool, so say that instead."""
    if isinstance(value, dict):
        return {
            ("package" if key == "package_url" else key): (
                "get_task_package" if key == "package_url" else _for_agent(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_for_agent(item) for item in value]
    return value


def _bad_argument(name: str, func: Any, args: dict[str, Any]) -> dict[str, Any] | None:
    """An integer or boolean argument the agent sent as something else, said
    plainly instead of a Python traceback from deep in the call."""
    schema = (_TOOLS.get(name, (None, {}))[1].get("inputSchema") or {}).get("properties") or {}
    for key, value in args.items():
        kind = (schema.get(key) or {}).get("type")
        if value is None:
            continue
        if kind == "integer" and not (isinstance(value, int) and not isinstance(value, bool)):
            if isinstance(value, str) and value.strip().lstrip("-").isdigit():
                args[key] = int(value)
                continue
            return {"ok": False, "code": "bad_argument", "error": f"{key} 要传整数，收到的是 {value!r}。"}
        if kind == "boolean" and not isinstance(value, bool):
            return {"ok": False, "code": "bad_argument", "error": f"{key} 要传 true 或 false，收到的是 {value!r}。"}
    return None


def _call_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    func = TOOL_FUNCTIONS.get(name)
    if func is None:
        raise KeyError(f"Unknown tool: {name}")
    args = arguments if isinstance(arguments, dict) else {}
    signature = inspect.signature(func)
    accepted = {key: value for key, value in args.items() if key in signature.parameters}
    payload = _bad_argument(name, func, accepted) or _for_agent(func(**accepted))
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


_write_lock = threading.Lock()


def _respond(response: dict[str, Any] | None, out=None) -> None:
    if response is None:
        return
    out = out or sys.stdout
    with _write_lock:
        out.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        out.flush()


def run_stdio(stdin=None, stdout=None) -> list[threading.Thread]:
    """Answer each tool call on its own thread. One conversation, its
    subagents included, shares this process: answered one at a time, a
    subagent's call to fetch the cut material waited behind the main agent's
    ten-minute wait_task, and the task was given away as never fetched
    (2026-10-28). Returns the threads still answering, for tests."""
    calls: list[threading.Thread] = []
    for line in stdin or sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except json.JSONDecodeError as exc:
            _respond(_error(None, -32700, f"Parse error: {exc}"), stdout)
            continue
        if not isinstance(message, dict):
            _respond(_error(None, -32600, "Invalid request"), stdout)
            continue
        if message.get("method") == "tools/call" and "id" in message:
            call = threading.Thread(
                target=lambda m=message: _respond(handle_jsonrpc_message(m), stdout),
                name="fluentflow-tool-call", daemon=True,
            )
            call.start()
            calls = [thread for thread in calls if thread.is_alive()] + [call]
            continue
        _respond(handle_jsonrpc_message(message), stdout)
    return [thread for thread in calls if thread.is_alive()]


def _use_utf8_stdio() -> None:
    """MCP messages are UTF-8 JSON. Claude Code and Codex start this process
    over pipes without Python's UTF-8 mode, so on a Chinese Windows its stdin
    and stdout used the ANSI code page, GBK: Chinese in replies reached the
    agent as GBK bytes, an emoji in a note raised inside the call and left it
    unanswered, and Chinese arguments (a passage to replace) were misread."""
    for stream, options in (
        (sys.stdin, {"encoding": "utf-8"}),
        (sys.stdout, {"encoding": "utf-8", "newline": "\n"}),
        (sys.stderr, {"encoding": "utf-8", "errors": "backslashreplace"}),
    ):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(**options)


_CLI_USAGE = """用法：
  fluentflow_mcp_server.py                     作为 MCP 服务跑（agent 注册的就是这个）
  fluentflow_mcp_server.py tools               列出全部工具和一句说明
  fluentflow_mcp_server.py tools <工具名>      打印这个工具的完整说明和参数
  fluentflow_mcp_server.py call <工具名> ['<JSON 参数>' | -]
                                               调一次工具，打印它的回答（JSON）；- 表示参数从标准输入读
"""


def run_cli(argv: list[str], out=None) -> int:
    """The same tools from the command line, for an agent whose conversation
    started before the tools were registered: Claude Code and Codex load MCP
    servers only when a conversation starts, so the conversation that ran the
    install cannot call them, but it can run this (2026-11-27, onboarding).
    Exit 0 when the tool answered, 1 when it answered ok=false, 2 on a usage
    error."""
    out = out or sys.stdout
    command, rest = argv[0], argv[1:]
    if command == "tools":
        if not rest:
            for definition in TOOL_DEFINITIONS:
                summary = str(definition.get("description") or "").strip().split(". ")[0]
                out.write(f"{definition['name']}: {summary[:160]}\n")
            return 0
        definition = next((item for item in TOOL_DEFINITIONS if item["name"] == rest[0]), None)
        if definition is None:
            sys.stderr.write(f"没有叫 {rest[0]} 的工具。运行 tools 看全部工具。\n")
            return 2
        out.write(json.dumps(definition, ensure_ascii=False, indent=2) + "\n")
        return 0
    if command != "call" or not rest or len(rest) > 2:
        sys.stderr.write(_CLI_USAGE)
        return 2
    name = rest[0]
    if name not in TOOL_FUNCTIONS:
        sys.stderr.write(f"没有叫 {name} 的工具。运行 tools 看全部工具。\n")
        return 2
    raw = (sys.stdin.read() if rest[1:] == ["-"] else rest[1]) if len(rest) == 2 else "{}"
    try:
        arguments = json.loads(raw or "{}")
    except ValueError as exc:
        sys.stderr.write(f"参数不是合法的 JSON：{exc}\n")
        return 2
    if not isinstance(arguments, dict):
        sys.stderr.write("参数要是一个 JSON 对象，例如 '{\"task_id\": \"abc\"}'。\n")
        return 2
    try:
        result = _call_tool(name, arguments)
    except TypeError as exc:
        sys.stderr.write(f"参数不对：{exc}\n")
        return 2
    out.write(result["content"][0]["text"] + "\n")
    return 1 if result.get("isError") else 0


def main() -> int:
    if len(sys.argv) > 1:
        _use_utf8_stdio()
        return run_cli(sys.argv[1:])
    core._heartbeat_enabled = True
    _use_utf8_stdio()
    # The host closing stdin ends the conversation: exit without finishing
    # the calls still out, or their heartbeat would outlive it.
    run_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
