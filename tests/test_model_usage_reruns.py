"""A step's work done again on request is recorded against that task and step.

Requirement (owner, 2026-10-25 plan, 模型用量记账): cutting again, turning a
cut decision around or undoing it, rewriting the note, running the word-list
correction again, and an agent writing or saving the note all call models
outside the task's own run. Each is reached here with nothing in the context,
as a request or a background thread reaches it, and its model call has to land
on the task and the step it is that step's work for.

Each entry's first dependency is replaced by one that spends a model call and
then ends the entry early; the call is recorded by the real ``model_usage``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import agent_note, debreath_job, job_store, model_usage, survey_cut, tail_trim, visual_note_job
from backend.core.local_request_scope import LOCAL_OWNER_ID
import backend.routers.local_agent as local_agent

_counter = iter(range(10_000))
TOKEN = "rerun-token"
AGENT = {"x-fluentflow-access-token": TOKEN, "x-fluentflow-client-id": LOCAL_OWNER_ID}


@pytest.fixture()
def task_id():
    task = f"rerun-{next(_counter)}"
    job_store.upsert_job(
        task_id=task, status="completed", client_id=LOCAL_OWNER_ID, stage="done", progress=100,
        result={
            "task_id": task, "transcript_text": "今天讲三件事",
            "raw_segments": [{"start": 0.0, "end": 2.0, "text": "今天讲三件事"}],
            "debreath": {"used_for_transcription": True},
            "summary_markdown": "# 笔记", "summary_status": "completed",
        },
    )
    yield task
    job_store.delete_jobs([task])


class Stop(Exception):
    pass


def _spend(*_args, **_kwargs):
    """A model call, then the entry ends here."""
    with model_usage.call("deepseek", "deepseek-flash") as call:
        call.input_tokens, call.output_tokens = 100, 10
    raise Stop()


def _recorded(task_id):
    return [(row["step"], row["task_id"]) for row in job_store.list_model_calls(task_id)]


def _ends(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except (Stop, debreath_job.DebreathError, survey_cut.ReviewError, visual_note_job.VisualNoteError):
        pass


def test_cutting_again_is_the_cut_step(monkeypatch, task_id):
    monkeypatch.setattr(debreath_job, "_run_debreath", _spend)
    _ends(debreath_job.run_debreath, task_id)
    assert _recorded(task_id) == [("cut", task_id)]


def test_the_cut_judged_again_is_the_cut_step(monkeypatch, task_id):
    monkeypatch.setattr(survey_cut.debreath_job, "resolve_source", _spend)
    monkeypatch.setattr(survey_cut.debreath_job, "debreath_state", lambda _result: {})
    _ends(survey_cut.survey_and_cut, task_id)
    assert _recorded(task_id) == [("cut", task_id)]


def test_where_the_recording_ends_judged_again_is_the_cut_step(monkeypatch, task_id):
    monkeypatch.setattr(tail_trim, "_media_path", _spend)
    _ends(tail_trim.trim_tail, task_id)
    assert _recorded(task_id) == [("cut", task_id)]


@pytest.mark.parametrize("entry", ["flip", "undo_change"])
def test_turning_a_cut_decision_around_or_undoing_it_is_the_cut_step(monkeypatch, task_id, entry):
    monkeypatch.setattr(survey_cut, "_task_survey", _spend)
    _ends(getattr(survey_cut, entry), task_id, "k1" if entry == "flip" else 0)
    assert _recorded(task_id) == [("cut", task_id)]


def test_rewriting_the_note_from_the_frames_is_the_note_step(monkeypatch, task_id):
    monkeypatch.setattr(visual_note_job, "_run_visual_note", _spend)
    _ends(visual_note_job.run_visual_note, task_id)
    assert _recorded(task_id) == [("note", task_id)]


@pytest.mark.parametrize("entry", ["save", "save_part"])
def test_an_agent_saving_its_note_is_the_note_step(monkeypatch, task_id, entry):
    monkeypatch.setattr(agent_note, "_job", _spend)
    kwargs = {"note_markdown": "# 笔记"} if entry == "save" else {"part": 1, "note_markdown": "# 笔记"}
    _ends(getattr(agent_note, entry), task_id, **kwargs)
    assert _recorded(task_id) == [("note", task_id)]


@pytest.fixture()
def agent(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", TOKEN)
    app = FastAPI()
    app.include_router(local_agent.router)
    return TestClient(app, raise_server_exceptions=False)


def test_an_agent_rewriting_the_note_with_the_text_model_is_the_note_step(monkeypatch, agent, task_id):
    def summarize(_transcript, **_kwargs):
        try:
            _spend()
        except Stop:
            pass
        return SimpleNamespace(markdown="# 新笔记", requested_mode="auto", resolved_mode="direct", chunk_count=1)

    monkeypatch.setattr(local_agent, "summarize_transcript_with_metadata", summarize)
    agent.post(f"/agent/v1/tasks/{task_id}/note/regenerate", headers=AGENT, json={})
    assert _recorded(task_id) == [("note", task_id)]


def test_running_the_word_list_correction_again_is_the_correction_step(monkeypatch, agent, task_id):
    def chat(_system, _user):
        with model_usage.call("deepseek", "deepseek-flash") as call:
            call.input_tokens, call.output_tokens = 100, 10
        return '{"fixes": []}'

    monkeypatch.setattr(local_agent, "_glossary_chat", lambda: (chat, "text_model:deepseek-flash"))
    agent.post(f"/agent/v1/tasks/{task_id}/glossary-corrections/run", headers=AGENT)
    rows = _recorded(task_id)
    assert rows and set(rows) == {("correct", task_id)}
