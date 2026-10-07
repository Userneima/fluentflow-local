"""The MCP tool descriptions match the flow an agent actually gets.

Requirements: submitting cuts breath gaps and writes the note by itself;
waiting covers the note; the cut and the cut-media note tools are for redoing
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
    assert "note" in text and "note_pending" in text


def test_the_step_tools_are_for_redoing_not_the_normal_path():
    for name in ("debreath_task", "write_note_from_cut_media"):
        assert "submitting already" in _description(name), name
    debreath = _description("debreath_task")
    assert "timeline" in debreath and "resubmit" in debreath
