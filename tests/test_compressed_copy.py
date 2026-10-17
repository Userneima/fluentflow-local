"""A high-bitrate recording also gets an uncut compressed copy.

Requirements (owner, 2026-10-18):
- A recording above the bitrate threshold gets a compressed copy of the whole
  thing in its output folder, beside the cut; FluentFlow never touches the
  original.
- Making it is local work: the next recording starts after it, not during it,
  and it runs while the note waits on Claude.
- A task with no note still gets its copy; a copy made already is not made twice;
  an upload (no folder of the user's) and an audio recording get none.
- The output folder is filled after the copy exists.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import local_intake_flow as flow
from backend.routers import local_processing as lp


# ── which tasks get a copy ─────────────────────────────────────────────────

@pytest.fixture
def task(monkeypatch, tmp_path):
    state = {"result": {"task_id": "t"}}
    saved: dict = {}
    original = tmp_path / "会议.mp4"
    original.write_bytes(b"x")
    monkeypatch.setattr(flow, "get_job", lambda *_a, **_k: {"task_id": "t", "result": dict(state["result"])})
    monkeypatch.setattr(flow, "save_result_changes", lambda _t, _b, after, **_k: saved.update(after))
    monkeypatch.setattr(flow.debreath_job, "resolve_source", lambda _t: original)
    monkeypatch.setattr(flow, "is_managed_path", lambda _p: False)
    made: list = []
    monkeypatch.setattr(flow, "_compress_beside",
                        lambda path, watch, task_id, phase="": made.append(path) or {"compressed": True})
    return SimpleNamespace(state=state, saved=saved, original=original, made=made)


def test_a_recording_in_the_users_folder_gets_its_copy_recorded(task):
    assert flow.compress_original("t", None) == {"compressed": True}
    assert task.made == [task.original]
    assert task.saved["compressed_copy"] == {"compressed": True}


def test_a_copy_made_already_is_not_made_twice(task):
    task.state["result"]["compressed_copy"] = {"compressed": True}
    assert flow.compress_original("t", None) is None
    task.state["result"] = {"debreath": {"compressed": True}}
    assert flow.compress_original("t", None) is None
    assert task.made == []


def test_an_upload_gets_no_copy(task, monkeypatch):
    monkeypatch.setattr(flow, "is_managed_path", lambda _p: True)
    assert flow.compress_original("t", None) is None
    assert task.made == []


def test_an_audio_recording_gets_no_copy(task, monkeypatch, tmp_path):
    monkeypatch.setattr(flow.debreath_job, "resolve_source", lambda _t: tmp_path / "会议.m4a")
    assert flow.compress_original("t", None) is None


# ── where it runs in the flow ──────────────────────────────────────────────

class Steps:
    def __init__(self):
        self.log: list[str] = []
        self.copy_gate = threading.Event()
        self.remote_gate = threading.Event()

    async def pipeline(self, ctx):
        self.log.append("pipeline")

    def write_note(self, task_id, client_id, *, on_local_work_done=None):
        self.log.append("pictures-taken")
        on_local_work_done()
        self.remote_gate.wait(5)
        self.log.append("note-done")

    def compress(self, task_id, client_id, watch):
        self.log.append("copy-start")
        self.copy_gate.wait(5)
        self.log.append("copy-done")


def _wire(monkeypatch, steps, *, note=True):
    monkeypatch.setattr(lp, "_run_pipeline", steps.pipeline)
    monkeypatch.setattr(lp.local_intake_flow, "survey_first_enabled", lambda: False)
    monkeypatch.setattr(lp.local_intake_flow, "note_is_wanted", lambda *_a: note)
    monkeypatch.setattr(lp.local_intake_flow, "mark_note_running", lambda *_a: None)
    monkeypatch.setattr(lp.local_intake_flow, "write_note", steps.write_note)
    monkeypatch.setattr(lp.local_intake_flow, "compress_original", steps.compress)
    monkeypatch.setattr(lp.tail_trim, "trim_tail", lambda *_a: None)

    async def deliver(_ctx):
        steps.log.append("delivered")
    monkeypatch.setattr(lp, "_export_note_written_after_pipeline", lambda _ctx: asyncio.sleep(0))
    monkeypatch.setattr(lp, "_deliver_to_output_folder", deliver)


async def _until(predicate, tries=150):
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


def test_the_queue_waits_for_the_copy_but_not_for_claude(monkeypatch):
    steps = Steps()
    _wire(monkeypatch, steps)

    async def scenario():
        done = asyncio.Event()
        runner = asyncio.create_task(lp.run_pipeline_then_note(done, SimpleNamespace(task_id_value="t", client_id=None)))
        assert await _until(lambda: "copy-start" in steps.log)
        await asyncio.sleep(0.1)
        assert not done.is_set(), "the next recording started beside the copy"
        steps.copy_gate.set()
        assert await _until(done.is_set), "the queue waited for Claude"
        assert "note-done" not in steps.log
        steps.remote_gate.set()
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert steps.log.index("copy-done") < steps.log.index("delivered")


def test_a_task_without_a_note_still_gets_its_copy(monkeypatch):
    steps = Steps()
    _wire(monkeypatch, steps, note=False)
    steps.copy_gate.set()

    asyncio.run(asyncio.wait_for(lp.run_pipeline_then_note(asyncio.Event(), SimpleNamespace(task_id_value="t", client_id=None)), 5))

    assert "copy-done" in steps.log and steps.log.index("copy-done") < steps.log.index("delivered")
