"""The note-from-cut-media step as an agent-actionable workflow.

Per `docs/agent_mcp_parity.md` an external agent has to be able to submit this,
wait for it, read what it was written from, retry it, and download it. What is
guarded here:

- the Agent API reaches the SAME entry the page reaches, so a guard added to one
  surface cannot go missing from the other;
- the free calls stay free. A preview and either of the two note switches must not
  be able to spend the machine owner's Claude allowance, because an agent that can
  cost money by reading is a different thing from one that can cost money by
  asking;
- the task package says which file the note was written from and whose clock its
  timestamps are on. Without those an agent cannot tell this note from the
  ordinary transcript note, and the difference is the entire flow;
- the MCP tool wraps the stable Agent API path and defaults to the free call.

Local-edition only, and that is a recorded boundary rather than an omission: the
flow does not exist on the hosted server at all, neither as a page route nor as
an agent route, because who pays for the outbound Claude call on a shared server
is unanswered. See `docs/visual_note_plan.md` and `docs/edition_boundaries.md`.
The other side of that boundary is asserted in the `fluentflow` repository,
which has to prove its task package does not advertise this route; from here
there is no hosted router to look at.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.core.debreath_job as dj
import backend.core.visual_note_job as vn
import backend.routers.job_visual_note as jvn
import backend.routers.local_agent as local_agent
from backend.core.local_agent_package import build_agent_task_package
from backend.core.result_artifacts import VISUAL_NOTE_KIND, artifact_target_path
from scripts import fluentflow_mcp_server as mcp

TASK = "task-agent-cut-note"
_TOKEN = "local-secret-token"
_HEADERS = {"x-fluentflow-client-id": "desktop-a", "x-fluentflow-access-token": _TOKEN}

CUT_LIST = {
    "cuts": [{"start": 10.0, "end": 20.0}],
    "keeps": [{"start": 0.0, "end": 10.0}, {"start": 20.0, "end": 100.0}],
}


def _debreathed_result(**extra: Any) -> dict[str, Any]:
    return {
        "task_id": TASK,
        "filename": "lecture.mp4",
        "summary_markdown": "原来的文字笔记",
        "display_segments": [
            {"start": 0.0, "end": 5.0, "text": "开场"},
            {"start": 65.0, "end": 70.0, "text": "看这张幻灯片"},
        ],
        "debreath": {
            "status": "completed",
            "rendered": True,
            "render_verified": True,
            "media_filename": "debreath/lecture_debreath.mp4",
            "plan": {"cut_count": 1, "removed_seconds": 10.0},
            "transcript_timeline": {"source": "debreath_cut_list_remap", "dropped_segments": 0},
        },
        "artifacts": {
            dj.MEDIA_KIND: {"kind": dj.MEDIA_KIND, "filename": "debreath/lecture_debreath.mp4"},
            dj.CUT_LIST_KIND: {"kind": dj.CUT_LIST_KIND, "filename": "debreath/lecture_cut_list.json"},
            dj.TRANSCRIPT_KIND: {"kind": dj.TRANSCRIPT_KIND, "filename": "debreath/lecture_debreath.srt"},
        },
        **extra,
    }


@pytest.fixture(autouse=True)
def cut_files():
    """A cut file and its cut list where a real render would have left them."""
    artifact_target_path(TASK, "debreath/lecture_debreath.mp4").write_bytes(b"pretend cut video")
    artifact_target_path(TASK, "debreath/lecture_cut_list.json").write_text(
        json.dumps(CUT_LIST), encoding="utf-8"
    )
    vn.release(TASK)
    yield
    vn.release(TASK)


@pytest.fixture()
def local_app(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", _TOKEN)
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "api_key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    app = FastAPI()
    app.include_router(local_agent.router)
    return app


@pytest.fixture()
def agent_job(monkeypatch):
    """One de-breathed job, visible to the agent router and to the shared entry."""
    state = {"task_id": TASK, "status": "completed", "stage": "done", "progress": 100,
             "result": _debreathed_result()}

    def reader(task_id: str, **_: Any):
        return {**state, "result": dict(state["result"])} if task_id == TASK else None

    def writer(task_id: str, result: dict, **_: Any):
        if task_id != TASK:
            return None
        state["result"] = result
        return {**state, "result": dict(result)}

    monkeypatch.setattr(jvn, "get_job", reader)
    monkeypatch.setattr(vn, "get_job", reader)
    monkeypatch.setattr(vn, "update_job_result", writer)
    monkeypatch.setattr(local_agent, "get_job", reader)
    return state


# ── submitting, and the guards it goes through ─────────────────────────────

def test_an_agent_can_start_the_note_and_reaches_the_same_entry_as_the_page(
    agent_job, local_app, monkeypatch
):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        vn, "run_visual_note", lambda task_id, **kwargs: calls.append({"task_id": task_id, **kwargs})
    )

    response = TestClient(local_app).post(
        f"/agent/v1/tasks/{TASK}/visual-note", json={}, headers=_HEADERS
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] is True and body["replace_note"] is True
    assert body["media"]["kind"] == vn.MEDIA_CUT
    assert body["subtitle_timeline"]["source"] == "debreath_cut_list_remap"
    assert body["package_url"] == f"/agent/v1/tasks/{TASK}/package"
    assert "cut_media_note" in body["package"]
    assert calls[0]["replace_note"] is True and calls[0]["claimed"] is True


def test_an_agent_can_ask_for_the_note_without_touching_the_tasks_note(
    agent_job, local_app, monkeypatch
):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        vn, "run_visual_note", lambda task_id, **kwargs: calls.append({"task_id": task_id, **kwargs})
    )

    response = TestClient(local_app).post(
        f"/agent/v1/tasks/{TASK}/visual-note", json={"replace_note": False}, headers=_HEADERS
    )

    assert response.json()["replace_note"] is False
    assert calls[0]["replace_note"] is False


def test_a_task_with_no_cut_file_is_refused_with_the_first_step_named(
    agent_job, local_app, monkeypatch
):
    """The same refusal the page gets, in the response the agent is waiting on."""
    agent_job["result"] = {"task_id": TASK, "filename": "lecture.mp4"}
    monkeypatch.setattr(vn, "run_visual_note", lambda *a, **k: pytest.fail("must not start"))

    response = TestClient(local_app).post(
        f"/agent/v1/tasks/{TASK}/visual-note", json={}, headers=_HEADERS
    )

    assert response.status_code == 409
    assert "去气口" in response.json()["detail"]


def test_a_run_already_going_is_refused_rather_than_queued(agent_job, local_app, monkeypatch):
    monkeypatch.setattr(vn, "run_visual_note", lambda *a, **k: pytest.fail("must not start"))
    vn.claim(TASK)

    response = TestClient(local_app).post(
        f"/agent/v1/tasks/{TASK}/visual-note", json={}, headers=_HEADERS
    )

    assert response.status_code == 409
    assert "正在" in response.json()["detail"]


# ── the free calls stay free ───────────────────────────────────────────────

def test_a_preview_spends_nothing_and_reports_what_would_be_sent(
    agent_job, local_app, monkeypatch
):
    monkeypatch.setattr(vn, "run_visual_note", lambda *a, **k: pytest.fail("must not spend"))

    body = TestClient(local_app).post(
        f"/agent/v1/tasks/{TASK}/visual-note", json={"preview": True}, headers=_HEADERS
    ).json()

    assert body["preview"] is True and body["eligible"] is True
    assert body["media"]["filename"] == "debreath/lecture_debreath.mp4"
    assert body["frame_budget"] > 0 and body["transcript_chars"] > 0
    assert body["will_replace_note"] is True
    assert body["channel_label"], "whose allowance pays, before the button"


def test_switching_between_the_two_notes_needs_no_model_call(agent_job, local_app, monkeypatch):
    monkeypatch.setattr(vn, "run_visual_note", lambda *a, **k: pytest.fail("must not spend"))
    agent_job["result"]["visual_note"] = {
        "status": "completed",
        "markdown": "根据剪后版本写的笔记",
        "promoted": True,
        "replaced_note": {"previous_markdown": "原来的文字笔记"},
    }
    agent_job["result"]["summary_markdown"] = "根据剪后版本写的笔记"
    client = TestClient(local_app)

    restored = client.post(
        f"/agent/v1/tasks/{TASK}/visual-note",
        json={"restore_previous_note": True},
        headers=_HEADERS,
    )
    assert restored.status_code == 200
    assert restored.json()["result"]["summary_markdown"] == "原来的文字笔记"

    again = client.post(
        f"/agent/v1/tasks/{TASK}/visual-note",
        json={"use_generated_note": True},
        headers=_HEADERS,
    )
    assert again.status_code == 200
    assert again.json()["result"]["summary_markdown"] == "根据剪后版本写的笔记"


def test_restoring_when_there_is_nothing_to_restore_is_refused_in_words(
    agent_job, local_app
):
    response = TestClient(local_app).post(
        f"/agent/v1/tasks/{TASK}/visual-note",
        json={"restore_previous_note": True},
        headers=_HEADERS,
    )

    assert response.status_code == 409
    assert "恢复" in response.json()["detail"]


# ── what the task package tells an agent ───────────────────────────────────

def _package(result: dict[str, Any]) -> dict[str, Any]:
    return build_agent_task_package(
        {"task_id": TASK, "status": "completed", "stage": "done", "result": result}
    )


def test_the_package_says_which_file_the_note_was_written_from(agent_job):
    result = _debreathed_result(
        visual_note={
            "status": "completed",
            "markdown": "![幻灯片](x.jpg)\n\n正文",
            "basis": vn.BASIS_BOTH,
            "basis_note": "两张幻灯片看得清",
            "promoted": True,
            "media_source": {"kind": vn.MEDIA_CUT, "filename": "debreath/lecture_debreath.mp4",
                             "has_video": True, "unchanged": False},
            "subtitle_timeline": {"source": "debreath_cut_list_remap", "segments": 2,
                                  "dropped_segments": 1},
            "frames_sent": [{"filename": "note_0001.jpg"}, {"filename": "note_0002.jpg"}],
            "frames_cited": [{"filename": "note_0001.jpg"}],
        },
        summary_written_from=vn.SUMMARY_WRITTEN_FROM,
    )
    result["artifacts"][VISUAL_NOTE_KIND] = {"kind": VISUAL_NOTE_KIND, "filename": "visual_note.md"}

    note = _package(result)["cut_media_note"]

    assert note["available"] is True and note["status"] == "completed"
    assert note["media_source"]["kind"] == vn.MEDIA_CUT
    assert note["subtitle_timeline"]["dropped_segments"] == 1
    assert note["basis"] == vn.BASIS_BOTH
    assert note["frames_sent"] == 2 and note["frames_cited"] == 1
    assert note["promoted"] is True
    assert note["summary_written_from"] == vn.SUMMARY_WRITTEN_FROM
    assert note["note_artifact"] == VISUAL_NOTE_KIND


def test_the_package_carries_the_measured_basis_so_audio_cannot_claim_pictures(agent_job):
    note = _package(_debreathed_result(visual_note={
        "status": "completed",
        "markdown": "只有声音",
        "basis": vn.BASIS_TRANSCRIPT_ONLY,
        "media_source": {"kind": vn.MEDIA_CUT, "has_video": False, "unchanged": False},
        "frames_sent": [],
    }))["cut_media_note"]

    assert note["basis"] == vn.BASIS_TRANSCRIPT_ONLY
    assert note["frames_sent"] == 0
    assert note["media_source"]["has_video"] is False


def test_never_run_is_different_from_run_and_found_nothing(agent_job):
    note = _package(_debreathed_result())["cut_media_note"]

    assert note["available"] is False and note["status"] is None
    assert note["basis"] is None, "which is not the same as transcript_only"


def test_the_cut_versions_own_subtitles_are_named_in_the_debreath_block(agent_job):
    debreath = _package(_debreathed_result())["debreath"]

    assert debreath["transcript_artifact"] == dj.TRANSCRIPT_KIND
    assert debreath["transcript_timeline"]["source"] == "debreath_cut_list_remap"
    assert debreath["media_filename"] == "debreath/lecture_debreath.mp4"


# ── next actions: only when there is a step to take ────────────────────────

def test_a_cut_file_with_no_note_yet_gets_the_note_suggested(agent_job):
    actions = {item["action"] for item in _package(_debreathed_result())["next_actions"]}

    assert "write_cut_media_note" in actions


def test_a_task_that_was_never_de_breathed_is_not_told_to_write_the_note(agent_job):
    package = _package({"task_id": TASK, "summary_markdown": "笔记"})

    actions = {item["action"] for item in package["next_actions"]}
    assert "write_cut_media_note" not in actions, "the next step there belongs to the de-breath"


def test_a_running_note_gets_a_wait_and_a_failed_one_gets_the_reason(agent_job):
    running = _package(_debreathed_result(visual_note={"status": "running"}))["next_actions"]
    assert any(item["action"] == "wait_cut_media_note" for item in running)

    failed = _package(
        _debreathed_result(visual_note={"status": "failed", "error": "本机 Claude 没有登录"})
    )["next_actions"]
    retry = next(item for item in failed if item["action"] == "write_cut_media_note")
    assert "没有登录" in retry["reason"]


def test_a_finished_note_suggests_nothing(agent_job):
    package = _package(_debreathed_result(visual_note={"status": "completed", "markdown": "笔记"}))

    actions = {item["action"] for item in package["next_actions"]}
    assert "write_cut_media_note" not in actions and "wait_cut_media_note" not in actions


# ── the MCP tool wraps the stable path ─────────────────────────────────────

def test_the_mcp_tool_defaults_to_the_free_call(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_request(method, path, **kwargs):
        seen.update({"method": method, "path": path, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(mcp, "_agent_request", fake_request)

    mcp.write_note_from_cut_media(TASK)

    assert seen["method"] == "POST"
    assert seen["path"] == f"/agent/v1/tasks/{TASK}/visual-note"
    assert seen["payload"] == {"preview": True}, "reading must not cost the user money"


def test_the_mcp_tool_can_run_it_and_can_switch_notes(monkeypatch):
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        mcp, "_agent_request", lambda method, path, **kwargs: seen.append(kwargs) or {"ok": True}
    )

    mcp.write_note_from_cut_media(TASK, preview=False)
    mcp.write_note_from_cut_media(TASK, preview=False, replace_note=False)
    mcp.write_note_from_cut_media(TASK, restore_previous_note=True)
    mcp.write_note_from_cut_media(TASK, use_generated_note=True)

    assert [item["payload"] for item in seen] == [
        {"replace_note": True},
        {"replace_note": False},
        {"restore_previous_note": True},
        {"use_generated_note": True},
    ]


def test_the_mcp_tool_is_listed_with_the_product_actions():
    definition = next(
        item for item in mcp.TOOL_DEFINITIONS if item["name"] == "write_note_from_cut_media"
    )

    assert definition["inputSchema"]["required"] == ["task_id"]
    assert definition["inputSchema"]["properties"]["preview"]["default"] is True
    assert "debreath_task" in definition["description"], "the flow's first step is named"
