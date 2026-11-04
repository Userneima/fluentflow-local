"""What an agent reads back is short and says what to do next (MCP tool
audit, docs/方案/2026-11-04-MCP工具体检.md items 4 to 7).

Written from what the agent should see, not from the code:
- a corrections list shows the changes still in force, says how many were
  undone, and gives each word-list line once;
- a processing report shows the sections the page shows, and a folded one
  only as a title and a count until asked for;
- a mistyped task id says how to find the right one; an error says its text
  once and keeps its code where the agent looks;
- answers point at tools, not HTTP paths; a list cut at 100 says so;
- an argument of the wrong type is named, not a Python traceback;
- no tool asks for api_base.
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.core import agent_cut, task_actions
from backend.routers import local_agent
from scripts import fluentflow_mcp_server as mcp
from scripts.local_agent_client import FluentFlowApiError


def test_a_corrections_list_shows_what_is_in_force_and_each_word_list_line_once():
    line = "- 黑客级竞品调研：本期训练营的名字。"
    records = [
        {"id": "a", "from": "镜头", "to": "竞品", "entry": line, "reverted": True},
        {"id": "b", "from": "镜头", "to": "竞品", "entry": line},
        {"id": "c", "from": "性格", "to": "黑客", "entry": line},
        {"id": "d", "from": "Cloud", "to": "Claude", "why": "上下文"},
    ]
    view = local_agent._corrections_view(records, include_reverted=False)
    assert [r["id"] for r in view["corrections"]] == ["b", "c", "d"]
    assert view["reverted_count"] == 1
    assert view["entries"] == [line]
    assert all(r.get("entry_ref") == 0 and "entry" not in r for r in view["corrections"][:2])
    assert "entry_ref" not in view["corrections"][2]

    everything = local_agent._corrections_view(records, include_reverted=True)
    assert [r["id"] for r in everything["corrections"]] == ["a", "b", "c", "d"]
    assert everything["reverted_count"] == 1


def test_the_report_folds_what_the_page_folds_until_that_section_is_asked_for(monkeypatch):
    report = {
        "summary": "s",
        "sections": [
            {"title": "需要你判断", "judge": True, "folded": False, "items": [{"ref": "1", "text": "a"}]},
            {"title": "剪掉的长空白", "folded": False, "items": [{"ref": "2", "text": "b"}]},
            {"title": "其他处理", "folded": True, "items": [{"ref": str(i), "text": "x"} for i in range(3, 200)]},
        ],
    }
    job = {"result": {}}
    monkeypatch.setattr(task_actions, "_job", lambda *a, **k: job)
    monkeypatch.setattr(task_actions.debreath_job, "debreath_state", lambda result: {"survey": {"report": report}})

    default = task_actions.cut_report("t")["sections"]
    assert [len(s.get("items", [])) for s in default] == [1, 1, 0]
    assert default[2] == {"title": "其他处理", "to_judge": False, "folded": True, "item_count": 197}

    asked = task_actions.cut_report("t", section="其他处理")["sections"]
    assert len(asked[2]["items"]) == 197 and asked[2]["items"][0]["ref"] == "3"


def _failing(status, payload):
    def request(*args, **kwargs):
        detail = payload.get("detail")
        message = detail if isinstance(detail, str) else detail.get("message")
        raise FluentFlowApiError(message, status=status, payload=payload)
    return request


def test_a_mistyped_task_id_says_how_to_find_the_right_one(monkeypatch):
    for wording in ("Job not found", "任务不存在"):
        monkeypatch.setattr(mcp, "api_request", _failing(404, {"detail": wording}))
        answer = mcp.get_task("0b11a2f9")
        assert answer["ok"] is False and answer["code"] == "task_not_found"
        assert "32" in answer["error"] and "list_tasks" in answer["error"]


def test_an_error_is_said_once_and_its_code_is_where_the_agent_looks(monkeypatch):
    detail = {"code": "duplicate_link", "message": "这个链接已经处理过", "existing_task_id": "abc"}
    monkeypatch.setattr(mcp, "api_request", _failing(409, {"detail": detail}))
    answer = mcp.get_task("t" * 32)
    assert answer == {
        "ok": False, "error": "这个链接已经处理过", "status": 409,
        "code": "duplicate_link", "existing_task_id": "abc",
    }


def test_answers_name_the_package_tool_not_its_http_path(monkeypatch):
    monkeypatch.setitem(mcp.TOOL_FUNCTIONS, "get_task", lambda task_id: {
        "ok": True, "package_url": "/agent/v1/tasks/x/package", "task": {"package_url": "/agent/v1/tasks/x/package"},
    })
    text = mcp._call_tool("get_task", {"task_id": "x"})["content"][0]["text"]
    assert "/agent/v1" not in text and text.count('"package": "get_task_package"') == 2


def test_a_list_cut_at_its_limit_says_there_is_more(monkeypatch):
    jobs = [{"task_id": f"t{i}", "result": {"filename": f"f{i}"}} for i in range(5)]
    monkeypatch.setattr(local_agent, "list_jobs", lambda **kwargs: jobs)
    monkeypatch.setattr(local_agent, "_local_client_scope", lambda request: None)
    request = SimpleNamespace()
    assert local_agent.list_agent_tasks(request, limit=3)["has_more"] is True
    assert len(local_agent.list_agent_tasks(request, limit=3)["tasks"]) == 3
    assert local_agent.list_agent_tasks(request, limit=5)["has_more"] is False


def test_an_argument_of_the_wrong_type_is_named(monkeypatch):
    seen = {}
    monkeypatch.setitem(mcp.TOOL_FUNCTIONS, "list_tasks", lambda query="", limit=20: seen.update(limit=limit) or {"ok": True})
    mcp._call_tool("list_tasks", {"limit": "50"})
    assert seen["limit"] == 50

    refused = mcp._call_tool("list_tasks", {"limit": "many"})
    assert refused["isError"] is True and "limit" in refused["content"][0]["text"]


def test_a_task_not_waiting_for_a_cut_says_so():
    said = agent_cut._not_waiting({"status": agent_cut.FALLBACK, "fallback_reason": "agent 的对话已经关掉了"})
    assert said.startswith("这个任务现在不需要判断剪辑")


def test_no_tool_asks_for_api_base():
    assert not [t["name"] for t in mcp.TOOL_DEFINITIONS if "api_base" in t["inputSchema"].get("properties", {})]
