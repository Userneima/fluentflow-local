"""A task cut off or stopped carries on from where it was, in place.

Requirements (owner, 2026-10-18 and 2026-10-25; docs/方案/2026-10-25-任务步骤清单和模型用量记账.md,
to-do "任务『完成』指每一步都做完", batch two), in the user's terms:

- After a restart the same task carries on from the first step that did not
  reach its end; no new task is made, and the steps it did are not done again.
- A cut whose decision was already made is only rendered again: no model is
  asked again (that costs money).
- A transcription is done again from the start when it was cut off; when only
  its corrections were left, the transcription is not repeated.
- A task restarts cut off mid-step more than twice in a row is left for the
  user, as before.
- Any step can be stopped. The step reads stopped, the task 已停下, and carrying
  it on starts at that step; the steps before it are not done again.
- Stopping kills what the step runs (here: a command line started for it).
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import (
    job_store,
    survey_cut,
    task_actions,
    task_activity,
    task_flow as tf,
    task_steps,
    task_stop,
)
from backend.core.silence_cuts import CutPlan, TimeRange

_counter = iter(range(10_000))


@pytest.fixture()
def task_id():
    return f"resume-{next(_counter)}-{int(time.time() * 1000) % 100000}"


def _statuses(task_id):
    return task_steps.statuses(task_steps.steps_of([task_id]).get(task_id, []))


def _patch(task_id, **changes):
    job = job_store.get_job(task_id) or {}
    result = dict(job.get("result") or {})
    result.update(changes)
    job_store.update_job_result(task_id, result)


class Edges:
    """The pipeline, the cut and the note faked where they meet the outside;
    each records that it ran and writes what the real one writes."""

    def __init__(self, task_id):
        self.task_id = task_id
        self.ran: list[str] = []
        self.note_gate = threading.Event()
        self.note_gate.set()
        self.pipeline_gate = asyncio.Event
        self.hold_pipeline = False
        self.checkpoints: list = []

    async def pipeline(self, ctx):
        self.ran.append("pipeline")
        self.checkpoints.append(ctx.transcribe_checkpoint)
        if self.hold_pipeline:
            await asyncio.sleep(30)
        job_store.upsert_job(task_id=self.task_id, status="completed", stage="done", progress=100,
                             result={"task_id": self.task_id, "transcript_text": "大家好"})

    def survey_and_cut(self, task_id, client_id, on_plan=None):
        self.ran.append("cut")
        _patch(task_id, debreath={"status": "completed"})

    def write_note(self, task_id, client_id, *, on_local_work_done=None, planned_ranges=None):
        self.ran.append("note")
        if on_local_work_done:
            on_local_work_done()
        self.note_gate.wait(10)
        if task_stop.requested(task_id):
            return
        _patch(task_id, summary_markdown="# 笔记", summary_status="completed")


def _wire(monkeypatch, edges):
    async def never(_ctx):
        return False

    async def nothing(_ctx):
        return None

    monkeypatch.setattr(tf, "_run_pipeline", edges.pipeline)
    monkeypatch.setattr(tf, "_agent_judges_cut", never)
    monkeypatch.setattr(tf.local_intake_flow, "survey_first_enabled", lambda: True)
    monkeypatch.setattr(tf.local_intake_flow, "note_is_wanted",
                        lambda task_id, *_a: not (job_store.get_job(task_id) or {}).get("result", {}).get("summary_markdown"))
    monkeypatch.setattr(tf.local_intake_flow, "mark_note_running", lambda *_a: None)
    monkeypatch.setattr(tf.local_intake_flow, "write_note", edges.write_note)
    monkeypatch.setattr(tf.local_intake_flow, "compress_original", lambda *_a: None)
    monkeypatch.setattr(tf.survey_cut, "survey_and_cut", edges.survey_and_cut)
    monkeypatch.setattr(tf.visual_note_channel, "resolve_channel",
                        lambda *_a, **_k: SimpleNamespace(available=False))
    monkeypatch.setattr(tf, "_deliver_to_output_folder", nothing)


def _in_place_task(task_id, tmp_path, *, status="completed", result=None):
    recording = tmp_path / f"{task_id}.mp4"
    recording.write_bytes(b"not really a video")
    job_store.upsert_job(
        task_id=task_id, status=status, stage="done", progress=100, source_type="video",
        source_filename=recording.name,
        result=result if result is not None else {"task_id": task_id, "transcript_text": "大家好"},
        metadata={"folder_intake": {"original_path": str(recording)}, "queue_options": {}},
    )
    return recording


async def _until(predicate, tries=400):
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


def _ended(task_id):
    return not any(status in task_steps.OPEN for status in _statuses(task_id).values())


# ── after a restart ──────────────────────────────────────────────────────────

def _cut_off_during_the_cut(task_id):
    task_steps.plan(task_id)
    for name in task_steps.BEFORE_TRANSCRIPT:
        task_steps.finish(task_id, name, task_steps.DONE)
    task_steps.start(task_id, task_steps.CUT)


def test_a_restart_carries_the_same_task_on_from_the_cut(monkeypatch, task_id, tmp_path):
    _in_place_task(task_id, tmp_path)
    _cut_off_during_the_cut(task_id)
    edges = Edges(task_id)
    _wire(monkeypatch, edges)

    async def scenario():
        assert task_actions.stepped_tasks_cut_off().count(task_id) == 1
        assert await task_actions.resume_after_restart([task_id]) == 1
        assert await _until(lambda: _ended(task_id))

    asyncio.run(scenario())
    assert edges.ran == ["cut", "note"], "transcription is not done again"
    assert set(_statuses(task_id).values()) <= {task_steps.DONE, task_steps.SKIPPED}
    assert _statuses(task_id)[task_steps.CUT] == task_steps.DONE
    assert job_store.get_job(task_id)["status"] == "completed"


def test_a_task_waiting_its_turn_is_carried_on_and_not_counted(monkeypatch, task_id, tmp_path):
    _in_place_task(task_id, tmp_path, status="queued", result={})
    task_steps.plan(task_id)
    edges = Edges(task_id)
    _wire(monkeypatch, edges)

    async def scenario():
        assert await task_actions.resume_after_restart([task_id]) == 1
        assert await _until(lambda: _ended(task_id))

    asyncio.run(scenario())
    assert edges.ran[0] == "pipeline"
    assert job_store.get_job(task_id)["status"] == "completed"
    assert not (job_store.get_job(task_id).get("metadata") or {}).get(task_actions.RESTART_RESUME_COUNT_KEY)


def test_restarts_that_keep_cutting_a_task_off_leave_it_for_the_user(monkeypatch, task_id, tmp_path):
    _in_place_task(task_id, tmp_path)
    _cut_off_during_the_cut(task_id)
    job_store.update_job_metadata(task_id, {task_actions.RESTART_RESUME_COUNT_KEY: task_actions.RESTART_RESUME_LIMIT})
    edges = Edges(task_id)
    _wire(monkeypatch, edges)

    assert asyncio.run(task_actions.resume_after_restart([task_id])) == 0
    assert edges.ran == []
    step = next(s for s in task_steps.steps_of([task_id])[task_id] if s["step_type"].endswith(":cut"))
    assert step["status"] == task_steps.FAILED and step["error_reason"] == task_steps.RESTART_ERROR


def test_only_the_corrections_run_again_when_the_transcription_was_kept(monkeypatch, task_id, tmp_path):
    _in_place_task(task_id, tmp_path, status="running", result={})
    task_steps.plan(task_id)
    task_steps.finish(task_id, task_steps.FETCH, task_steps.DONE)
    task_steps.finish(task_id, task_steps.TRANSCRIBE, task_steps.DONE)
    task_steps.start(task_id, task_steps.CORRECT)
    kept = {"tr": {"text": "大家好"}, "base_result": {}, "in_path": "x", "segments_payload": []}
    monkeypatch.setattr(tf, "_artifact_storage_dir", lambda: tmp_path)
    tf._save_checkpoint(task_id, kept)
    edges = Edges(task_id)
    _wire(monkeypatch, edges)

    async def scenario():
        assert await task_actions.resume_after_restart([task_id]) == 1
        assert await _until(lambda: _ended(task_id))

    asyncio.run(scenario())
    assert edges.checkpoints == [kept], "the pipeline was handed what the transcription produced"
    assert _statuses(task_id)[task_steps.TRANSCRIBE] == task_steps.DONE
    assert _statuses(task_id)[task_steps.CORRECT] == task_steps.DONE
    assert tf.load_checkpoint(task_id) is None, "dropped once the transcript is stored"


def test_a_cut_cut_off_mid_render_is_rendered_again_without_asking_a_model(monkeypatch, task_id, tmp_path):
    job_store.upsert_job(task_id=task_id, status="completed", result={
        "task_id": task_id, "raw_segments": [{"start": 0, "end": 5, "text": "大家好"}],
        "debreath": {survey_cut.DECIDED_KEY: {
            "cuts": [[5.0, 9.0]], "judged_by": "claude-test", "record": {"judged_by": "claude-test", "removed": []},
        }},
    })
    rendered = {}
    monkeypatch.setattr(survey_cut.debreath_job, "resolve_source", lambda _t: tmp_path / "x.mp4")
    monkeypatch.setattr(survey_cut, "_judge", lambda *_a: pytest.fail("a model was asked again"))
    monkeypatch.setattr(survey_cut, "revise_plan", lambda *_a, **_k: pytest.fail("a model was asked again"))
    monkeypatch.setattr(survey_cut, "spoken_words", lambda media, plan, words: ([], {}))

    def run_debreath(task_id, *, client_id=None, revise=None, **_kwargs):
        volume = CutPlan(source_duration_seconds=20.0, cuts=[TimeRange(1.0, 2.0)], keeps=[], silences_found=1,
                         noise_db=-40, min_silence_seconds=0.3, padding_seconds=0.1)
        rendered["plan"] = revise(tmp_path / "x.mp4", volume)
        raise RuntimeError("stop here: only the plan matters")

    monkeypatch.setattr(survey_cut.debreath_job, "run_debreath", run_debreath)
    record = survey_cut.survey_and_cut(task_id)

    assert [(c.start, c.end) for c in rendered["plan"].cuts] == [(5.0, 9.0)]
    assert record["judged_by"] == "claude-test" and record.get("replayed_decision") is True


def test_a_decision_is_kept_before_the_render(monkeypatch, task_id, tmp_path):
    job_store.upsert_job(task_id=task_id, status="completed", result={
        "task_id": task_id, "raw_segments": [{"start": 0, "end": 5, "text": "大家好"}],
    })
    monkeypatch.setattr(survey_cut.debreath_job, "resolve_source", lambda _t: tmp_path / "x.mp4")
    monkeypatch.setattr(survey_cut, "_judge", lambda *_a: None)
    monkeypatch.setattr(survey_cut, "spoken_words", lambda media, plan, words: ([], {}))

    def run_debreath(task_id, *, client_id=None, revise=None, **_kwargs):
        volume = CutPlan(source_duration_seconds=20.0, cuts=[TimeRange(1.0, 2.0)], keeps=[], silences_found=1,
                         noise_db=-40, min_silence_seconds=0.3, padding_seconds=0.1)
        revise(tmp_path / "x.mp4", volume)
        raise RuntimeError("the service went away mid-render")

    monkeypatch.setattr(survey_cut.debreath_job, "run_debreath", run_debreath)
    survey_cut.survey_and_cut(task_id)

    kept = job_store.get_job(task_id)["result"]["debreath"][survey_cut.DECIDED_KEY]
    assert kept["cuts"] == [[1.0, 2.0]]


# ── stopping a step, and carrying on ─────────────────────────────────────────

def test_a_stopped_note_reads_stopped_and_carrying_on_writes_only_the_note(monkeypatch, task_id, tmp_path):
    _in_place_task(task_id, tmp_path, status="queued", result={})
    edges = Edges(task_id)
    edges.note_gate.clear()
    _wire(monkeypatch, edges)

    async def scenario():
        await task_actions._carry_on(job_store.get_job(task_id), task_steps.FETCH, route="/test")
        assert await _until(lambda: _statuses(task_id).get(task_steps.NOTE) == task_steps.RUNNING)
        answer = await task_actions.stop_step(task_id, None)
        assert answer["stopped_step"]["step"] == task_steps.NOTE
        edges.note_gate.set()
        assert await _until(lambda: _ended(task_id))
        assert await _until(lambda: not task_actions.JOB_EVENTS.is_running(task_id))

        activity = task_activity.activity(job_store.get_job(task_id))
        assert not activity.busy and activity.label == "已停下"
        assert activity.resume_from == task_steps.NOTE
        assert _statuses(task_id)[task_steps.NOTE] == task_steps.STOPPED
        assert job_store.get_job(task_id)["status"] == "completed", "the transcript stays the user's"

        edges.ran.clear()
        answer = await task_actions.resume_task(task_id, None)
        assert answer["resume_from"]["step"] == task_steps.NOTE
        assert await _until(lambda: _ended(task_id) and not task_actions.JOB_EVENTS.is_running(task_id))

    asyncio.run(scenario())
    assert edges.ran == ["note"], "nothing before the note is done again"
    assert _statuses(task_id)[task_steps.NOTE] == task_steps.DONE
    assert task_activity.activity(job_store.get_job(task_id)).resume_from is None


def test_a_stopped_transcription_is_carried_on_from_the_transcription(monkeypatch, task_id, tmp_path):
    _in_place_task(task_id, tmp_path, status="queued", result={})
    edges = Edges(task_id)
    edges.hold_pipeline = True
    _wire(monkeypatch, edges)

    async def scenario():
        await task_actions._carry_on(job_store.get_job(task_id), task_steps.FETCH, route="/test")
        assert await _until(lambda: _statuses(task_id).get(task_steps.TRANSCRIBE) == task_steps.RUNNING)
        await task_actions.stop_step(task_id, None)
        assert await _until(lambda: _ended(task_id) and not task_actions.JOB_EVENTS.is_running(task_id))
        assert _statuses(task_id)[task_steps.TRANSCRIBE] == task_steps.STOPPED
        assert job_store.get_job(task_id)["status"] == "cancelled"

        edges.hold_pipeline = False
        edges.ran.clear()
        await task_actions.resume_task(task_id, None)
        assert await _until(lambda: _ended(task_id) and not task_actions.JOB_EVENTS.is_running(task_id))

    asyncio.run(scenario())
    assert edges.ran[0] == "pipeline"
    assert job_store.get_job(task_id)["status"] == "completed"
    assert _statuses(task_id)[task_steps.TRANSCRIBE] == task_steps.DONE


def test_stopping_kills_what_the_step_started():
    task = f"kill-{next(_counter)}"
    task_stop.begin(task)
    with task_stop.bound(task):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        task_stop.track(process)
    assert task_stop.request(task) == 1
    assert process.wait(timeout=5) is not None
    task_stop.untrack(process)


def test_nothing_to_stop_and_nothing_to_carry_on_are_said_plainly(task_id):
    job_store.upsert_job(task_id=task_id, status="completed", result={"transcript_text": "x"})
    task_steps.plan(task_id)
    for name in task_steps.ORDER:
        task_steps.finish(task_id, name, task_steps.DONE)
    with pytest.raises(task_actions.Refused) as stop:
        asyncio.run(task_actions.stop_step(task_id, None))
    assert stop.value.code == "not_working"
    with pytest.raises(task_actions.Refused) as resume:
        asyncio.run(task_actions.resume_task(task_id, None))
    assert resume.value.code == "nothing_to_resume"


def test_a_task_from_before_steps_were_recorded_points_to_retry(task_id):
    job_store.upsert_job(task_id=task_id, status="failed", result={})
    with pytest.raises(task_actions.Refused) as refused:
        asyncio.run(task_actions.resume_task(task_id, None))
    assert refused.value.code == "no_steps"


def test_the_detail_timeline_is_the_tasks_steps(task_id):
    """Requirement: the processing detail's timeline reads the step list, not
    the event log: each step, how it ended, and why a failed one failed."""
    from backend.core.local_task_detail import build_task_detail

    job_store.upsert_job(task_id=task_id, status="completed", result={"transcript_text": "x"})
    task_steps.plan(task_id)
    for name in task_steps.BEFORE_TRANSCRIPT:
        task_steps.finish(task_id, name, task_steps.DONE)
    task_steps.finish(task_id, task_steps.CUT, task_steps.STOPPED)
    task_steps.finish(task_id, task_steps.NOTE, task_steps.FAILED, "额度用完了")

    timeline = build_task_detail(job_store.get_job(task_id))["timeline"]

    assert [step["id"] for step in timeline] == list(task_steps.ORDER)
    by_id = {step["id"]: step for step in timeline}
    assert by_id["transcribe"]["title"] == "转录" and by_id["transcribe"]["status"] == "completed"
    assert by_id["cut"]["status"] == "stopped"
    assert by_id["note"]["status"] == "failed" and by_id["note"]["error_reason"] == "额度用完了"
