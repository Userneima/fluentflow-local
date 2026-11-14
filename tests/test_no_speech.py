"""A recording in which no one speaks.

Requirements (owner, 2026-11-14, from a screen recording that took only a muted
microphone and still read as completed):

- A transcription that comes back empty fails the task instead of reporting it
  completed, and the task says 「这段录音里没检测到人声」 with the usual cause.
- Nothing after it runs: no cut, no note, no compressed copy, no Feishu
  export, no output folder. Those steps read as skipped, not failed.
- An agent waiting on the task is answered at once; retrying does not help, so
  the diagnosis does not offer it.
- A transcription that failed for any other reason goes on as before.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.core import job_store, task_activity
from backend.core import task_flow as tf
from backend.core.local_entry_guards import friendly_error
from backend.core.local_error_diagnostics import NO_SPEECH, diagnose_error
from backend.core.media_job import require_speech
from backend.routers import local_agent
from tests.test_task_steps import Flow, _ctx, _job, _queued, _steps, _wire

_counter = iter(range(10_000))


@pytest.fixture()
def task_id():
    return f"no-speech-{next(_counter)}"


def test_an_empty_transcription_is_refused_and_one_with_words_is_not():
    with pytest.raises(RuntimeError, match=NO_SPEECH):
        require_speech("", [])
    with pytest.raises(RuntimeError, match=NO_SPEECH):
        require_speech("  \n", None)
    require_speech("大家好", [])
    require_speech("", [{"start": 0, "end": 1, "text": "嗯"}])


def test_the_task_says_no_one_was_heard_and_offers_no_retry():
    stored = friendly_error(RuntimeError(NO_SPEECH))
    assert stored.startswith(NO_SPEECH)
    # The stored message is diagnosed again when the task is shown.
    diagnosis = diagnose_error(stored)
    assert diagnosis["code"] == "no_speech"
    assert "系统声音" in diagnosis["detail"]
    assert diagnosis["retryable"] is False


class _Silent(Flow):
    def __init__(self, task_id, error):
        super().__init__(task_id)
        self.error = error
        self.cut_called = self.note_called = self.compressed = self.delivered = False

    async def pipeline(self, ctx):
        job_store.upsert_job(task_id=self.task_id, status="failed", stage="transcribe", progress=0,
                             error_reason=friendly_error(RuntimeError(self.error)))

    def survey_and_cut(self, *a, **k):
        self.cut_called = True

    def write_note(self, *a, **k):
        self.note_called = True


def _run(monkeypatch, task_id, error):
    _queued(task_id)
    flow = _Silent(task_id, error)
    _wire(monkeypatch, flow)
    monkeypatch.setattr(tf.local_intake_flow, "compress_original",
                        lambda *_a: setattr(flow, "compressed", True))

    async def deliver(_ctx):
        flow.delivered = True

    monkeypatch.setattr(tf, "_deliver_to_output_folder", deliver)
    asyncio.run(asyncio.wait_for(tf.run_pipeline_then_note(asyncio.Event(), _ctx(task_id)), 5))
    return flow


def test_nothing_runs_after_a_transcription_that_heard_no_one(monkeypatch, task_id):
    flow = _run(monkeypatch, task_id, NO_SPEECH)

    assert not (flow.cut_called or flow.note_called or flow.compressed or flow.delivered)
    steps = _steps(task_id)
    assert steps["transcribe"]["status"] == "failed"
    assert steps["transcribe"]["error_reason"].startswith(NO_SPEECH)
    assert {name: steps[name]["status"] for name in ("cut", "note", "compress", "feishu")} == {
        "cut": "skipped", "note": "skipped", "compress": "skipped", "feishu": "skipped",
    }
    job = _job(task_id)
    assert job["status"] == "failed"
    assert not task_activity.activity(job).busy
    assert local_agent._task_done(job)


def test_another_failed_transcription_still_goes_on(monkeypatch, task_id):
    flow = _run(monkeypatch, task_id, "whisper crashed")

    assert flow.cut_called or flow.note_called
