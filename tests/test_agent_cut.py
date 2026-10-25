"""The user's own agent judges what to cut (owner, 2026-10-16/17).

Requirements:
1. A task submitted by an agent waits after transcription for the agent to
   judge when the agent asked for it (cut_judge=agent), or when FluentFlow has
   no judge of its own (auto). FluentFlow's own judge is never asked while it
   waits. A task submitted from the page, or with cut_judge=fluentflow, does
   not wait. ``wait_task`` answers at once that it is waiting, instead of
   waiting on the agent that is waiting on it.
2. The agent reads the same timeline FluentFlow's model reads, with
   screenshots of the long gaps whose picture moved as files on this machine.
3. The agent's decision is cut under the same rules as the model's: a gap id
   not in the timeline is refused (the agent can fix it), removals taking more
   than a quarter of what was said are not applied but put to the user, and
   spoken words are not cut. The cut is rendered once with the decision.
4. Whoever judged writes the report: the agent organises the facts, and its
   organisation survives the user changing a cut later. A cut the agent judged
   is never organised by FluentFlow's model.
5. FluentFlow stops waiting and judges itself when the agent's conversation
   closes (its reports stop), when the material is not fetched in time, when
   no decision follows the material in time, or when the user asks on the
   page; it says why. A decision after that is refused.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import agent_cut, job_store, silence_cuts, survey_cut
from backend.core.local_request_scope import LOCAL_OWNER_ID
import backend.routers.local_agent as local_agent
import backend.routers.local_job_debreath as local_job_debreath
from backend.core import task_flow as tf, task_queue as tq, task_submission as ts  # noqa: F401

from tests.test_survey_cut import TAIL, _recording, _segments

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
_TOKEN = "local-secret-token"
_HEADERS = {"x-fluentflow-access-token": _TOKEN}


def _task(tmp_path: Path, *, choice: str | None = "agent", status: str = "completed") -> dict:
    folder = tmp_path / "周会"
    folder.mkdir(exist_ok=True)
    source = folder / "周会.mp4"
    if not source.exists() and shutil.which("ffmpeg"):
        _recording(source)
    task_id = f"ac-{uuid.uuid4().hex[:8]}"
    _created.append(task_id)
    intake = {"original_path": str(source)}
    if choice is not None:
        intake["cut_judge"] = choice
    segments = _segments()
    job_store.upsert_job(
        task_id=task_id, status=status, client_id=LOCAL_OWNER_ID,
        metadata={"folder_intake": intake},
        result={
            "raw_segments": segments, "display_segments": segments,
            "transcript_text": "\n".join(s["text"] for s in segments), "transcript_media": "source",
        },
    )
    return {"id": task_id, "source": source}


def _result(task_id: str) -> dict:
    return job_store.get_job(task_id)["result"]


@pytest.fixture
def no_pictures(monkeypatch):
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", _TOKEN)
    monkeypatch.setattr(local_agent, "_local_client_scope", lambda request: LOCAL_OWNER_ID)
    monkeypatch.setattr(local_job_debreath, "local_client_scope", lambda request: LOCAL_OWNER_ID)
    app = FastAPI()
    app.include_router(local_agent.router)
    app.include_router(local_job_debreath.router)
    return TestClient(app)


_created: list[str] = []


@pytest.fixture(autouse=True)
def fresh_beats():
    agent_cut._beats.clear()
    yield
    agent_cut._beats.clear()
    # Other tests list every task in the shared store (interrupted, waiting).
    job_store.delete_jobs(list(_created))
    _created.clear()


# --- 1. who judges --------------------------------------------------------

def test_who_waits_for_the_agent(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(agent_cut.cut_judge, "choose", lambda *a, **k: asked.append(1) or None)
    assert agent_cut.should_wait(_task(tmp_path, choice="agent")["id"], LOCAL_OWNER_ID) is True
    assert agent_cut.should_wait(_task(tmp_path, choice="auto")["id"], LOCAL_OWNER_ID) is True
    assert agent_cut.should_wait(_task(tmp_path, choice="fluentflow")["id"], LOCAL_OWNER_ID) is False
    # From the page: no choice recorded, FluentFlow judges as before.
    assert agent_cut.should_wait(_task(tmp_path, choice=None)["id"], LOCAL_OWNER_ID) is False
    # A task that did not transcribe has nothing to judge.
    assert agent_cut.should_wait(_task(tmp_path, status="failed")["id"], LOCAL_OWNER_ID) is False

    monkeypatch.setattr(agent_cut.cut_judge, "choose", lambda *a, **k: object())
    assert agent_cut.should_wait(_task(tmp_path, choice="auto")["id"], LOCAL_OWNER_ID) is False
    assert agent_cut.should_wait(_task(tmp_path, choice="agent")["id"], LOCAL_OWNER_ID) is True


def test_an_unknown_choice_is_refused_at_submission(api, tmp_path):
    response = api.post("/agent/v1/tasks", headers=_HEADERS, json={
        "path": str(tmp_path / "a.mp4"), "options": {"cut_judge": "someone"},
    })
    assert response.status_code == 400 and "cut_judge" in response.json()["detail"]


@needs_ffmpeg
def test_a_waiting_task_says_so_and_wait_returns_at_once(api, tmp_path, no_pictures, monkeypatch):
    task = _task(tmp_path)
    monkeypatch.setattr(survey_cut.cut_judge, "choose", lambda *a, **k: pytest.fail("FluentFlow's judge was asked"))
    assert agent_cut.prepare(task["id"], LOCAL_OWNER_ID) is True

    waited = api.post(f"/agent/v1/tasks/{task['id']}/wait", headers=_HEADERS, json={"timeout_seconds": 30}).json()
    assert waited["done"] is False and waited["agent_cut"]["status"] == "waiting"
    assert "get_agent_cut_material" in waited["agent_cut"]["next"]
    got = api.get(f"/agent/v1/tasks/{task['id']}", headers=_HEADERS).json()
    assert got["task"]["agent_cut"]["status"] == "waiting"
    listed = job_store.list_jobs(limit=50)
    row = next(job for job in listed if job["task_id"] == task["id"])
    assert row["result"]["agent_cut"]["status"] == "waiting"
    assert _result(task["id"]).get("debreath") is None  # nothing cut yet


# --- 2. the material ------------------------------------------------------

@needs_ffmpeg
def test_the_agent_reads_what_the_model_reads_with_screenshots_as_files(api, tmp_path, monkeypatch):
    task = _task(tmp_path)
    # The picture moves in the second gap.
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {1: 0.5})
    agent_cut.prepare(task["id"], LOCAL_OWNER_ID)

    material = api.get(f"/agent/v1/tasks/{task['id']}/agent-cut/material", headers=_HEADERS).json()

    seen = {}

    def look(system, user, images):
        seen["user"], seen["images"] = user, images
        return '{"keep_gaps": [], "remove": []}'

    # The plan FluentFlow's own judge is shown: loudness, spoken words kept.
    plan = agent_cut.debreath_job.volume_plan(task["source"])
    spoken, _ = survey_cut.spoken_words(task["source"], plan, survey_cut.speech_spans(_result(task["id"])))
    plan, _ = survey_cut.protect_words(plan, spoken)
    survey_cut.revise_plan(task["source"], plan, _segments(), None, look=look,
                           pictures=lambda source, gaps: {1: 0.5})
    assert material["timeline"] == seen["user"]
    assert material["instructions"].startswith(survey_cut.SYSTEM)
    assert [shot["label"] for shot in material["shots"]] == [label for label, _ in seen["images"]]
    assert len(material["shots"]) == 2 and all(Path(shot["path"]).is_file() for shot in material["shots"])
    assert _result(task["id"])["agent_cut"].get("material_at")


# --- 3. the decision ------------------------------------------------------

@needs_ffmpeg
def test_the_decision_is_cut_once_under_the_same_rules(api, tmp_path, no_pictures):
    task = _task(tmp_path)
    agent_cut.prepare(task["id"], LOCAL_OWNER_ID)

    bad = api.post(f"/agent/v1/tasks/{task['id']}/agent-cut", headers=_HEADERS,
                   json={"keep_gaps": [{"id": 9, "reason": "?"}], "remove": []})
    assert bad.status_code == 409 and "9" in bad.json()["detail"]
    assert agent_cut.is_waiting(_result(task["id"]))

    import json
    answer = json.loads(TAIL)
    saved = api.post(f"/agent/v1/tasks/{task['id']}/agent-cut", headers=_HEADERS,
                     json={**answer, "model": "claude-test"}).json()
    assert saved["kept_gaps"] == 1 and saved["removed"] == 1 and "[r1]" in saved["facts"]

    state = _result(task["id"])["agent_cut"]
    record = survey_cut.survey_and_cut(task["id"], decide=agent_cut.decider(state), judged_by=state["judged_by"])

    result = _result(task["id"])
    assert record["judged_by"] == "agent:claude-test"
    # The same cut as when the model gave this answer
    # (test_survey_cut.test_the_recording_is_cut_once_and_the_transcript_moves_onto_the_cut).
    media = Path(result["debreath"]["delivered_path"])
    assert silence_cuts.media_duration_seconds(media) == pytest.approx(42.8, abs=1.0)
    report = result["debreath"]["survey"]["report"]
    assert report["sections"][0]["title"] == "需要你判断"
    assert report["organized_by"] is None  # grouped by kind until the agent's arrives


@needs_ffmpeg
def test_removals_taking_too_much_talk_are_put_to_the_user(api, tmp_path, no_pictures):
    task = _task(tmp_path)
    agent_cut.prepare(task["id"], LOCAL_OWNER_ID)

    saved = api.post(f"/agent/v1/tasks/{task['id']}/agent-cut", headers=_HEADERS, json={
        "keep_gaps": [], "remove": [{"from": 14.5, "to": None, "reason": "?"}],
    }).json()

    assert saved["removed"] == 0 and saved["removal_refused"]
    assert "[x1]" in saved["facts"]
    state = _result(task["id"])["agent_cut"]
    survey_cut.survey_and_cut(task["id"], decide=agent_cut.decider(state), judged_by=state["judged_by"])
    report = _result(task["id"])["debreath"]["survey"]["report"]
    judge = report["sections"][0]
    assert judge["title"] == "需要你判断" and [item["kind"] for item in judge["items"]] == ["refused_removal"]
    assert "模型" not in judge["items"][0]["doubt"]


# --- 4. the report --------------------------------------------------------

@needs_ffmpeg
def test_the_agent_writes_the_report_and_it_survives_a_change(api, tmp_path, no_pictures, monkeypatch):
    task = _task(tmp_path)
    agent_cut.prepare(task["id"], LOCAL_OWNER_ID)
    import json
    api.post(f"/agent/v1/tasks/{task['id']}/agent-cut", headers=_HEADERS, json=json.loads(TAIL))
    state = _result(task["id"])["agent_cut"]
    survey_cut.survey_and_cut(task["id"], decide=agent_cut.decider(state), judged_by=state["judged_by"])

    report = {"summary": "开头结尾都干净了。", "sections": [
        {"title": "去掉的会后闲聊", "folded": False, "items": [{"ref": "r1", "text": "说完拜拜后多录的，去掉。"}]},
        {"title": "留着的空白", "folded": False, "items": [
            {"ref": "g2", "text": "讲者让大家读表格，留着。", "unsure": True, "doubt": "不确定表格要看多久"},
        ]},
    ]}
    sent = api.post(f"/agent/v1/tasks/{task['id']}/agent-cut/report", headers=_HEADERS, json=report)
    assert sent.status_code == 200
    # The count the agent tells the user includes what the rules always ask
    # (the removed talk), not only what the agent was unsure of.
    assert sent.json()["to_judge"] == 2

    shown = _result(task["id"])["debreath"]["survey"]["report"]
    assert shown["summary"] == "开头结尾都干净了。" and shown["organized_by"] == "agent"
    # What the agent was unsure of, and what the rules always ask, is put
    # to the user in the agent's words.
    judged = shown["sections"][0]
    assert judged["title"] == "需要你判断"
    texts = {item["text"]: item.get("doubt") for item in judged["items"]}
    assert texts.get("说完拜拜后多录的，去掉。") and texts.get("讲者让大家读表格，留着。") == "不确定表格要看多久"

    # FluentFlow's model is never asked to organise it, also after a change.
    monkeypatch.setattr(survey_cut, "_default_chat", lambda: (lambda s, u: pytest.fail("model asked"), "fake"))
    first_gap = next(item for section in shown["sections"] for item in section["items"] if item["kind"] == "long_gap"
                     and item["action"] == "removed")
    survey_cut.flip(task["id"], first_gap["ref"], LOCAL_OWNER_ID)
    after = _result(task["id"])["debreath"]["survey"]["report"]
    assert after["summary"] == "开头结尾都干净了。" and after["organized_by"] == "agent"
    assert any(item["text"] == "说完拜拜后多录的，去掉。" for section in after["sections"] for item in section["items"])


# --- 5. not waiting forever ---------------------------------------------------

@needs_ffmpeg
def test_fluentflow_judges_when_the_agent_is_gone_or_late(tmp_path, no_pictures, monkeypatch):
    closed = _task(tmp_path)
    agent_cut.prepare(closed["id"], LOCAL_OWNER_ID)
    agent_cut.beat([closed["id"]], LOCAL_OWNER_ID)
    assert agent_cut.check(closed["id"], LOCAL_OWNER_ID) is None
    agent_cut._beats[closed["id"]] -= agent_cut.HEARTBEAT_STALE_SECONDS + 1
    state = agent_cut.check(closed["id"], LOCAL_OWNER_ID)
    assert state["status"] == "fallback" and "关掉" in state["fallback_reason"]
    with pytest.raises(agent_cut.AgentCutError):
        agent_cut.decide(closed["id"], LOCAL_OWNER_ID, {"keep_gaps": [], "remove": []})

    never_came = _task(tmp_path)
    agent_cut.prepare(never_came["id"], LOCAL_OWNER_ID)
    monkeypatch.setattr(agent_cut, "FETCH_LIMIT_SECONDS", -1.0)
    assert "没来取" in agent_cut.check(never_came["id"], LOCAL_OWNER_ID)["fallback_reason"]

    slow = _task(tmp_path)
    agent_cut.prepare(slow["id"], LOCAL_OWNER_ID)
    agent_cut.material(slow["id"], LOCAL_OWNER_ID)
    assert agent_cut.check(slow["id"], LOCAL_OWNER_ID) is None
    agent_cut._save(slow["id"], LOCAL_OWNER_ID, lambda state, _r: state.update(decision_limit_seconds=-1))
    assert "没交回决定" in agent_cut.check(slow["id"], LOCAL_OWNER_ID)["fallback_reason"]


def test_the_time_limit_grows_with_the_recording():
    assert agent_cut.decision_limit(600) == agent_cut.DECIDE_MIN_SECONDS
    assert agent_cut.decision_limit(4 * 3600) == 4 * agent_cut.DECIDE_SECONDS_PER_HOUR


@needs_ffmpeg
def test_the_user_can_let_fluentflow_judge_from_the_page(api, tmp_path, no_pictures):
    task = _task(tmp_path)
    agent_cut.prepare(task["id"], LOCAL_OWNER_ID)

    assert api.post(f"/jobs/{task['id']}/agent-cut/self-judge").status_code == 200
    state = agent_cut.check(task["id"], LOCAL_OWNER_ID)
    assert state["status"] == "fallback" and "网页" in state["fallback_reason"]
    assert api.post(f"/jobs/{task['id']}/agent-cut/self-judge").status_code == 409
    late = api.post(f"/agent/v1/tasks/{task['id']}/agent-cut", headers=_HEADERS, json={"keep_gaps": [], "remove": []})
    assert late.status_code == 409 and "FluentFlow 自己判断" in late.json()["detail"]


def test_after_waiting_the_task_is_cut_with_the_decision_or_by_fluentflow(monkeypatch):
    calls = []

    async def cut_then_note(step, hand_off, **cut):
        calls.append(cut)
        hand_off()

    states = iter([None, {"status": "decided", "judged_by": "agent:x", "decision": {"kept_gaps": [], "removed": []}}])
    monkeypatch.setattr(tf.agent_cut, "check", lambda *a: next(states))
    monkeypatch.setattr(tf.agent_cut, "finished", lambda *a: None)
    monkeypatch.setattr(tf, "AGENT_CUT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(tf, "_cut_then_note", cut_then_note)
    monkeypatch.setattr(tf.output_folder, "schedule_sync", lambda task_id: None)
    asyncio.run(tf.continue_after_agent("t-1", None))
    assert calls[0]["judged_by"] == "agent:x" and callable(calls[0]["decide"])

    monkeypatch.setattr(tf.agent_cut, "check",
                        lambda *a: {"status": "fallback", "fallback_reason": "agent 的对话已经关掉了"})
    asyncio.run(tf.continue_after_agent("t-2", None))
    assert calls[1] == {"extra": {"agent_fallback": "agent 的对话已经关掉了"}}


def test_the_heartbeat_keeps_only_tasks_still_on_their_way(tmp_path):
    running = _task(tmp_path, status="running")["id"]
    done = _task(tmp_path, status="completed")["id"]
    assert agent_cut.beat([running, done, "missing"], LOCAL_OWNER_ID) == [running]
    assert running in agent_cut._beats and "missing" not in agent_cut._beats
