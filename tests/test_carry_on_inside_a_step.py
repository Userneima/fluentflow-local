"""A step cut off halfway carries on without paying again for what it had.

Requirements (owner, 2026-11-06, docs/方案/2026-11-06-中断后接着做.md), in
the user's terms:

- A step stopped by the user, by the service stopping, or by a crash keeps
  the model answers it already got; carrying it on asks the model only for
  what it had not been answered yet.
- A step that ends by itself (done, skipped, or failed for a reason of its
  own) keeps nothing: asking for it again on request asks the model again.
- A step that failed because the model could not be reached keeps what it
  had been answered before.
- Work done on request outside a task's run (rewrite the note) keeps nothing.
- Stopping the task, or the service, ends the transcript correction within
  seconds, without waiting for requests already sent.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from backend.core import (
    glossary_correction, job_store, model_answers, model_usage, subtitle_correction as sc, task_steps, task_stop,
)

_counter = iter(range(10_000))


@pytest.fixture()
def task_id():
    found = f"carry-on-{next(_counter)}"
    yield found
    job_store.delete_model_answers(found)


class CountingModel:
    """Answers each request with its own text, counting what was sent."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def __call__(self, user: str) -> str:
        self.sent.append(user)
        return f"answer to {user} #{self.sent.count(user)}"


def _ask(model: CountingModel, user: str) -> str:
    return model_answers.remembered(model_answers.request_key("test", user), lambda: model(user))


def _run_step(task_id: str, step: str, requests: list[str], model: CountingModel) -> list[str]:
    task_steps.start(task_id, step)
    return [_ask(model, user) for user in requests]


def test_a_stopped_step_carried_on_reads_its_answers_and_asks_only_the_rest(task_id):
    model = CountingModel()
    first = _run_step(task_id, task_steps.NOTE, ["chapter 1", "chapter 2"], model)
    task_steps.finish(task_id, task_steps.NOTE, task_steps.STOPPED)

    again = _run_step(task_id, task_steps.NOTE, ["chapter 1", "chapter 2", "chapter 3"], model)

    assert again[:2] == first
    assert model.sent == ["chapter 1", "chapter 2", "chapter 3"]
    task_steps.finish(task_id, task_steps.NOTE, task_steps.DONE)
    assert job_store.count_model_answers(task_id) == 0


def test_answers_are_read_back_in_the_order_given(task_id):
    """A request asked twice (an unreadable reply asked again) gets its
    second answer the second time, as the first run did."""
    model = CountingModel()
    first = _run_step(task_id, task_steps.CORRECT, ["batch", "batch"], model)
    task_steps.finish(task_id, task_steps.CORRECT, task_steps.STOPPED)

    again = _run_step(task_id, task_steps.CORRECT, ["batch", "batch", "batch"], model)

    assert first == ["answer to batch #1", "answer to batch #2"]
    assert again == ["answer to batch #1", "answer to batch #2", "answer to batch #3"]
    assert len(model.sent) == 3


def test_a_step_cut_off_by_a_restart_keeps_its_answers(task_id):
    model = CountingModel()
    _run_step(task_id, task_steps.CUT, ["judge"], model)
    task_steps.finish(task_id, task_steps.CUT, task_steps.FAILED, task_steps.RESTART_ERROR)

    _run_step(task_id, task_steps.CUT, ["judge"], model)
    assert model.sent == ["judge"]


def test_the_service_stopping_keeps_the_answers(task_id, monkeypatch):
    model = CountingModel()
    _run_step(task_id, task_steps.CUT, ["judge"], model)
    monkeypatch.setattr(task_stop, "shutting_down", lambda: True)
    task_steps.finish(task_id, task_steps.CUT, task_steps.FAILED, "导出失败")
    monkeypatch.undo()

    _run_step(task_id, task_steps.CUT, ["judge"], model)
    assert model.sent == ["judge"]


@pytest.mark.parametrize("status,error", [
    (task_steps.DONE, None), (task_steps.SKIPPED, None), (task_steps.FAILED, "笔记是空的"),
])
def test_a_step_ended_by_itself_keeps_nothing(task_id, status, error):
    model = CountingModel()
    _run_step(task_id, task_steps.NOTE, ["chapter 1"], model)
    task_steps.finish(task_id, task_steps.NOTE, status, error)

    _run_step(task_id, task_steps.NOTE, ["chapter 1"], model)
    assert model.sent == ["chapter 1", "chapter 1"]


def test_a_step_failed_on_an_unreachable_model_keeps_what_it_had(task_id):
    model = CountingModel()
    task_steps.start(task_id, task_steps.NOTE)
    _ask(model, "chapter 1")

    def offline() -> str:
        raise ConnectionError("network is down")

    with pytest.raises(ConnectionError):
        model_answers.remembered(model_answers.request_key("test", "chapter 2"), offline)
    task_steps.finish(task_id, task_steps.NOTE, task_steps.FAILED, "网络断了")

    _run_step(task_id, task_steps.NOTE, ["chapter 1", "chapter 2"], model)
    assert model.sent == ["chapter 1", "chapter 2"]


def test_work_on_request_outside_the_run_keeps_nothing(task_id):
    model = CountingModel()
    with model_usage.scope(task_id, task_steps.NOTE):
        _ask(model, "rewrite")
        _ask(model, "rewrite")
    assert job_store.count_model_answers(task_id) == 0
    assert model.sent == ["rewrite", "rewrite"]


def test_the_exits_go_through_the_kept_answers(task_id, monkeypatch):
    """The text model's exit, with the fake model: a note step stopped and
    carried on records no second call for what it already asked."""
    from backend.core import ai_client

    monkeypatch.setenv("FLUENTFLOW_FAKE_MODEL", "1")
    client = SimpleNamespace(base_url="https://api.deepseek.com/v1")
    calls: list[dict] = []
    monkeypatch.setattr(job_store, "insert_model_call", lambda **row: calls.append(row))

    task_steps.start(task_id, task_steps.NOTE)
    first = ai_client._chat(client, "deepseek-chat", "写笔记", "第一章")
    task_steps.finish(task_id, task_steps.NOTE, task_steps.STOPPED)
    task_steps.start(task_id, task_steps.NOTE)
    again = ai_client._chat(client, "deepseek-chat", "写笔记", "第一章")
    ai_client._chat(client, "deepseek-chat", "写笔记", "第二章")
    task_steps.finish(task_id, task_steps.NOTE, task_steps.DONE)

    assert again == first
    assert len(calls) == 2


def test_stopping_does_not_wait_for_requests_already_sent():
    stop = threading.Event()
    release = threading.Event()

    def slow(item: int) -> int:
        release.wait(30)
        return item

    def check() -> None:
        if stop.is_set():
            raise sc.Cancelled("stopped")

    threading.Timer(0.3, stop.set).start()
    started = time.monotonic()
    with pytest.raises(sc.Cancelled):
        model_usage.stoppable_map(slow, [1, 2, 3], workers=2, check_stop=check)
    assert time.monotonic() - started < 3
    release.set()


def test_stoppable_map_keeps_order_and_raises_the_first_failure():
    assert model_usage.stoppable_map(lambda n: n * 2, [3, 1, 2], workers=2, check_stop=lambda: None) == [6, 2, 4]

    def fails(n: int) -> int:
        if n == 2:
            raise ValueError("two")
        return n

    with pytest.raises(ValueError):
        model_usage.stoppable_map(fails, [1, 2, 3], workers=3, check_stop=lambda: None)


def test_stopping_the_task_ends_the_glossary_correction_within_seconds(task_id):
    """The user's 停下 (or the service stopping) reaches the correction's
    threads: it returns cancelled while a request is still out."""
    release = threading.Event()
    segments = [{"start": float(n), "end": float(n) + 1, "text": f"第{n}句话"} for n in range(200)]

    def blocking_chat(system: str, user: str) -> str:
        release.wait(30)
        return '{"fixes": []}'

    task_stop.begin(task_id)
    stop = task_stop.either(task_id, threading.Event())
    threading.Timer(0.3, task_stop.request, args=(task_id,)).start()
    started = time.monotonic()
    outcome = glossary_correction.correct(segments, "GLM", blocking_chat, stop=stop)
    assert outcome.status == "cancelled"
    assert time.monotonic() - started < 3
    release.set()


def test_a_correction_stopped_under_the_pipeline_ends_the_run(task_id, monkeypatch):
    """A cancelled correction is not stored as if it had corrected the
    transcript: the run ends as cancelled."""
    from backend.core import media_job

    monkeypatch.setattr(glossary_correction, "enabled", lambda: True)
    monkeypatch.setattr(media_job, "_subtitle_chat", lambda *_a: (lambda s, u: "", "key"))
    monkeypatch.setattr(media_job.correction_notes, "for_task", lambda *_a: "")
    monkeypatch.setattr(glossary_correction, "correct", lambda segments, glossary, chat, stop=None: (
        glossary_correction.GlossaryCorrectionResult(status="cancelled", segments=segments, seconds=0.0)
    ))
    ctx = SimpleNamespace(task_id_value=task_id, client_id=None, source_type="local", source_filename="a.mp4",
                          event_route="/x", secret_resolver=None, loop=None)

    async def run() -> None:
        ctx.loop = asyncio.get_running_loop()
        await media_job._run_glossary_correction_stage(ctx, segments=[{"text": "x"}], ai_kwargs={})

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())


def test_a_key_written_note_carried_on_reads_its_answer_back(task_id, monkeypatch):
    """(Codex push review, 2026-11-06) The Claude key's note exit gives back
    the kept answer, not an empty one, when its step is carried on."""
    from backend.core import claude_vision

    monkeypatch.setenv("FLUENTFLOW_FAKE_MODEL", "1")
    calls: list[dict] = []
    monkeypatch.setattr(job_store, "insert_model_call", lambda **row: calls.append(row))
    monkeypatch.setattr(claude_vision, "require_available", lambda *_a, **_k: None)
    client = SimpleNamespace()

    task_steps.start(task_id, task_steps.NOTE)
    first = claude_vision.write_visual_note("讲者讲了一些东西。", [], api_key="sk-fake", model="claude-x", client=client)
    task_steps.finish(task_id, task_steps.NOTE, task_steps.STOPPED)
    task_steps.start(task_id, task_steps.NOTE)
    again = claude_vision.write_visual_note("讲者讲了一些东西。", [], api_key="sk-fake", model="claude-x", client=client)
    task_steps.finish(task_id, task_steps.NOTE, task_steps.DONE)

    assert again.markdown == first.markdown and again.markdown
    assert len(calls) == 1
