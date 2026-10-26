"""The MCP tool descriptions match the flow an agent actually gets.

Requirements: submitting transcribes, has a model decide the cut, cuts and
writes the note by itself, and says how an agent writes the note on its own
subscription instead; waiting covers the cut and the note; the cut and the cut-media note tools are for redoing
a step on an existing task, and re-cutting warns about the timeline.
"""

from __future__ import annotations

from scripts import fluentflow_mcp_server as mcp


def _description(name: str) -> str:
    return next(item for item in mcp.TOOL_DEFINITIONS if item["name"] == name)["description"].lower()


def test_submitting_says_it_cuts_and_writes_the_note():
    for name in ("submit_local_media", "submit_video_link"):
        text = _description(name)
        assert "breath" in text and "note" in text and "wait_task" in text, name


def test_waiting_covers_the_note():
    text = _description("wait_task")
    assert "note" in text and "note_pending" in text and "cut_running" in text


def test_submitting_describes_the_survey_first_flow_and_the_agents_own_note():
    for name in ("submit_local_media", "submit_video_link"):
        text = _description(name)
        assert "transcribes the original" in text and "you decide what to cut" in text, name
        assert "skip_summary=true" in text and "get_agent_note_material" in text, name


def test_the_step_tools_are_for_redoing_not_the_normal_path():
    for name in ("debreath_task", "write_note_from_cut_media"):
        assert "submitting already" in _description(name), name
    debreath = _description("debreath_task")
    assert "timeline" in debreath and "resubmit" in debreath


def test_the_check_script_expects_every_tool_the_server_lists():
    from scripts import check_mcp_server

    listed = {item["name"] for item in mcp.TOOL_DEFINITIONS}
    assert check_mcp_server.EXPECTED_TOOLS == listed == set(mcp.TOOL_FUNCTIONS)
    assert check_mcp_server.parse_args(["--backend-e2e"]).backend_e2e is True


def test_submit_transcript_returns_at_once_and_passes_the_note_settings(monkeypatch):
    sent = {}
    monkeypatch.setattr(mcp, "_agent_request", lambda method, path, **kw: sent.update(kw) or {"ok": True})

    mcp.submit_transcript("文本", note_mode="direct", prompt_preset="meeting", system_prompt="只列行动项")

    assert sent["payload"]["wait"] is False
    assert sent["payload"]["options"]["system_prompt"] == "只列行动项"
    assert "wait_task" in _description("submit_transcript")


def test_descriptions_say_what_the_tools_really_do():
    for name in ("submit_local_media", "submit_video_link"):
        text = _description(name)
        assert "only apply when a text model writes the note" in text
        assert "anthropic api key" in text and "subscription" in text
    assert "original recording" in _description("write_note_from_cut_media")
    assert "new task" in _description("retry_task") and "completed" in _description("retry_task")
    noise = next(i for i in mcp.TOOL_DEFINITIONS if i["name"] == "debreath_task")["inputSchema"]["properties"]["noise_db"]
    assert "measured" in noise["description"]


def test_retry_task_can_turn_speaker_separation_on(monkeypatch):
    """Requirement (2026-10-15): the agent re-runs a multi-person recording with
    speaker separation through retry_task, and the tool says how."""
    sent: dict = {}
    monkeypatch.setattr(mcp, "_agent_request", lambda method, path, **kw: sent.update(kw, path=path) or {"ok": True})

    mcp.retry_task("t-1", speaker_diarization=True)
    assert sent["path"] == "/agent/v1/tasks/t-1/retry"
    assert sent["payload"] == {"speaker_diarization": True}

    sent.clear()
    mcp.retry_task("t-1")
    assert sent["payload"] is None

    tool = next(t for t in mcp.TOOL_DEFINITIONS if t["name"] == "retry_task")
    assert "speaker_diarization" in tool["inputSchema"]["properties"]
    assert "speaker_diarization=true" in tool["description"]
