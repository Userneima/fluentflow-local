#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.check_mcp_server import McpStdioClient


SAFE_VIDEO_URL = "https://youtu.be/fluentflow-eval-offline"
SAFE_UNAVAILABLE_VIDEO_URL = "https://youtu.be/fluentflow-eval-unavailable"
SAFE_TRANSCRIPT = (
    "FluentFlow Agent MCP backend eval transcript. "
    "This synthetic text is short and contains no user content."
)


@dataclass(frozen=True)
class EvalCase:
    name: str
    mode: str
    description: str
    runner: Callable[["EvalClient"], dict[str, Any]]


class EvalClient:
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        return None


class StdioEvalClient(EvalClient):
    def __init__(self, *, api_base: str, client_id: str, access_token: str | None = None) -> None:
        self._client = McpStdioClient(api_base=api_base, client_id=client_id, access_token=access_token)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._client.call_tool(name, arguments)

    def close(self) -> None:
        self._client.close()


class MockEvalClient(EvalClient):
    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "submit_video_link":
            input_text = str(arguments.get("input_text") or "")
            failed = "unavailable" in input_text
            task_id = "eval-failure" if failed else "eval-success"
            package = _mock_package(task_id, failed=failed)
            self.tasks[task_id] = package
            return {"ok": True, "task_id": task_id, "status": package["task"]["status"]}
        if name == "submit_transcript":
            task_id = "eval-transcript"
            package = _mock_package(task_id, failed=False, source_type="transcript")
            package["note"]["status"] = "completed"
            package["note"]["diagnosis"] = {"code": "note_completed", "retryable": False}
            self.tasks[task_id] = package
            return {"ok": True, "task_id": task_id, "status": "completed"}
        task_id = str(arguments.get("task_id") or "")
        package = self.tasks.get(task_id)
        if package is None:
            return {"ok": False, "status": 404, "error": "Task not found"}
        if name == "wait_task":
            return {"ok": True, "done": package["task"]["status"] in {"completed", "failed"}, "package": package}
        if name == "get_task_package":
            return package
        if name == "diagnose_task":
            return {"ok": True, "task_id": task_id, "diagnosis": package["note"]["diagnosis"]}
        if name == "regenerate_note":
            package = _replace_note(package, status="completed", code="note_completed", retryable=False)
            self.tasks[task_id] = package
            return {"ok": True, "task_id": task_id, "package": package}
        if name == "export_result":
            return {"ok": True, "task_id": task_id, "export": {"target": arguments.get("target") or "lark", "status": "created", "url": "mock://export/eval-success"}}
        return {"ok": False, "error": f"Unknown mock tool: {name}"}


def _mock_package(task_id: str, *, failed: bool, source_type: str = "video_link") -> dict[str, Any]:
    status = "failed" if failed else "completed"
    diagnosis = (
        {"code": "video_source_unavailable", "retryable": True, "note": "Synthetic video source failed before transcription."}
        if failed
        else {"code": "transcript_only_mode", "retryable": True, "note": "Transcript exists and note can be regenerated."}
    )
    return {
        "ok": True,
        "agent_task_package_version": "1",
        "task": {"task_id": task_id, "status": status, "stage": "failed" if failed else "done"},
        "source": {
            "type": source_type,
            "video_source": {"provider": "mock-youtube", "url_present": source_type == "video_link"} if source_type == "video_link" else None,
        },
        "transcript": {"available": not failed, "text": "redacted synthetic transcript" if not failed else ""},
        "note": {"status": "failed" if failed else "skipped", "diagnosis": diagnosis},
        "artifacts": {},
        "next_actions": [{"action": "regenerate_note"}] if not failed else [{"action": "diagnose_task"}],
    }


def _replace_note(package: dict[str, Any], *, status: str, code: str, retryable: bool) -> dict[str, Any]:
    updated = dict(package)
    note = dict(updated.get("note") or {})
    note["status"] = status
    note["diagnosis"] = {"code": code, "retryable": retryable}
    updated["note"] = note
    updated["next_actions"] = []
    return updated


def load_eval_cases(mode: str = "mock") -> list[EvalCase]:
    cases = [
        EvalCase(
            name="video_link_success_note_export",
            mode="mock",
            description="Submit a synthetic video link, wait, inspect package, regenerate note, and export.",
            runner=run_video_link_success_note_export,
        ),
        EvalCase(
            name="video_link_failure_diagnosis",
            mode="mock",
            description="Submit a synthetic unavailable video link and record diagnosis instead of crashing.",
            runner=run_video_link_failure_diagnosis,
        ),
        EvalCase(
            name="backend_transcript_package_diagnosis",
            mode="backend-e2e",
            description="Submit a short synthetic transcript through MCP and inspect package plus diagnosis.",
            runner=run_backend_transcript_package_diagnosis,
        ),
    ]
    return [case for case in cases if case.mode == mode]


def run_video_link_success_note_export(client: EvalClient) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    submitted = _record_tool(calls, client, "submit_video_link", {"input_text": SAFE_VIDEO_URL, "title": "Agent MCP eval video", "skip_summary": True})
    task_id = str(submitted.get("task_id") or "")
    _record_tool(calls, client, "wait_task", {"task_id": task_id, "timeout_seconds": 0})
    package = _record_tool(calls, client, "get_task_package", {"task_id": task_id})
    diagnosis = _extract_diagnosis(package)
    regenerated = _record_tool(calls, client, "regenerate_note", {"task_id": task_id, "note_mode": "auto"})
    export = _record_tool(calls, client, "export_result", {"task_id": task_id, "target": "lark", "title": "Agent MCP eval export"})
    final_package = regenerated.get("package") if isinstance(regenerated.get("package"), dict) else package
    ok = bool(task_id) and _tool_calls_ok(calls) and _package_note_status(final_package) == "completed" and (export.get("ok") is not False)
    return {
        "status": "passed" if ok else "failed",
        "tool_calls": calls,
        "task_id": task_id or None,
        "diagnosis": _summarize_diagnosis(diagnosis),
        "package": _summarize_package(final_package),
        "export": _summarize_export(export),
        "error": None if ok else "Success path did not produce a completed note and export summary.",
    }


def run_video_link_failure_diagnosis(client: EvalClient) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    submitted = _record_tool(calls, client, "submit_video_link", {"input_text": SAFE_UNAVAILABLE_VIDEO_URL, "title": "Unavailable eval video"})
    task_id = str(submitted.get("task_id") or "")
    _record_tool(calls, client, "wait_task", {"task_id": task_id, "timeout_seconds": 0})
    diagnosis_payload = _record_tool(calls, client, "diagnose_task", {"task_id": task_id})
    package = _record_tool(calls, client, "get_task_package", {"task_id": task_id})
    diagnosis = diagnosis_payload.get("diagnosis") if isinstance(diagnosis_payload.get("diagnosis"), dict) else _extract_diagnosis(package)
    ok = bool(task_id) and bool(diagnosis) and _summarize_package(package).get("task_status") == "failed"
    return {
        "status": "passed" if ok else "failed",
        "tool_calls": calls,
        "task_id": task_id or None,
        "diagnosis": _summarize_diagnosis(diagnosis),
        "package": _summarize_package(package),
        "export": None,
        "error": None if ok else "Failure path did not produce a diagnosis summary.",
    }


def run_backend_transcript_package_diagnosis(client: EvalClient) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    submitted = _record_tool(calls, client, "submit_transcript", {"title": "Agent MCP backend eval transcript", "transcript_text": SAFE_TRANSCRIPT, "skip_summary": True})
    task_id = str(submitted.get("task_id") or "")
    _record_tool(calls, client, "wait_task", {"task_id": task_id, "timeout_seconds": 0})
    package = _record_tool(calls, client, "get_task_package", {"task_id": task_id})
    diagnosis_payload = _record_tool(calls, client, "diagnose_task", {"task_id": task_id})
    diagnosis = diagnosis_payload.get("diagnosis") if isinstance(diagnosis_payload.get("diagnosis"), dict) else _extract_diagnosis(package)
    ok = bool(task_id) and _tool_calls_ok(calls) and bool(_summarize_package(package).get("task_status"))
    return {
        "status": "passed" if ok else "failed",
        "tool_calls": calls,
        "task_id": task_id or None,
        "diagnosis": _summarize_diagnosis(diagnosis),
        "package": _summarize_package(package),
        "export": None,
        "error": None if ok else "Backend e2e did not return task package status.",
    }


def _record_tool(calls: list[dict[str, Any]], client: EvalClient, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        payload = client.call_tool(name, arguments)
        error = payload.get("error") if isinstance(payload, dict) else None
        status = "error" if isinstance(payload, dict) and payload.get("ok") is False else "ok"
    except Exception as exc:  # Eval must record tool failures instead of hiding prior calls.
        payload = {"ok": False, "error": str(exc)}
        error = str(exc)
        status = "error"
    elapsed = round(time.perf_counter() - started, 4)
    calls.append({"name": name, "status": status, "elapsed_seconds": elapsed, "error": str(error) if error else None, "summary": _summarize_payload(name, payload)})
    return payload if isinstance(payload, dict) else {"ok": False, "error": "Tool returned non-object payload."}


def run_eval(mode: str = "mock", *, api_base: str = "http://127.0.0.1:8000", client_id: str = "local-client", access_token: str | None = None) -> dict[str, Any]:
    cases = load_eval_cases(mode)
    if not cases:
        raise ValueError(f"No eval cases for mode: {mode}")
    client: EvalClient = MockEvalClient() if mode == "mock" else StdioEvalClient(api_base=api_base, client_id=client_id, access_token=access_token)
    try:
        results = [_run_case(case, client) for case in cases]
    finally:
        client.close()
    passed = sum(1 for result in results if result["status"] == "passed")
    failed = len(results) - passed
    return {"ok": failed == 0, "mode": mode, "summary": {"total": len(results), "passed": passed, "failed": failed}, "cases": results}


def _run_case(case: EvalCase, client: EvalClient) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = case.runner(client)
    except Exception as exc:
        result = {"status": "failed", "tool_calls": [], "task_id": None, "diagnosis": None, "package": None, "export": None, "error": str(exc)}
    result["name"] = case.name
    result["description"] = case.description
    result["elapsed_seconds"] = round(time.perf_counter() - started, 4)
    return result


def _tool_calls_ok(calls: list[dict[str, Any]]) -> bool:
    return bool(calls) and all(call.get("status") == "ok" for call in calls)


def _package_note_status(package: dict[str, Any]) -> str | None:
    note = package.get("note") if isinstance(package.get("note"), dict) else {}
    status = note.get("status")
    return str(status) if status is not None else None


def _extract_diagnosis(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("diagnosis"), dict):
        return payload["diagnosis"]
    note = payload.get("note") if isinstance(payload.get("note"), dict) else {}
    diagnosis = note.get("diagnosis") if isinstance(note.get("diagnosis"), dict) else {}
    return diagnosis


def _summarize_payload(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"ok": payload.get("ok")}
    if payload.get("task_id"):
        summary["task_id"] = payload.get("task_id")
    if payload.get("status"):
        summary["status"] = payload.get("status")
    if payload.get("done") is not None:
        summary["done"] = payload.get("done")
    if isinstance(payload.get("diagnosis"), dict):
        summary["diagnosis"] = _summarize_diagnosis(payload["diagnosis"])
    package = payload.get("package") if isinstance(payload.get("package"), dict) else payload if name == "get_task_package" else None
    if isinstance(package, dict):
        summary["package"] = _summarize_package(package)
    if isinstance(payload.get("export"), dict):
        summary["export"] = _summarize_export(payload)
    return summary


def _summarize_package(package: dict[str, Any]) -> dict[str, Any]:
    task = package.get("task") if isinstance(package.get("task"), dict) else {}
    source = package.get("source") if isinstance(package.get("source"), dict) else {}
    transcript = package.get("transcript") if isinstance(package.get("transcript"), dict) else {}
    note = package.get("note") if isinstance(package.get("note"), dict) else {}
    artifacts = package.get("artifacts") if isinstance(package.get("artifacts"), dict) else {}
    next_actions = package.get("next_actions") if isinstance(package.get("next_actions"), list) else []
    return {
        "task_status": task.get("status"),
        "task_stage": task.get("stage"),
        "source_type": source.get("type"),
        "transcript_available": bool(transcript.get("available") or transcript.get("text")),
        "note_status": note.get("status"),
        "diagnosis": _summarize_diagnosis(note.get("diagnosis") if isinstance(note.get("diagnosis"), dict) else {}),
        "artifact_keys": sorted(str(key) for key in artifacts.keys()),
        "next_action_count": len(next_actions),
    }


def _summarize_diagnosis(diagnosis: dict[str, Any]) -> dict[str, Any] | None:
    if not diagnosis:
        return None
    return {"code": diagnosis.get("code"), "retryable": diagnosis.get("retryable"), "note_present": bool(diagnosis.get("note"))}


def _summarize_export(payload: dict[str, Any]) -> dict[str, Any] | None:
    export = payload.get("export") if isinstance(payload.get("export"), dict) else None
    if not export:
        return None
    return {"target": export.get("target"), "status": export.get("status") or ("ok" if payload.get("ok") else None), "url_present": bool(export.get("url"))}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run FluentFlow Agent/MCP eval cases and print JSON metrics.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mock", action="store_true", help="Run deterministic offline/mock eval cases. This is the default.")
    mode.add_argument("--backend-e2e", action="store_true", help="Run backend MCP eval cases against a running local/backend service.")
    parser.add_argument("--api-base", default=os.environ.get("FLUENTFLOW_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--client-id", default=os.environ.get("FLUENTFLOW_CLIENT_ID", "local-client"))
    parser.add_argument("--access-token", default=os.environ.get("FLUENTFLOW_ACCESS_TOKEN"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    mode = "backend-e2e" if args.backend_e2e else "mock"
    try:
        result = run_eval(mode=mode, api_base=args.api_base, client_id=args.client_id, access_token=args.access_token)
    except Exception as exc:
        print(json.dumps({"ok": False, "mode": mode, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
