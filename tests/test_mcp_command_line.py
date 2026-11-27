"""The MCP tools from the command line.

Requirement (2026-11-27, onboarding): Claude Code and Codex load MCP servers
only when a conversation starts, so the conversation that ran the install
cannot call the tools it just registered. Asking the user to quit and resume
was a step he did not follow; the agent can run commands in that conversation,
so ``fluentflow_mcp_server.py call <tool> '<json>'`` runs the same tool code.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

from scripts import fluentflow_mcp_server as server

ROOT = Path(__file__).resolve().parents[1]


def _run(argv, monkeypatch=None, stdin=""):
    out = io.StringIO()
    if monkeypatch is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = server.run_cli(argv, out=out)
    return code, out.getvalue()


def test_call_prints_the_tools_answer_and_exits_0(monkeypatch):
    seen = {}

    def get_task(task_id):
        seen["task_id"] = task_id
        return {"ok": True, "status": "done", "package_url": "/agent/v1/tasks/x/package"}

    monkeypatch.setitem(server.TOOL_FUNCTIONS, "get_task", get_task)

    code, out = _run(["call", "get_task", '{"task_id": "abc"}'])

    assert code == 0 and seen == {"task_id": "abc"}
    # the same answer the MCP tool gives, package path named as the agent reads it
    assert json.loads(out) == {"ok": True, "status": "done", "package": "get_task_package"}


def test_arguments_can_come_from_stdin_and_default_to_none(monkeypatch):
    monkeypatch.setitem(server.TOOL_FUNCTIONS, "get_task", lambda task_id: {"ok": True, "id": task_id})
    code, out = _run(["call", "get_task", "-"], monkeypatch, stdin='{"task_id": "from-stdin"}')
    assert code == 0 and json.loads(out)["id"] == "from-stdin"

    monkeypatch.setitem(server.TOOL_FUNCTIONS, "list_tasks", lambda limit=5: {"ok": True, "limit": limit})
    code, out = _run(["call", "list_tasks"])
    assert code == 0 and json.loads(out)["limit"] == 5


def test_a_refusal_exits_1_and_still_prints_the_answer(monkeypatch):
    monkeypatch.setitem(server.TOOL_FUNCTIONS, "get_task", lambda task_id: {"ok": False, "code": "task_not_found"})
    code, out = _run(["call", "get_task", '{"task_id": "x"}'])
    assert code == 1 and json.loads(out)["code"] == "task_not_found"


def test_usage_errors_exit_2_without_calling_anything(monkeypatch, capsys):
    monkeypatch.setitem(server.TOOL_FUNCTIONS, "get_task", lambda task_id: (_ for _ in ()).throw(AssertionError))
    for argv in (["call"], ["call", "no_such_tool"], ["call", "get_task", "not json"],
                 ["call", "get_task", "[1]"], ["call", "get_task", "{}", "extra"], ["frobnicate"]):
        assert _run(argv)[0] == 2, argv
    assert _run(["call", "get_task", "{}"])[0] == 2  # missing task_id
    assert "tools" in capsys.readouterr().err


def test_tools_lists_every_tool_and_describes_one():
    code, out = _run(["tools"])
    names = [line.split(":", 1)[0] for line in out.splitlines()]
    assert code == 0 and names == [d["name"] for d in server.TOOL_DEFINITIONS]

    code, out = _run(["tools", "submit_video_link"])
    assert code == 0 and json.loads(out)["name"] == "submit_video_link"
    assert _run(["tools", "nope"])[0] == 2


def test_the_script_itself_runs_the_command_line():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "fluentflow_mcp_server.py"), "tools"],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0
    assert "submit_video_link:" in result.stdout


def test_a_long_call_waits_until_done_instead_of_answering_still_working(monkeypatch):
    """The next command is a new process and could not pick the run up again."""
    import threading
    from scripts.fluentflow_mcp import core

    monkeypatch.setattr(core, "HOLD_SECONDS", 0.05)
    release = threading.Event()

    def slow(task_id):
        return core._held("get_task", {"task_id": task_id}, lambda: (release.wait(5), {"ok": True, "done": True})[1])

    monkeypatch.setitem(server.TOOL_FUNCTIONS, "get_task", slow)
    threading.Timer(0.3, release.set).start()

    code, out = _run(["call", "get_task", '{"task_id": "long"}'])

    assert code == 0 and json.loads(out) == {"ok": True, "done": True}
    assert core._heartbeat_enabled is False
    assert core.HOLD_SECONDS == 0.05  # put back for anything else in this process


def test_piped_arguments_with_a_byte_order_mark_are_read(monkeypatch):
    """Windows PowerShell 5.1 pipes a UTF-8 BOM before the JSON (release check 2026-11-27)."""
    monkeypatch.setitem(server.TOOL_FUNCTIONS, "record_first_use", lambda material=None: {"ok": True, "material": material})

    class Stdin:
        buffer = io.BytesIO('\ufeff{"material": "抖音 https://v.douyin.com/abc/"}'.encode("utf-8"))

    monkeypatch.setattr(sys, "stdin", Stdin())
    code, out = _run(["call", "record_first_use", "-"])
    assert code == 0 and json.loads(out)["material"] == "抖音 https://v.douyin.com/abc/"
