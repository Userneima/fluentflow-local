"""The MCP side of the agent judging the cut (``backend/core/agent_cut.py``).

Requirements:
- submitting says who judges the cut (cut_judge), default current agent;
- the agent can fetch the material, send its decision and its report;
- the material is a long tool result Claude Code keeps whole;
- while this MCP process lives it reports the tasks it submitted or touched,
  and stops reporting the ones FluentFlow no longer waits on.
"""

from __future__ import annotations

from scripts import fluentflow_mcp_server as mcp

LOCAL_HEALTH = {"status": "ok", "edition": "local", "runtime": {"execution": "local"}}


def _install(monkeypatch, answers=None):
    calls = []

    def fake(method, api_base, path, **kwargs):
        calls.append((method, path, kwargs.get("payload")))
        if path == "/health":
            return LOCAL_HEALTH
        return (answers or {}).get(path, {"ok": True})

    monkeypatch.setattr(mcp, "api_request", fake)
    monkeypatch.setattr(mcp, "_watched", {})
    return calls


def test_submission_says_who_judges_the_cut(monkeypatch):
    calls = _install(monkeypatch, {"/agent/v1/tasks": {"ok": True, "task_id": "t1"}})
    mcp.submit_local_media("/Users/me/meeting.mov")
    mcp.submit_video_link("https://example.com/v", cut_judge="agent")
    options = [payload["options"] for _, path, payload in calls if path == "/agent/v1/tasks"]
    assert options[0]["cut_judge"] == "agent" and options[1]["cut_judge"] == "agent"
    schema = {tool["name"]: tool for tool in mcp.TOOL_DEFINITIONS}
    for name in ("submit_local_media", "submit_video_link"):
        assert schema[name]["inputSchema"]["properties"]["cut_judge"]["enum"] == ["auto", "agent", "fluentflow"]
    # Submitted tasks are reported from now on.
    assert set(mcp._watched) == {"t1"}


def test_the_agent_fetches_decides_and_reports(monkeypatch):
    calls = _install(monkeypatch)
    mcp.get_agent_cut_material("t1")
    mcp.save_agent_cut("t1", keep_gaps=[{"id": 2, "reason": "演示"}], remove=[], model="m")
    mcp.save_agent_cut_report("t1", summary="s", sections=[])
    sent = [(method, path, payload) for method, path, payload in calls if path != "/health"]
    assert sent == [
        ("GET", "/agent/v1/tasks/t1/agent-cut/material", None),
        ("POST", "/agent/v1/tasks/t1/agent-cut", {"keep_gaps": [{"id": 2, "reason": "演示"}], "remove": [], "model": "m"}),
        ("POST", "/agent/v1/tasks/t1/agent-cut/report", {"report": {"summary": "s", "sections": []}}),
    ]
    material = next(tool for tool in mcp.TOOL_DEFINITIONS if tool["name"] == "get_agent_cut_material")
    assert material["_meta"]["anthropic/maxResultSizeChars"] >= 200_000


def test_the_heartbeat_drops_tasks_fluentflow_no_longer_waits_on(monkeypatch):
    calls = _install(monkeypatch, {"/agent/v1/agent-cut/heartbeat": {"ok": True, "watching": ["t1"]}})
    mcp._watch("t1", None, None)
    mcp._watch("t2", None, None)
    mcp._heartbeat_once()
    beats = [payload for _, path, payload in calls if path == "/agent/v1/agent-cut/heartbeat"]
    assert sorted(beats[0]["task_ids"]) == ["t1", "t2"]
    assert set(mcp._watched) == {"t1"}


def test_an_unanswered_heartbeat_keeps_every_task(monkeypatch):
    _install(monkeypatch, {"/agent/v1/agent-cut/heartbeat": {"ok": False, "error": "down"}})
    mcp._watch("t1", None, None)
    mcp._heartbeat_once()
    assert set(mcp._watched) == {"t1"}


def test_waiting_and_submitting_tell_the_agent_about_judging_the_cut():
    tools = {tool["name"]: tool["description"] for tool in mcp.TOOL_DEFINITIONS}
    assert "get_agent_cut_material" in tools["wait_task"]
    assert "cut_judge" in tools["submit_local_media"]
    assert "do not ask them in chat" in tools["save_agent_cut"]


def test_a_long_wait_does_not_hold_the_conversations_other_calls(monkeypatch):
    """2026-10-28, tasks 3146… and 081a…: a subagent's call for the cut
    material waited ten minutes behind the main agent's wait_task in the same
    MCP process, and the task was given away as never fetched."""
    import io
    import json
    import threading

    release = threading.Event()
    fetched = threading.Event()

    def fake(method, api_base, path, **kwargs):
        if path == "/health":
            return LOCAL_HEALTH
        if path.endswith("/wait"):
            release.wait(5)
            return {"ok": True, "done": True}
        if path.endswith("/agent-cut/material"):
            fetched.set()
        return {"ok": True}

    monkeypatch.setattr(mcp, "api_request", fake)
    monkeypatch.setattr(mcp, "_watched", {})
    calls = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "wait_task", "arguments": {"task_id": "t1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "get_agent_cut_material", "arguments": {"task_id": "t2"}}},
    ]
    out = io.StringIO()
    pending = mcp.run_stdio(io.StringIO("".join(json.dumps(call) + "\n" for call in calls)), out)
    assert fetched.wait(2), "the material call waited behind wait_task"
    # The wait is reported while it holds, not only once it returns.
    assert "t1" in mcp._watched
    release.set()
    for thread in pending:
        thread.join(5)
    answered = [json.loads(line)["id"] for line in out.getvalue().splitlines()]
    assert answered == [2, 1]
