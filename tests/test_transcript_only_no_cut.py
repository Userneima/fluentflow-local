"""An agent that only wants the transcript submits with cut=false (2026-11-17,
reported by a 知识短片 conversation; 王愉超 had the fluentflow-local conversation
fix it). In the user's terms:

- A recording or link submitted with cut=false is transcribed and not cut:
  the task never waits for the agent to judge a cut, FluentFlow does not cut
  either, and the cut step reads skipped.
- The rest runs as for any uncut task: the note (when one is wanted) from the
  original, the compressed copy, Feishu, the output folder.
- Once the transcript is stored and the later steps ended, the task is done for
  an agent waiting on it.
- Submitting the task again, or carrying it on, still does not cut it.
- Left out, cut means true: the normal lecture case is cut as before (and
  save_agent_cut with empty lists still means "cut by the defaults").
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routers.local_agent as local_agent
from backend.core import agent_cut, job_store, task_activity, task_submission
from backend.core import task_flow as tf

from tests.test_task_steps import Flow, _ctx, _steps, _wire

_TOKEN = "no-cut-token"
_counter = iter(range(10_000))


@pytest.fixture()
def task_id():
    found = f"nocut-{next(_counter)}"
    yield found
    job_store.delete_jobs([found])


def _declined(task_id):
    job_store.upsert_job(task_id=task_id, status="queued", stage="queued", progress=0,
                         metadata={"cut_judge": "agent", "cut": False})


def _run(monkeypatch, task_id, *, note):
    flow = Flow(task_id, note=note)
    flow.note_gate.set()
    flow.render_gate.set()
    _wire(monkeypatch, flow)
    called = {"cut": 0, "agent": 0}
    real_survey = flow.survey_and_cut

    def survey(*args, **kwargs):
        called["cut"] += 1
        return real_survey(*args, **kwargs)

    monkeypatch.setattr(tf.survey_cut, "survey_and_cut", survey)
    # The real question "does the agent judge this cut?", which says yes for
    # cut_judge=agent; preparing the agent's material is where waiting begins.
    monkeypatch.setattr(tf, "_agent_judges_cut", _ORIGINAL_AGENT_JUDGES)
    monkeypatch.setattr(agent_cut, "prepare", lambda *_a: called.__setitem__("agent", called["agent"] + 1) or True)
    asyncio.run(asyncio.wait_for(tf.run_pipeline_then_note(asyncio.Event(), _ctx(task_id)), 5))
    return called


_ORIGINAL_AGENT_JUDGES = tf._agent_judges_cut


@pytest.mark.parametrize("note", [False, True])
def test_a_task_submitted_with_cut_false_is_not_cut_and_never_waits_for_the_agent(monkeypatch, task_id, note):
    _declined(task_id)
    called = _run(monkeypatch, task_id, note=note)

    assert called == {"cut": 0, "agent": 0}
    job = job_store.get_job(task_id)
    assert not agent_cut.is_waiting(job.get("result"))
    assert not task_activity.activity(job).busy
    assert local_agent._task_done(job)
    statuses = {name: step["status"] for name, step in _steps(task_id).items()}
    assert statuses["cut"] == "skipped"
    assert statuses["note"] == ("done" if note else "skipped")
    assert "running" not in statuses.values() and "pending" not in statuses.values()


def test_left_out_the_recording_is_cut_as_before(monkeypatch, task_id):
    job_store.upsert_job(task_id=task_id, status="queued", stage="queued", progress=0,
                         metadata={"cut_judge": "fluentflow"})
    called = _run(monkeypatch, task_id, note=False)
    assert called["cut"] == 1
    assert _steps(task_id)["cut"]["status"] == "done"


def test_carrying_on_after_a_restart_still_does_not_cut(monkeypatch, task_id):
    _declined(task_id)
    _run(monkeypatch, task_id, note=False)
    # Carried on from the cut step (a restart, or the user's 接着做).
    flow = Flow(task_id, note=False)
    _wire(monkeypatch, flow)
    called = []
    monkeypatch.setattr(tf.survey_cut, "survey_and_cut", lambda *a, **k: called.append(a))
    monkeypatch.setattr(tf, "_agent_judges_cut", _ORIGINAL_AGENT_JUDGES)
    monkeypatch.setattr(agent_cut, "prepare", lambda *_a: called.append("agent") or True)
    asyncio.run(asyncio.wait_for(
        tf.run_pipeline_then_note(asyncio.Event(), _ctx(task_id), start_at="cut"), 5,
    ))
    assert called == []
    assert _steps(task_id)["cut"]["status"] == "skipped"


def test_submitting_again_keeps_cut_false(monkeypatch):
    job = {"task_id": "t-nocut", "status": "failed", "source_type": "video_link",
           "metadata": {"cut_judge": "agent", "cut": False, "video_source_url": "https://example.com/v/1"}}
    monkeypatch.setattr(task_submission, "get_job", lambda *_a, **_k: job)
    monkeypatch.setattr(task_submission, "_in_place_origin", lambda _job: None)
    monkeypatch.setattr(task_submission, "find_source_file", lambda _task_id: None)
    seen = {}

    async def link_retry(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(task_submission, "_retry_link_task", link_retry)
    asyncio.run(task_submission.retry_task("t-nocut", client_id=None, local_caller=True))
    assert seen["carry"]["cut"] is False


def test_submitting_again_in_place_keeps_cut_false(monkeypatch):
    job = {"task_id": "t-nocut-path", "status": "failed", "source_type": "video_file",
           "metadata": {"folder_intake": {"original_path": "/x/talk.mp4", "cut_judge": "agent", "cut": False}}}
    monkeypatch.setattr(task_submission, "get_job", lambda *_a, **_k: job)
    seen = {}

    async def in_place(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(task_submission, "_retry_in_place", in_place)
    asyncio.run(task_submission.retry_task("t-nocut-path", client_id=None, local_caller=True))
    assert seen["origin"]["cut"] is False


def _app():
    app = FastAPI()
    app.include_router(local_agent.router)
    return app


def test_the_submit_routes_record_cut_false(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", _TOKEN)
    headers = {"x-fluentflow-access-token": _TOKEN}
    media = tmp_path / "talk.m4a"
    media.write_bytes(b"x")
    seen: dict = {}

    async def queued(source, **kwargs):
        seen["path"] = kwargs
        return {"task_id": "t-path", "status": "queued"}

    async def submit(**kwargs):
        seen["link"] = kwargs
        return {"task_id": "t-link", "status": "queued"}

    monkeypatch.setattr(local_agent, "request_is_localhost", lambda _request: True)
    monkeypatch.setattr(local_agent.local_folder_intake, "resolve_media_file", lambda raw: media)
    monkeypatch.setattr(local_agent, "queue_local_media_file", queued)
    monkeypatch.setattr(local_agent, "submit_video_source_job", submit)
    client = TestClient(_app())

    for options in ({"cut": False}, {"cut": "false"}):
        assert client.post("/agent/v1/tasks", headers=headers, json={
            "path": str(media), "input_type": "local_path", "options": options,
        }).status_code == 200
        assert seen["path"]["origin"]["cut"] is False
        assert "cut" not in seen["path"]["options"]
        assert client.post("/agent/v1/tasks", headers=headers, json={
            "input": "https://www.bilibili.com/video/BV1xx411c7mD", "options": options,
        }).status_code == 200
        assert seen["link"]["extra_metadata"]["cut"] is False
        assert "cut" not in seen["link"]["raw_options"]

    # Left out (or true): nothing recorded, the task is cut as before.
    client.post("/agent/v1/tasks", headers=headers, json={"path": str(media), "input_type": "local_path"})
    assert "cut" not in seen["path"]["origin"]
    client.post("/agent/v1/tasks", headers=headers, json={
        "input": "https://www.bilibili.com/video/BV1xx411c7mD", "options": {"cut": True},
    })
    assert "cut" not in seen["link"]["extra_metadata"]


def test_the_mcp_submit_tools_pass_cut(monkeypatch):
    import scripts.fluentflow_mcp_server as mcp
    from scripts.fluentflow_mcp import core

    sent = []
    monkeypatch.setattr(core, "_require_local_edition", lambda *_a: None)
    monkeypatch.setattr(core, "_agent_request", lambda *_a, **k: sent.append(k["payload"]) or {"ok": True})
    monkeypatch.setattr(core, "_watch", lambda *_a: None)

    mcp.submit_local_media("/x/talk.mp4", cut=False)
    mcp.submit_video_link("https://www.bilibili.com/video/BV1xx411c7mD", cut=False)
    mcp.submit_local_media("/x/talk.mp4")
    assert sent[0]["options"]["cut"] == "false"
    assert sent[1]["options"]["cut"] == "false"
    assert "cut" not in sent[2]["options"]
    for name in ("submit_local_media", "submit_video_link"):
        assert "cut" in core._TOOLS[name][1]["inputSchema"]["properties"]


def test_the_note_of_a_task_not_cut_reads_the_original(monkeypatch, task_id, tmp_path):
    """The note (FluentFlow's picture note, or the agent's material) is
    written from the original recording, pictures included (Codex push
    review, 2026-11-17)."""
    from backend.core import visual_note_job as vn

    original = tmp_path / "talk.mp4"
    original.write_bytes(b"x")
    job_store.upsert_job(task_id=task_id, status="completed",
                         metadata={"cut_judge": "agent", "cut": False},
                         result={"task_id": task_id, "transcript_text": "大家好"})
    monkeypatch.setattr(vn, "find_source_file", lambda _task_id: original)
    media = vn.cut_media(task_id, job_store.get_job(task_id)["result"])
    assert media is not None and media.path == original and media.has_video and media.unchanged

    # A task that was not declined and has no cut yet still has none.
    other = f"{task_id}-cut"
    job_store.upsert_job(task_id=other, status="completed", metadata={"cut_judge": "agent"})
    try:
        assert vn.cut_media(other, {}) is None
    finally:
        job_store.delete_jobs([other])
