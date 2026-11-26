"""A task is finished only when every one of its steps has ended.

Requirements (owner, 2026-10-18 and 2026-10-25, docs/方案/2026-10-25-任务步骤清单和模型用量记账.md),
in the user's terms:

- A task records its steps: fetching the recording, transcription, correcting
  the transcript, cut, note, compressed copy, Feishu export. Each one is not started, in progress, done, failed, skipped or
  stopped.
- While any step is still to come or in progress, the task is still working:
  it cannot be deleted, an agent waiting for it is not done, the restart guard
  counts it, and the task list says what it is doing. The transcript is
  readable all along.
- A task that only wanted a transcript is finished once the transcript is
  stored.
- A failed step ends the task with that failure named; an agent waiting for
  it is answered at once.
- No step stays "in progress" once nothing runs it: a step that raised, a task
  that was stopped, a service that was restarted.
- A task waiting for the user's agent to judge its cut is not counted as busy
  (the agent would wait on itself), and a restart leaves it waiting.
- A cut or note started again on a finished task still counts.
- Starting a task, or carrying it on, records its steps in one write: each
  write waits for the disk, measured at tens of milliseconds on Windows, and
  the queue does it on the loop that answers the page and agents.
"""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import job_store, task_activity, task_steps
from backend.routers.agent_v1 import common
from backend.routers import local_processing as lp
from backend.core import task_flow as tf, task_queue as tq, task_submission as ts  # noqa: F401

_GUARD_PATH = Path(__file__).resolve().parents[1] / "launchers" / "macos" / "count_active_jobs.py"
_spec = importlib.util.spec_from_file_location("count_active_jobs", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

_counter = iter(range(10_000))


@pytest.fixture()
def task_id():
    return f"steps-{next(_counter)}"


def _job(task_id):
    return job_store.get_job(task_id)


def _patch(task_id, **changes):
    job = _job(task_id) or {}
    result = dict(job.get("result") or {})
    result.update(changes)
    job_store.update_job_result(task_id, result)


def _steps(task_id):
    return {
        str(step["step_type"]).split(":", 1)[1]: step
        for step in task_steps.steps_of([task_id]).get(task_id, [])
    }


def _busy_everywhere(task_id) -> dict[str, bool]:
    job = _job(task_id)
    activity = task_activity.activity(job)
    return {
        "activity": activity.busy,
        "delete_refused": task_activity.busy_reason(job) is not None,
        "agent_not_done": not common._task_done(job),
        "restart_guard": guard.is_busy({**job, "activity": activity.as_dict()}),
    }


class Flow:
    """The task's real flow, with the pipeline, the cut and the note faked at
    the edges: each fake writes what the real one writes to the result."""

    def __init__(self, task_id, *, note=True, cut=True, note_outcome="completed"):
        self.task_id = task_id
        self.note = note
        self.cut = cut
        self.note_outcome = note_outcome
        self.render_gate = threading.Event()
        self.note_gate = threading.Event()
        self.raise_in_cut = False
        self.raise_in_note = False

    async def pipeline(self, ctx):
        job_store.upsert_job(task_id=self.task_id, status="completed", stage="done", progress=100,
                             result={"task_id": self.task_id, "transcript_text": "大家好"})

    def survey_and_cut(self, task_id, client_id, on_plan=None):
        if self.raise_in_cut:
            raise RuntimeError("ffmpeg went away")
        if not self.cut:
            return
        _patch(task_id, debreath={"status": "running"})
        self.render_gate.wait(5)
        _patch(task_id, debreath={"status": "completed"})

    def write_note(self, task_id, client_id, *, on_local_work_done=None, planned_ranges=None):
        if on_local_work_done:
            on_local_work_done()
        if self.raise_in_note:
            raise RuntimeError("Claude went away")
        self.note_gate.wait(5)
        if self.note_outcome == "completed":
            _patch(task_id, summary_markdown="# 笔记", summary_status="completed")
        else:
            _patch(task_id, summary_markdown="", summary_status="failed", summary_error="额度用完了")


def _wire(monkeypatch, flow):
    monkeypatch.setattr(tf, "_run_pipeline", flow.pipeline)
    monkeypatch.setattr(tf, "_agent_judges_cut", _never)
    monkeypatch.setattr(tf.local_intake_flow, "survey_first_enabled", lambda: True)
    monkeypatch.setattr(tf.local_intake_flow, "note_is_wanted", lambda *_a: flow.note)
    monkeypatch.setattr(tf.local_intake_flow, "mark_note_running", lambda *_a: None)
    monkeypatch.setattr(tf.local_intake_flow, "write_note", flow.write_note)
    monkeypatch.setattr(tf.local_intake_flow, "compress_original", lambda *_a: None)
    monkeypatch.setattr(tf.survey_cut, "survey_and_cut", flow.survey_and_cut)
    monkeypatch.setattr(tf.visual_note_channel, "resolve_channel",
                        lambda *_a, **_k: SimpleNamespace(available=False))
    monkeypatch.setattr(tf, "_deliver_to_output_folder", _nothing)


async def _never(_ctx):
    return False


async def _nothing(_ctx):
    return None


async def _until(predicate, tries=250):
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


# A whole flow with fakes at its edges: seconds on this machine, and on the
# GitHub Windows machine each write to the task store waits on the disk (33 ms
# median, up to 400, decisions 8.27 and 8.30), so a fixed 5 s budget failed
# there now and then. A budget only has to catch a hang; the tests that assert
# something happened wait for that state instead.
FLOW_SECONDS = 30


def _ctx(task_id, *, lark=False):
    return SimpleNamespace(task_id_value=task_id, client_id=None, do_lark=lark,
                           note_deferred_to_visual_note=True)


def _queued(task_id):
    job_store.upsert_job(task_id=task_id, status="queued", stage="queued", progress=0)


def test_while_the_cut_and_note_are_still_coming_the_task_is_busy_everywhere(monkeypatch, task_id):
    _queued(task_id)
    flow = Flow(task_id)
    _wire(monkeypatch, flow)

    async def scenario():
        runner = asyncio.create_task(tf.run_pipeline_then_note(asyncio.Event(), _ctx(task_id)))
        assert await _until(lambda: (_steps(task_id).get("cut") or {}).get("status") == "running")
        # The transcript is the user's already; the task is not finished.
        assert _job(task_id)["result"]["transcript_text"] == "大家好"
        assert all(_busy_everywhere(task_id).values()), _busy_everywhere(task_id)
        assert task_activity.activity(_job(task_id)).label == "正在剪辑"
        assert common._cut_running(_job(task_id))

        flow.render_gate.set()
        assert await _until(lambda: (_steps(task_id).get("note") or {}).get("status") == "running")
        assert all(_busy_everywhere(task_id).values())
        assert common._note_pending(_job(task_id))
        assert task_activity.busy_reason(_job(task_id)) == "这个任务的笔记还在写，等写完再删。"

        flow.note_gate.set()
        await asyncio.wait_for(runner, FLOW_SECONDS)

    asyncio.run(scenario())
    assert not any(_busy_everywhere(task_id).values())
    steps = _steps(task_id)
    assert {name: step["status"] for name, step in steps.items()} == {
        "fetch": "done", "transcribe": "done", "correct": "done",
        "cut": "done", "note": "done", "compress": "skipped", "feishu": "skipped",
    }
    assert steps["cut"]["started_at"] and steps["cut"]["finished_at"]


def test_a_task_that_only_wanted_a_transcript_is_finished_once_it_is_stored(monkeypatch, task_id):
    _queued(task_id)
    flow = Flow(task_id, note=False, cut=False)
    _wire(monkeypatch, flow)

    asyncio.run(asyncio.wait_for(tf.run_pipeline_then_note(asyncio.Event(), _ctx(task_id)), FLOW_SECONDS))

    assert not any(_busy_everywhere(task_id).values())
    assert {name: step["status"] for name, step in _steps(task_id).items()} == {
        "fetch": "done", "transcribe": "done", "correct": "done",
        "cut": "skipped", "note": "skipped", "compress": "skipped", "feishu": "skipped",
    }


def test_a_failed_note_ends_the_task_and_names_the_failure(monkeypatch, task_id):
    _queued(task_id)
    flow = Flow(task_id, note_outcome="failed")
    flow.render_gate.set()
    flow.note_gate.set()
    _wire(monkeypatch, flow)

    asyncio.run(asyncio.wait_for(tf.run_pipeline_then_note(asyncio.Event(), _ctx(task_id)), FLOW_SECONDS))

    activity = task_activity.activity(_job(task_id))
    assert not activity.busy
    assert activity.failed_steps == ("note",)
    assert _steps(task_id)["note"]["error_reason"] == "额度用完了"
    assert common._task_done(_job(task_id))


def test_a_cut_that_raises_is_failed_and_the_task_still_ends(monkeypatch, task_id):
    _queued(task_id)
    flow = Flow(task_id)
    flow.raise_in_cut = True
    flow.note_gate.set()
    _wire(monkeypatch, flow)

    asyncio.run(asyncio.wait_for(tf.run_pipeline_then_note(asyncio.Event(), _ctx(task_id)), FLOW_SECONDS))

    steps = _steps(task_id)
    assert steps["cut"]["status"] == "failed"
    assert steps["note"]["status"] == "done"
    assert not task_activity.activity(_job(task_id)).busy


def test_a_note_that_raises_leaves_no_step_in_progress(monkeypatch, task_id):
    _queued(task_id)
    flow = Flow(task_id)
    flow.render_gate.set()
    flow.raise_in_note = True
    _wire(monkeypatch, flow)

    with pytest.raises(RuntimeError):
        asyncio.run(asyncio.wait_for(tf.run_pipeline_then_note(asyncio.Event(), _ctx(task_id)), FLOW_SECONDS))

    steps = _steps(task_id)
    assert steps["note"]["status"] == "failed"
    assert all(step["status"] in task_steps.ENDS for step in steps.values())
    assert not task_activity.activity(_job(task_id)).busy


def test_a_stopped_task_has_its_open_steps_stopped(monkeypatch, task_id):
    _queued(task_id)
    flow = Flow(task_id)
    _wire(monkeypatch, flow)

    async def scenario():
        runner = asyncio.create_task(tf.run_pipeline_then_note(asyncio.Event(), _ctx(task_id)))
        assert await _until(lambda: (_steps(task_id).get("cut") or {}).get("status") == "running")
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner
        flow.render_gate.set()

    asyncio.run(scenario())
    steps = _steps(task_id)
    assert steps["transcribe"]["status"] == "done"
    assert {steps[name]["status"] for name in ("cut", "note", "compress", "feishu")} == {"stopped"}
    assert not task_activity.activity(_job(task_id)).busy


def test_the_feishu_step_says_what_came_of_the_export(monkeypatch, task_id):
    _queued(task_id)
    flow = Flow(task_id)
    flow.render_gate.set()
    flow.note_gate.set()
    _wire(monkeypatch, flow)

    async def export(ctx):
        _patch(ctx.task_id_value, lark_error="飞书没登录")

    monkeypatch.setattr(tf, "_export_note_written_after_pipeline", export)
    asyncio.run(asyncio.wait_for(tf.run_pipeline_then_note(asyncio.Event(), _ctx(task_id, lark=True)), FLOW_SECONDS))

    feishu = _steps(task_id)["feishu"]
    assert (feishu["status"], feishu["error_reason"]) == ("failed", "飞书没登录")


class _StepStoreWatch:
    """Records every task-store read and write that runs on the loop's thread.

    The test's own reads go through ``steps`` below and are not counted: they
    run on purpose, from here."""

    def __init__(self, monkeypatch):
        self.on_the_loop: list[str] = []
        self._reading_here = False
        loop_thread = threading.main_thread()
        real_write, real_read = job_store.write_task_steps, job_store.list_task_steps

        def note(what: str) -> None:
            if not self._reading_here and threading.current_thread() is loop_thread:
                self.on_the_loop.append(what)

        def write(*args, **kwargs):
            note("write")
            return real_write(*args, **kwargs)

        def read(*args, **kwargs):
            note("read")
            return real_read(*args, **kwargs)

        monkeypatch.setattr(job_store, "write_task_steps", write)
        monkeypatch.setattr(job_store, "list_task_steps", read)

    def steps(self, task_id: str) -> dict[str, dict]:
        """The task's steps, read by the test itself."""
        self._reading_here = True
        try:
            return _steps(task_id)
        finally:
            self._reading_here = False


def test_the_step_records_are_never_written_on_the_event_loop(monkeypatch, task_id, tmp_path):
    """Requirement (decision 8.30): writing a step waits for the disk (on the
    GitHub Windows machine 33 ms median, up to 400), so everything that runs on
    the loop that also answers the page and agents hands every write and read of
    the task store to a worker thread: a recording's whole flow, a subtitle
    file read where it is, and the stop endpoint (review of 2026-11-27)."""
    watch = _StepStoreWatch(monkeypatch)
    # A recording, through its whole flow.
    _queued(task_id)
    flow = Flow(task_id)
    flow.render_gate.set()
    flow.note_gate.set()
    _wire(monkeypatch, flow)
    asyncio.run(asyncio.wait_for(tf.run_pipeline_then_note(asyncio.Event(), _ctx(task_id)), FLOW_SECONDS))
    assert watch.on_the_loop == []
    steps = watch.steps(task_id)
    assert steps["note"]["status"] == task_steps.DONE
    assert all(step["status"] in task_steps.ENDS for step in steps.values())

    # A subtitle file read where it is (``transcript_note``), its note faked.
    from backend.core import transcript_note as tn

    subtitle = tmp_path / "课.srt"
    subtitle.write_text("1\n00:00:01,000 --> 00:00:02,000\n你好\n", encoding="utf-8")

    async def fake_summarize(**_kwargs):
        return {"task_id": _kwargs.get("task_id"), "transcript_text": "你好"}

    monkeypatch.setattr(tn, "summarize_transcript_source", fake_summarize)
    started = asyncio.run(asyncio.wait_for(
        tn.submit_subtitle_in_place(subtitle, client_id=None, options={}, route="/t", origin={}),
        FLOW_SECONDS,
    ))
    assert watch.on_the_loop == []
    assert all(step["status"] in task_steps.ENDS
               for step in watch.steps(started["task_id"]).values())

    # The stop endpoint, on a task with a step open and no runner.
    from backend.core import task_actions

    stopping = task_id + "-stop"
    job_store.upsert_job(task_id=stopping, status="running", stage="cut", progress=50)
    task_steps.plan(stopping)
    task_steps.start(stopping, task_steps.CUT)
    watch.on_the_loop.clear()  # the setup above is the test's own, on the loop
    asyncio.run(task_actions.stop_step(stopping, None))
    assert watch.on_the_loop == [], watch.on_the_loop
    assert watch.steps(stopping)[task_steps.CUT]["status"] == task_steps.STOPPED


def test_a_second_cancel_waits_out_the_write_too(monkeypatch, task_id):
    """Requirement (decision 8.30, review of 2026-11-27): a stop and then the
    service stopping arrive as two cancellations. Both have to wait for a write
    already handed to its thread: escaping on the second put the close that
    follows beside it, on the same task's steps."""
    started, release, wrote = threading.Event(), threading.Event(), threading.Event()

    def slow_write(*_args, **_kwargs):
        started.set()
        assert release.wait(30), "the write was never let go"
        wrote.set()

    monkeypatch.setattr(job_store, "write_task_steps", slow_write)

    async def scenario():
        task = asyncio.ensure_future(task_steps.start_off_loop(task_id, task_steps.NOTE))
        for _ in range(300):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set(), "the write never began"
        task.cancel()  # the user stops the step
        await asyncio.sleep(0.05)
        task.cancel()  # then the service stops
        await asyncio.sleep(0.05)
        assert not task.done(), "the cancellation came through with the write still running"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert wrote.is_set(), "the write did not finish before the cancellation"

    asyncio.run(scenario())


def test_a_restart_closes_what_was_running_and_leaves_an_agent_wait_alone(task_id):
    waiting = task_id + "-agent"
    for each in (task_id, waiting):
        job_store.upsert_job(task_id=each, status="completed", stage="done", result={"task_id": each})
        task_steps.plan(each)
        task_steps.finish(each, "transcribe", "done")
        task_steps.start(each, "cut")

    task_steps.recover_after_restart(keep=lambda each: each == waiting)

    steps = _steps(task_id)
    assert (steps["cut"]["status"], steps["cut"]["error_reason"]) == ("failed", task_steps.RESTART_ERROR)
    assert {steps[name]["status"] for name in ("note", "compress", "feishu")} == {"skipped"}
    assert not task_activity.activity(_job(task_id)).busy
    assert _steps(waiting)["cut"]["status"] == "running"


def test_planning_and_reopening_a_task_write_its_steps_in_one_commit(monkeypatch, task_id):
    job_store.upsert_job(task_id=task_id, status="completed", stage="done", result={"task_id": task_id})
    job_store.ensure_job_db()
    opened = []
    real = job_store.sqlite_connection

    def counting(*args, **kwargs):
        # Only this thread's opens: another test's leftover thread may write to
        # the same store in the same worker.
        if threading.current_thread() is threading.main_thread():
            opened.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(job_store, "sqlite_connection", counting)
    task_steps.plan(task_id)
    assert len(opened) == 1
    assert {step["status"] for step in _steps(task_id).values()} == {"pending"}
    assert set(_steps(task_id)) == set(task_steps.ORDER)

    monkeypatch.undo()
    task_steps.finish(task_id, "fetch", "done")
    task_steps.finish(task_id, "transcribe", "failed", "x")
    recorded = task_steps.steps_of([task_id])[task_id]  # the queue reads them first, as here
    monkeypatch.setattr(job_store, "sqlite_connection", counting)
    opened.clear()
    task_steps.reopen_from(task_id, "transcribe", recorded)
    assert len(opened) == 1
    steps = _steps(task_id)
    assert steps["fetch"]["status"] == "done"
    assert (steps["transcribe"]["status"], steps["transcribe"]["error_reason"]) == ("pending", None)


def test_a_task_waiting_for_the_agent_is_not_busy_but_says_so(task_id):
    job_store.upsert_job(task_id=task_id, status="completed", stage="done",
                         result={"task_id": task_id, "agent_cut": {"status": "waiting"}})
    task_steps.plan(task_id)
    task_steps.finish(task_id, "transcribe", "done")
    task_steps.start(task_id, "cut")

    activity = task_activity.activity(_job(task_id))
    assert not activity.busy
    assert activity.waiting_for_agent
    assert task_activity.busy_reason(_job(task_id)) is None


def test_a_cut_started_again_on_a_finished_task_counts(task_id):
    job_store.upsert_job(task_id=task_id, status="completed", stage="done",
                         result={"task_id": task_id, "debreath": {"status": "running"}})
    task_steps.plan(task_id)
    for name in task_steps.ORDER:
        task_steps.finish(task_id, name, "done")

    assert all(_busy_everywhere(task_id).values())
    assert task_activity.activity(_job(task_id)).label == "正在剪辑"


def test_the_jobs_list_carries_the_answer_the_restart_guard_counts(monkeypatch, task_id):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.routers import local_jobs

    monkeypatch.setattr(local_jobs, "local_client_scope", lambda _request: None)
    job_store.upsert_job(task_id=task_id, status="completed", stage="done", result={"task_id": task_id})
    task_steps.plan(task_id)
    task_steps.finish(task_id, "transcribe", "done")
    task_steps.start(task_id, "note")
    app = FastAPI()
    app.include_router(local_jobs.router)

    jobs = TestClient(app).get("/jobs").json()["jobs"]
    mine = next(job for job in jobs if job["task_id"] == task_id)
    assert mine["activity"]["busy"] is True
    assert mine["activity"]["label"] == "正在写笔记"
    assert guard.is_busy(mine)


@pytest.mark.parametrize("state, outcome", [
    (None, "skipped"),
    # Shape of a real record (2026-10-25, a 4-minute clip): declined, not failed.
    ({"compress_skipped": "码率不高，压缩省不了多少", "compressed": False, "output_folder": "/x"}, "skipped"),
    ({"compressed": False, "compress_error": "压缩版没做成（磁盘满了），原文件不受影响。"}, "failed"),
    ({"compressed": True, "output_folder": "/x"}, "done"),
])
def test_the_compressed_copy_step_reads_what_the_copy_recorded(state, outcome):
    assert task_steps.compress_outcome(state)[0] == outcome
