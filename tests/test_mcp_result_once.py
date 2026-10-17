"""A tool's answer reaches the agent once.

Requirement (2026-10-16): Codex took the note material in one call but showed
it twice (as text and again as structured content), so its display limit cut
it off and it fell back to writing the note in parts.
"""

from __future__ import annotations

import json

from scripts import fluentflow_mcp_server as server


def test_a_tool_answer_carries_its_json_once(monkeypatch):
    payload = {"ok": True, "transcript": "[00:00] 讲课" * 1000}
    monkeypatch.setitem(server.TOOL_FUNCTIONS, "get_task", lambda task_id: payload)

    result = server._call_tool("get_task", {"task_id": "t"})

    assert "structuredContent" not in result
    texts = [part["text"] for part in result["content"] if part.get("type") == "text"]
    assert len(texts) == 1 and json.loads(texts[0]) == payload


def test_a_refusal_is_still_marked_as_an_error(monkeypatch):
    monkeypatch.setitem(server.TOOL_FUNCTIONS, "get_task", lambda task_id: {"ok": False, "error": "没有这个任务"})

    result = server._call_tool("get_task", {"task_id": "t"})

    assert result["isError"] is True
    assert "没有这个任务" in result["content"][0]["text"]
