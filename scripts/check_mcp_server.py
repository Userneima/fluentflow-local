#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = PROJECT_ROOT / "scripts" / "fluentflow_mcp_server.py"
EXPECTED_TOOLS = {
    "submit_video_link",
    "submit_transcript",
    "get_task",
    "wait_task",
    "get_task_package",
    "diagnose_task",
    "regenerate_note",
    "export_result",
}


class McpCheckError(RuntimeError):
    pass


class McpStdioClient:
    def __init__(self, *, api_base: str, client_id: str, access_token: str | None = None) -> None:
        env = os.environ.copy()
        env["FLUENTFLOW_API_BASE"] = api_base
        env["FLUENTFLOW_CLIENT_ID"] = client_id
        if access_token:
            env["FLUENTFLOW_ACCESS_TOKEN"] = access_token
        self.process = subprocess.Popen(
            [sys.executable, str(SERVER_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._next_id = 1

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.process.stdin or not self.process.stdout:
            raise McpCheckError("MCP server pipes are not available")
        message_id = self._next_id
        self._next_id += 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params or {}}, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise McpCheckError(f"MCP server exited without response. stderr={stderr.strip()}")
        response = json.loads(line)
        if response.get("id") != message_id:
            raise McpCheckError(f"Unexpected response id: {response}")
        if response.get("error"):
            raise McpCheckError(f"MCP error: {response['error']}")
        return response["result"]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise McpCheckError(f"Tool {name} returned error: {result.get('structuredContent') or result}")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise McpCheckError(f"Tool {name} returned no structured content: {result}")
        return structured


def run_check(*, api_base: str, client_id: str, access_token: str | None, backend_e2e: bool) -> dict[str, Any]:
    client = McpStdioClient(api_base=api_base, client_id=client_id, access_token=access_token)
    try:
        initialize = client.request("initialize")
        tools_result = client.request("tools/list")
        tool_names = {tool.get("name") for tool in tools_result.get("tools", []) if isinstance(tool, dict)}
        missing = EXPECTED_TOOLS - tool_names
        if missing:
            raise McpCheckError(f"Missing MCP tools: {sorted(missing)}")
        payload: dict[str, Any] = {
            "ok": True,
            "protocol_version": initialize.get("protocolVersion"),
            "server": initialize.get("serverInfo"),
            "tool_count": len(tool_names),
            "backend_e2e": None,
        }
        if backend_e2e:
            submitted = client.call_tool(
                "submit_transcript",
                {
                    "title": "MCP smoke transcript",
                    "transcript_text": "FluentFlow MCP smoke test transcript. This task intentionally skips summary.",
                    "skip_summary": True,
                    "api_base": api_base,
                    "client_id": client_id,
                },
            )
            task_id = str(submitted.get("task_id") or "")
            if not task_id:
                raise McpCheckError(f"submit_transcript returned no task_id: {submitted}")
            waited = client.call_tool("wait_task", {"task_id": task_id, "timeout_seconds": 0, "api_base": api_base, "client_id": client_id})
            package = client.call_tool("get_task_package", {"task_id": task_id, "api_base": api_base, "client_id": client_id})
            diagnosis = client.call_tool("diagnose_task", {"task_id": task_id, "api_base": api_base, "client_id": client_id})
            transcript = package.get("transcript") if isinstance(package.get("transcript"), dict) else {}
            if "smoke test transcript" not in str(transcript.get("text") or ""):
                raise McpCheckError("Agent Task Package did not include the submitted transcript text")
            payload["backend_e2e"] = {
                "task_id": task_id,
                "status": package.get("task", {}).get("status") if isinstance(package.get("task"), dict) else None,
                "wait_done": waited.get("done"),
                "diagnosis": diagnosis.get("note"),
            }
        return payload
    finally:
        client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check FluentFlow MCP stdio server and optional backend task flow.")
    parser.add_argument("--api-base", default=os.environ.get("FLUENTFLOW_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--client-id", default=os.environ.get("FLUENTFLOW_CLIENT_ID", "local-client"))
    parser.add_argument("--access-token", default=os.environ.get("FLUENTFLOW_ACCESS_TOKEN"))
    parser.add_argument("--backend-e2e", action="store_true", help="Submit a transcript through MCP and read the task package from the backend.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = run_check(api_base=args.api_base, client_id=args.client_id, access_token=args.access_token, backend_e2e=args.backend_e2e)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
