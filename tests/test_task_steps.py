"""A task is finished only when every one of its steps has ended.

Requirements (owner, 2026-10-18 and 2026-10-25, docs/方案/2026-10-25-任务步骤清单和模型用量记账.md),
in the user's terms:

- A task records its steps: transcription, cut, note, compressed copy, Feishu
  export. Each one is not started, in progress, done, failed, skipped or
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
"""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import job_store, task_activity, task_steps
from backend.routers import local_agent
from backend.routers import local_processing as lp

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
        "agent_not_done": not local_agent._task_done(job),
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
    monkeypatch.setattr(lp, "_run_pipeline", flow.pipeline)
    monkeypatch.setattr(lp, "_agent_judges_cut", _never)
    monkeypatch.setattr(lp.local_intake_flow, "survey_first_enabled", lambda: True)
    monkeypatch.setattr(lp.local_intake_flow, "note_is_wanted", lambda *_a: flow.note)
    monkeypatch.setattr(lp.local_intake_flow, "mark_note_running", lambda *_a: None)
    monkeypatch.setattr(lp.local_intake_flow, "write_note", flow.write_note)
    monkeypatch.setattr(lp.local_intake_flow, "compress_original", lambda *_a: None)
    monkeypatch.setattr(lp.survey_cut, "survey_and_cut", flow.survey_and_cut)
    monkeypatch.setattr(lp.visual_note_channel, "resolve_channel",
                        lambda *_a, **_k: SimpleNamespace(available=False))
    monkeypatch.setattr(lp, "_deliver_to_output_folder", _nothing)


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
        runner = asyncio.create_task(lp.run_pipeline_then_note(asyncio.Event(), _ctx(task_id)))
        assert await _until(lambda: (_steps(task_id).get("cut") or {}).get("status") == "running")
        # The transcript is the user's already; the task is not finished.
        assert _job(task_id)["result"]["transcript_text"] == "大家好"
        assert all(_busy_everywhere(task_id).values()), _busy_everywhere(task_id)
        assert task_activity.activity(_job(task_id)).label == "正在剪辑"
        assert local_agent._cut_running(_job(task_id))

        flow.render_gate.set()
        assert await _until(lambda: (_steps(task_id).get("note") or {}).get("status") == "running")
        assert all(_busy_everywhere(task_id).values())
        assert local_agent._note_pending(_job(task_id))
        assert task_activity.busy_reason(_job(task_id)) == "这个任务的笔记还在写，等写完再删。"

        flow.note_gate.set()
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert not any(_busy_everywhere(task_id).values())
    steps = _steps(task_id)
    assert {name: step["status"] for name, step in steps.items()} == {
        "transcribe": "done", "cut": "done", "note": "done", "compress": "skipped", "feishu": "skipped",
    }
    assert steps["cut"]["started_at"] and steps["cut"]["finished_at"]


def test_a_task_that_only_wanted_a_transcript_is_finished_once_it_is_stored(monkeypatch, task_id):
    _queued(task_id)
    flow = Flow(task_id, note=False, cut=False)
    _wire(monkeypatch, flow)

    asyncio.run(asyncio.wait_for(lp.run_pipeline_then_note(asyncio.Event(), _ctx(task_id)), 5))

    assert not any(_busy_everywhere(task_id).values())
    assert {name: step["status"] for name, step in _steps(task_id).items()} == {
        "transcribe": "done", "cut": "skipped", "note": "skipped", "compress": "skipped", "feishu": "skipped",
    }


def test_a_failed_note_ends_the_task_and_names_the_failure(monkeypatch, task_id):
    _queued(task_id)
    flow = Flow(task_id, note_outcome="failed")
    flow.render_gate.set()
    flow.note_gate.set()
    _wire(monkeypatch, flow)

    asyncio.run(asyncio.wait_for(lp.run_pipeline_then_note(asyncio.Event(), _ctx(task_id)), 5))

    activity = task_activity.activity(_job(task_id))
    assert not activity.busy
    assert activity.failed_steps == ("note",)
    assert _steps(task_id)["note"]["error_reason"] == "额度用完了"
    assert local_agent._task_done(_job(task_id))


def test_a_cut_that_raises_is_failed_and_the_task_still_ends(monkeypatch, task_id):
    _queued(task_id)
    flow = Flow(task_id)
    flow.raise_in_cut = True
    flow.note_gate.set()
    _wire(monkeypatch, flow)

    asyncio.run(asyncio.wait_for(lp.run_pipeline_then_note(asyncio.Event(), _ctx(task_id)), 5))

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
        asyncio.run(asyncio.wait_for(lp.run_pipeline_then_note(asyncio.Event(), _ctx(task_id)), 5))

    steps = _steps(task_id)
    assert steps["note"]["status"] == "failed"
    assert all(step["status"] in task_steps.ENDS for step in steps.values())
    assert not task_activity.activity(_job(task_id)).busy


def test_a_stopped_task_has_its_open_steps_stopped(monkeypatch, task_id):
    _queued(task_id)
    flow = Flow(task_id)
    _wire(monkeypatch, flow)

    async def scenario():
        runner = asyncio.create_task(lp.run_pipeline_then_note(asyncio.Event(), _ctx(task_id)))
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

    monkeypatch.setattr(lp, "_export_note_written_after_pipeline", export)
    asyncio.run(asyncio.wait_for(lp.run_pipeline_then_note(asyncio.Event(), _ctx(task_id, lark=True)), 5))

    feishu = _steps(task_id)["feishu"]
    assert (feishu["status"], feishu["error_reason"]) == ("failed", "飞书没登录")


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
