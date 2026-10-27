"""An agent's note starts while the cut renders, and its waits cost little.

Requirements (to-do「效率」, 2026-10-27; decisions 5.40 and 5.41), written from
what the agent needs rather than from the code:

- Once the cut is decided and rendering, the agent gets its note material
  without waiting for the render: the transcript on the cut video's clock,
  frames taken from the original at the moments the cut will show them, and
  the same transcript it would get after the render.
- Taking that material is not refused because the task's own render holds
  the queue.
- A note saved meanwhile is the task's note; if the cut then does not come
  out, the note goes back onto the original's clock.
- One wait can hold up to ten minutes; it returns at once when the agent can
  start its note (once, not on every later wait), when the task is done, and
  with the current state when its time runs out.
"""

from __future__ import annotations

import copy
import functools
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.core.agent_note as an
import backend.core.visual_note_job as vn
from backend.core import cut_timeline, debreath_job as dj, job_store as js, survey_cut
from backend.core.local_request_scope import LOCAL_OWNER_ID
import backend.routers.local_agent as local_agent
from backend.routers.local_agent import router as agent_router
from test_visual_note_job import CUT_LIST, SEGMENTS, TASK, _fake_extractor, job_store, no_ambient_key  # noqa: F401


def _rendering(state: dict, original) -> None:
    """The task as it stands mid-render: the cut decided, no cut file yet."""
    result = state["result"]
    result["intelligence"] = "agent"
    result["summary_markdown"] = ""
    result["artifacts"] = {}
    result["debreath"] = {
        "status": dj.STATUS_RUNNING,
        "stage": "rendering",
        "started_at": "2026-10-27T10:00:00",
        survey_cut.DECIDED_KEY: {
            "render_started_at": "2026-10-27T10:00:00",
            "cuts": [[10.0, 20.0]], "keeps": copy.deepcopy(CUT_LIST["keeps"]), "judged_by": "agent",
        },
    }


@pytest.fixture()
def rendering(job_store, tmp_path, monkeypatch):
    original = tmp_path / "lecture.mp4"
    original.write_bytes(b"pretend original video")
    monkeypatch.setattr(vn, "find_source_file", lambda _t: original)
    _rendering(job_store, original)
    return {"state": job_store, "original": original}


def _rendered(state: dict) -> None:
    """What survey_cut leaves once the render is done: the cut file, and the
    transcript moved onto the cut's clock by the same stretches."""
    from test_visual_note_job import _cut_media

    _cut_media(TASK)
    result = state["result"]
    ranges = cut_timeline.kept_ranges(CUT_LIST)
    result.update(survey_cut._onto_cut_clock({**result, "raw_segments": result["display_segments"]}, ranges))
    result["display_segments"] = result["raw_segments"]
    result["debreath"] = {
        "status": dj.STATUS_COMPLETED, "stage": "done", "rendered": True, "render_verified": True,
        "used_for_transcription": True, "media_filename": "debreath/lecture_debreath.mp4",
        "plan": {"cut_count": 1},
    }
    result["artifacts"] = {
        dj.MEDIA_KIND: {"kind": dj.MEDIA_KIND, "filename": "debreath/lecture_debreath.mp4"},
        dj.CUT_LIST_KIND: {"kind": dj.CUT_LIST_KIND, "filename": "debreath/lecture_cut_list.json"},
    }


def _material(tmp_path, extractor=None):
    return an.material(TASK, client_id="anonymous", keyframe_extractor=extractor or _fake_extractor(tmp_path))


def test_material_comes_while_the_cut_renders_on_the_cut_clock(rendering, tmp_path):
    extractor = _fake_extractor(tmp_path)

    material = an.material(TASK, client_id="anonymous", keyframe_extractor=extractor)

    # 65 s in the original is 55 s in the cut; the cut-away line is gone.
    assert "[00:55] 看这张幻灯片上的公式" in material["transcript"]
    assert "这一句整段都是被剪掉的空白" not in material["transcript"]
    # Frames come from the original, at the moment the cut will show them:
    # 30 s and 60 s in the original are 20 s and 50 s in the cut.
    assert extractor.seen["video_path"] == str(rendering["original"])
    assert [f["at"] for f in material["frames"]] == ["00:00", "00:20"]


def test_the_material_is_the_same_before_and_after_the_render(rendering, tmp_path):
    during = _material(tmp_path)
    _rendered(rendering["state"])

    after = an.material(TASK, client_id="anonymous", keyframe_extractor=lambda *a, **k: pytest.fail("frames again"))

    assert after["transcript"] == during["transcript"]
    assert [f["filename"] for f in after["frames"]] == [f["filename"] for f in during["frames"]]


def test_a_part_saved_during_the_render_still_counts_after_it(rendering, tmp_path):
    an.material(TASK, client_id="anonymous", small_parts=True, keyframe_extractor=_fake_extractor(tmp_path))
    an.save_part(TASK, part=1, note_markdown="# 笔记\n\n第一部分正文。", client_id="anonymous")
    _rendered(rendering["state"])

    store = an._read_store(TASK)
    _media, parts, _segments = an._eligible(TASK, vn.get_job(TASK))
    assert 1 in an._valid_parts(store, parts)


def test_a_note_saved_during_the_render_goes_back_onto_the_original_if_the_cut_fails(rendering, tmp_path):
    material = _material(tmp_path)
    cited = material["frames"][1]["filename"]
    an.save(TASK, note_markdown=f"# 笔记\n\n[00:55] 公式。\n\n![公式]({cited})", client_id="anonymous")
    state = rendering["state"]
    assert vn.visual_note_state(state["result"])["media_source"]["pictures_from_original"] is True

    state["result"]["debreath"] = {**state["result"]["debreath"], "status": dj.STATUS_FAILED, "stage": "failed"}
    moved = vn.put_back_onto_original(TASK, survey_cut.planned_ranges(state["result"]), client_id="anonymous")

    assert moved is True
    assert "[01:05]" in state["result"]["summary_markdown"]


def test_a_note_drafted_on_the_cut_clock_is_refused_once_the_cut_failed(rendering, tmp_path):
    """The common order: the render fails while the agent is still writing,
    so nothing was saved for the flow to move back; the save then comes."""
    _material(tmp_path)
    state = rendering["state"]
    state["result"]["debreath"] = {**state["result"]["debreath"], "status": dj.STATUS_FAILED, "stage": "failed"}

    with pytest.raises(an.AgentNoteError, match="剪后视频没有导出成功"):
        an.save(TASK, note_markdown="# 笔记\n\n[00:55] 公式。", client_id="anonymous")
    with pytest.raises(an.AgentNoteError, match="剪后视频没有导出成功"):
        an.save_part(TASK, part=1, note_markdown="# 笔记\n\n[00:55] 公式。", client_id="anonymous")
    assert state["result"]["summary_markdown"] == ""

    # Material fetched again is on the original, and a note from it is taken.
    again = an.material(TASK, client_id="anonymous", keyframe_extractor=_fake_extractor(tmp_path))
    assert "[01:05] 看这张幻灯片上的公式" in again["transcript"]
    an.save(TASK, note_markdown="# 笔记\n\n[01:05] 公式。", client_id="anonymous")
    assert state["result"]["summary_markdown"].startswith("# 笔记")


def test_a_later_render_that_did_not_decide_again_is_not_read_as_the_decided_one(rendering, tmp_path):
    """A decision left by a failed render stays for a restart to render again;
    a flip or a manual cut renders without rewriting it."""
    debreath = rendering["state"]["result"]["debreath"]
    debreath["started_at"] = "2026-10-27T11:30:00"

    assert survey_cut.ranges_while_rendering(rendering["state"]["result"]) == []
    with pytest.raises(an.AgentNoteError, match="去气口还在进行"):
        _material(tmp_path)


def test_without_a_decided_cut_the_note_still_waits_for_the_render(rendering, tmp_path):
    rendering["state"]["result"]["debreath"]["stage"] = "reviewing"

    with pytest.raises(an.AgentNoteError, match="去气口还在进行"):
        _material(tmp_path)


# --- the wait ----------------------------------------------------------------

_TOKEN = "local-secret-token"
_HEADERS = {"x-fluentflow-access-token": _TOKEN}


@pytest.fixture()
def jobs_db(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", _TOKEN)
    monkeypatch.setenv("FLUENTFLOW_DATA_DIR", str(tmp_path / "data"))
    db = tmp_path / "jobs.sqlite"
    monkeypatch.setattr(local_agent, "get_job", functools.partial(js.get_job, db_path=db))
    monkeypatch.setattr(local_agent, "_local_client_scope", lambda request: LOCAL_OWNER_ID)
    return db


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(agent_router)
    return TestClient(app)


def _store(db, task_id, *, debreath, summary="", stage="cut"):
    js.upsert_job(
        task_id=task_id, status="completed", stage=stage, client_id=LOCAL_OWNER_ID, summary_status="skipped",
        result={"task_id": task_id, "intelligence": "agent", "transcript_text": "转录。",
                "summary_markdown": summary, "summary_skipped": True, "debreath": debreath},
        db_path=db,
    )


_STARTED = "2026-10-27T10:00:00"
_DECIDED = {"started_at": _STARTED,
            survey_cut.DECIDED_KEY: {"render_started_at": _STARTED, "cuts": [[10.0, 20.0]], "keeps": CUT_LIST["keeps"]}}


def _wait(client, task_id, **payload):
    response = client.post(f"/agent/v1/tasks/{task_id}/wait", headers=_HEADERS, json=payload)
    assert response.status_code == 200
    return response.json()


@pytest.fixture()
def cut_open(monkeypatch):
    """The cut step still open, as while a task renders."""
    from backend.core import task_activity

    real = task_activity.activity

    def activity(job):
        current = real(job)
        if (job.get("result") or {}).get("debreath", {}).get("status") == dj.STATUS_RUNNING:
            from dataclasses import replace
            return replace(current, busy=True, open_steps=(*current.open_steps, "cut"))
        return current

    monkeypatch.setattr(local_agent.task_activity, "activity", activity)


def test_a_wait_returns_at_once_when_the_note_can_start(jobs_db, cut_open):
    _store(jobs_db, "t-ready", debreath={"status": dj.STATUS_RUNNING, "stage": "rendering", **_DECIDED})

    started = time.monotonic()
    waited = _wait(_client(), "t-ready", timeout_seconds=30, poll_interval_seconds=0.5)

    assert time.monotonic() - started < 5
    assert waited["done"] is False
    assert waited["agent_note"]["status"] == "ready"
    assert "get_agent_note_material" in waited["agent_note"]["next"]


def test_the_note_can_start_is_said_once_even_if_the_material_never_comes(jobs_db, cut_open):
    """A subagent not yet started, or material that failed, must not make
    every later wait return at once for the whole render."""
    _store(jobs_db, "t-once", debreath={"status": dj.STATUS_RUNNING, "stage": "rendering", **_DECIDED})
    client = _client()

    first = _wait(client, "t-once", timeout_seconds=1, poll_interval_seconds=0.5)
    second = _wait(client, "t-once", timeout_seconds=1, poll_interval_seconds=0.5)

    assert first["agent_note"]["status"] == "ready"
    assert "agent_note" not in second
    assert second["done"] is False and second["cut_running"] is True


def test_a_long_wait_returns_when_the_note_can_start(jobs_db, cut_open):
    _store(jobs_db, "t-later", debreath={"status": dj.STATUS_RUNNING, "stage": "reviewing"})
    timer = threading.Timer(1.0, _store, args=(jobs_db, "t-later"),
                            kwargs={"debreath": {"status": dj.STATUS_RUNNING, "stage": "rendering", **_DECIDED}})
    timer.start()
    try:
        started = time.monotonic()
        waited = _wait(_client(), "t-later", timeout_seconds=600, poll_interval_seconds=0.5)
    finally:
        timer.cancel()

    assert time.monotonic() - started < 10
    assert waited["agent_note"]["status"] == "ready"


def test_a_long_wait_returns_when_the_task_is_done(jobs_db, cut_open):
    _store(jobs_db, "t-done", debreath={"status": dj.STATUS_RUNNING, "stage": "reviewing"})
    timer = threading.Timer(1.0, _store, args=(jobs_db, "t-done"),
                            kwargs={"debreath": {"status": dj.STATUS_COMPLETED, "stage": "done"},
                                    "summary": "# 笔记", "stage": "done"})
    timer.start()
    try:
        started = time.monotonic()
        waited = _wait(_client(), "t-done", timeout_seconds=600, poll_interval_seconds=0.5)
    finally:
        timer.cancel()

    assert time.monotonic() - started < 10
    assert waited["done"] is True


def test_a_wait_may_hold_for_ten_minutes_and_no_longer(monkeypatch, jobs_db, cut_open):
    _store(jobs_db, "t-limit", debreath={"status": dj.STATUS_RUNNING, "stage": "reviewing"})
    held: list[float] = []
    clock = {"now": 0.0}

    async def fast_sleep(seconds):
        clock["now"] += seconds

    monkeypatch.setattr(local_agent.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(local_agent.asyncio, "sleep", fast_sleep)

    for asked in (300, 600, 3600):
        clock["now"] = 0.0
        waited = _wait(_client(), "t-limit", timeout_seconds=asked, poll_interval_seconds=10)
        held.append(clock["now"])
        assert waited["done"] is False and waited["task"]["status"] == "completed"

    assert held == [300, 600, 600]


def test_taking_material_during_the_tasks_own_render_is_not_refused_as_a_busy_queue(monkeypatch, jobs_db):
    _store(jobs_db, "t-own", debreath={"status": dj.STATUS_RUNNING, "stage": "rendering", **_DECIDED})
    monkeypatch.setattr(local_agent, "queue_is_busy", lambda: True)
    monkeypatch.setattr(local_agent.agent_note, "material", lambda task_id, **kw: {"task_id": task_id})

    response = _client().post("/agent/v1/tasks/t-own/agent-note/material", headers=_HEADERS, json={})

    assert response.status_code == 200


def test_another_tasks_work_still_keeps_the_material_waiting(monkeypatch, jobs_db):
    _store(jobs_db, "t-other", debreath={"status": dj.STATUS_COMPLETED, "stage": "done"})
    monkeypatch.setattr(local_agent, "queue_is_busy", lambda: True)

    response = _client().post("/agent/v1/tasks/t-other/agent-note/material", headers=_HEADERS, json={})

    assert response.status_code == 409


def test_after_a_cut_that_did_not_come_out_the_flow_moves_the_agents_note_back(monkeypatch):
    """The cut step of an agent's task (``_cut_then_note``, the one-after-the-
    other branch it takes) moves a note saved during the render back onto the
    original when the render did not come out, before anything else follows."""
    import asyncio
    import types

    from backend.core import task_flow

    order = []
    failed = {"debreath": {"status": dj.STATUS_FAILED, **_DECIDED}}

    async def cut(ctx, **kw):
        order.append("cut")

    async def note(*a, **kw):
        order.append("note")

    monkeypatch.setattr(task_flow, "_note_can_start_before_the_cut", lambda ctx: False)
    monkeypatch.setattr(task_flow, "_trim_recorded_tail", cut)
    monkeypatch.setattr(task_flow, "_write_note_after_transcript", note)
    monkeypatch.setattr(task_flow, "get_job", lambda task_id, **kw: {"result": failed})
    monkeypatch.setattr(task_flow, "_note_back_onto_original_if_uncut",
                        lambda task_id, client_id, ranges: order.append(("back", task_id, ranges)))

    asyncio.run(task_flow._cut_then_note(types.SimpleNamespace(task_id_value="t-flow", client_id=None), lambda: None))

    assert order == ["cut", ("back", "t-flow", cut_timeline.kept_ranges(CUT_LIST)), "note"]


def test_the_mcp_wait_stays_under_codexs_limit_by_default_and_never_past_ten_minutes(monkeypatch):
    """Codex gives one tool call 300 seconds unless tool_timeout_sec is raised
    (measured 2026-10-27): the default wait ends, answer included, before that."""
    import scripts.fluentflow_mcp_server as mcp

    calls = []
    monkeypatch.setattr(mcp, "_agent_request", lambda *a, **kw: calls.append(kw) or {"ok": True})
    monkeypatch.setattr(mcp, "_watching", lambda answer, *a: answer)

    mcp.wait_task("t")
    mcp.wait_task("t", timeout_seconds=3600)

    assert calls[0]["payload"]["timeout_seconds"] + 10 < 300 and calls[0]["timeout"] < 300
    assert calls[1]["payload"]["timeout_seconds"] == 600
    assert calls[1]["timeout"] > 600
