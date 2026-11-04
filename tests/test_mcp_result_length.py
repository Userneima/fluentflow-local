"""Long agent material says how long it is, first, so a host that cuts it short can tell."""

import json

import scripts.fluentflow_mcp_server as mcp


def _text(name, arguments):
    return mcp._call_tool(name, arguments)["content"][0]["text"]


def _serve(monkeypatch, answer):
    monkeypatch.setattr(mcp, "_require_local_edition", lambda base: None)
    monkeypatch.setattr(mcp, "_agent_request", lambda *a, **k: answer)


def test_material_starts_with_its_whole_length(monkeypatch):
    long = "00:01 讲者：" + "这一句很长。" * 5000
    for name in ("get_agent_note_material", "get_agent_cut_material"):
        _serve(monkeypatch, {"ok": True, "task_id": "t1", "transcript": long, "timeline": long})
        text = _text(name, {"task_id": "t1"})
        answer = json.loads(text)
        # What an agent sees first, the part a cut-off result keeps.
        assert list(answer)[:2] == ["result_chars", "if_cut_off"]
        assert answer["result_chars"] == len(text)
        assert text.startswith('{"result_chars": %d' % len(text))
        assert "small_parts" in answer["if_cut_off"]
        assert answer["transcript"] == long


def test_a_refusal_is_passed_on_unchanged(monkeypatch):
    refusal = {"ok": False, "error": "这个任务的剪辑已经判断过了。", "status": 409, "payload": None}
    _serve(monkeypatch, refusal)
    assert json.loads(_text("get_agent_cut_material", {"task_id": "t1"})) == refusal


def test_tools_that_return_a_whole_note_or_transcript_lift_claude_codes_limit():
    lifted = {d["name"] for d in mcp.TOOL_DEFINITIONS if (d.get("_meta") or {}).get("anthropic/maxResultSizeChars")}
    assert {"get_task_package", "get_agent_note_material", "get_agent_cut_material"} <= lifted
