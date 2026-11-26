"""停下这一步 stops a note being written in parts at once.

Requirements (to-do 「点『停下这一步』后，写笔记还在接着请求模型」, 2026-11-26),
in the user's terms:

- Stopping the step while the first part is being written does not wait for
  the request in flight: the step ends within a second, not when the model
  answers.
- No later part is written or checked after the stop: nothing more is paid
  for.
- The step reads stopped (已停下), not cut off by a stopping service, and the
  task keeps the note it had.
- What was already answered is kept: carrying the step on reads it back and
  asks the model only for the parts it had not answered.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

import backend.core.claude_vision as cv
import backend.core.visual_note_job as vn
from backend.core import job_store as store, model_answers, model_usage, task_actions, task_activity, task_steps, task_stop
from backend.core.claude_vision import VisualNoteDraft
from test_task_resume import Edges, _ended, _in_place_task, _statuses, _until, _wire
from test_visual_note_job import TASK, _fake_extractor, job_store, no_ambient_key  # noqa: F401

NOTE = "# 最小二乘法\n\n## 一、定义\n\n" + "最小二乘法是一种拟合方法。\n" * 20


class SlowFirstPart:
    """A model whose first request does not come back until released; each
    request goes out the way a real exit sends it (``model_answers``)."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.release = threading.Event()
        self.first_out = threading.Event()

    def ask(self, what: str) -> str:
        def send() -> str:
            self.sent.append(what)
            if len(self.sent) == 1:
                self.first_out.set()
                self.release.wait(30)
            return NOTE

        return model_answers.remembered(model_answers.request_key("stop-test", what), send)


def test_stopping_mid_first_part_writes_and_checks_no_later_part(job_store, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cv, "MAX_TRANSCRIPT_CHARS", 20)
    model = SlowFirstPart()
    checked: list[str] = []

    def writer(transcript, frames, *, part=None, **_kwargs):
        markdown = model.ask(f"part {part.index}/{part.total}")
        return VisualNoteDraft(markdown=markdown, basis_note="", model="claude-x", frames_sent=list(frames),
                               transcript_chars=len(transcript))

    def checker(system, user):
        checked.append(user)
        return '{"points": []}'

    outcome: dict[str, BaseException] = {}

    def run() -> None:
        with task_stop.bound(TASK):
            try:
                vn.run_visual_note(TASK, client_id="anonymous", api_key="sk-ant-test-not-a-real-key",
                                   keyframe_extractor=_fake_extractor(tmp_path), writer=writer,
                                   checker=(checker, "text_model"), reviser=lambda s, u: NOTE)
            except BaseException as exc:  # noqa: BLE001 - asserted below
                outcome["error"] = exc

    task_stop.begin(TASK)
    try:
        note = threading.Thread(target=model_usage.carry(run), daemon=True)
        note.start()
        assert model.first_out.wait(5)
        stopped_at = time.monotonic()
        task_stop.request(TASK)
        note.join(5)
        assert not note.is_alive()
        assert time.monotonic() - stopped_at < 1.5, "the request in flight is not waited for"
    finally:
        model.release.set()
        task_stop.begin(TASK)

    assert isinstance(outcome.get("error"), task_stop.StepStopped)
    assert not isinstance(outcome["error"], model_usage.ServiceStopping)
    assert model.sent == ["part 1/" + model.sent[0].split("/")[1]], "no later part is asked for"
    assert int(model.sent[0].split("/")[1]) > 1, "the note was written in several parts"
    assert checked == [], "nothing is checked after the stop"
    state = vn.visual_note_state(job_store["result"])
    assert state["status"] == vn.STATUS_FAILED and state["error"] == task_steps.STOPPED_NOTE
    assert job_store["result"]["summary_markdown"] == "原来的文字笔记", "the task keeps the note it had"
    assert not vn.is_claimed(TASK)


def test_a_stopped_note_ends_at_once_and_carrying_on_asks_only_what_was_not_answered(monkeypatch, tmp_path):
    task_id = f"stop-note-{int(time.time() * 1000) % 1_000_000}"
    _in_place_task(task_id, tmp_path, status="queued", result={})
    edges = Edges(task_id)
    _wire(monkeypatch, edges)
    model = SlowFirstPart()
    parts = ["第一段", "第二段", "第三段"]

    def write_note(task_id, client_id, *, on_local_work_done=None, planned_ranges=None):
        """The note's parts, each a request, in the order the frame note
        writes them (``visual_note_job._run_visual_note``)."""
        if on_local_work_done:
            on_local_work_done()
        vn.claim(task_id)
        try:
            written = []
            for part in parts:
                model_usage.stop_if_asked()
                written.append(model.ask(part))
            from backend.core import job_store as jobs

            job = jobs.get_job(task_id) or {}
            result = {**(job.get("result") or {}), "summary_markdown": "\n".join(written), "summary_status": "completed"}
            jobs.update_job_result(task_id, result)
        finally:
            vn.release(task_id)

    monkeypatch.setattr(task_actions.task_flow.local_intake_flow, "write_note", write_note)

    async def scenario():
        await task_actions._carry_on(store.get_job(task_id), task_steps.FETCH, route="/test")
        assert await _until(lambda: model.first_out.is_set())
        stopped_at = time.monotonic()
        answer = await task_actions.stop_step(task_id, None)
        assert answer["stopped_step"]["step"] == task_steps.NOTE
        assert await _until(lambda: _ended(task_id) and not task_actions.JOB_EVENTS.is_running(task_id)
                            and not vn.is_claimed(task_id), tries=100)
        assert time.monotonic() - stopped_at < 1.5, "the note's request in flight is not waited for"

        activity = task_activity.activity(store.get_job(task_id))
        assert not activity.busy and activity.label == "已停下"
        assert activity.resume_from == task_steps.NOTE
        assert _statuses(task_id)[task_steps.NOTE] == task_steps.STOPPED
        assert store.get_job(task_id)["result"].get("summary_error") == task_steps.STOPPED_NOTE
        assert model.sent == ["第一段"], "no later part is asked for after the stop"

        # The first part's answer comes back after the stop: kept for the step.
        model.release.set()
        assert await _until(lambda: store.count_model_answers(task_id) == 1)

        answer = await task_actions.resume_task(task_id, None)
        assert answer["resume_from"]["step"] == task_steps.NOTE
        assert await _until(lambda: _ended(task_id) and not task_actions.JOB_EVENTS.is_running(task_id))

    try:
        asyncio.run(scenario())
    finally:
        model.release.set()
        store.delete_model_answers(task_id)
    assert model.sent == ["第一段", "第二段", "第三段"], "carrying on pays only for the parts not answered"
    assert _statuses(task_id)[task_steps.NOTE] == task_steps.DONE


@pytest.fixture(autouse=True)
def _no_stop_left(monkeypatch):
    yield
    task_stop.begin(TASK)
