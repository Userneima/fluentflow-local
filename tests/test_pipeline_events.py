"""Events that answer "where did the time go, and why is there no note".

Requirements, as questions the event log has to answer after the task itself
has been deleted:

- How long did a task wait behind other recordings before it started?
- When the pipeline skipped its own note, was that because the user asked for a
  transcript only, or because the note is written from the frames afterwards?
- When the frame note could not run (an expired Claude login), which tasks fell
  back to the text note, and why?
- Which tasks did a restart cut off, and were they re-queued?
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from backend.core import job_store, local_intake_flow
from backend.core.job_event_hub import JobEventHub
from backend.routers import local_processing as lp


@pytest.fixture()
def events(monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(lp, "log_event", lambda **kw: recorded.append(kw))
    return recorded


@pytest.fixture()
def ids():
    created: list[str] = []

    def make(*names):
        tag = uuid.uuid4().hex[:8]
        out = [f"events-{tag}-{n}" for n in names]
        created.extend(out)
        return out

    yield make
    job_store.delete_jobs(created)


def _ctx(task_id, monkeypatch, *, skip_summary=False):
    # Built inside the service's async handlers, so built on a loop here too.
    async def build():
        return lp._local_media_job_context(
            task_id=task_id, client_id=None, source_type="video", source_filename="talk.mp4",
            raw_title="talk", display_title="talk", suffix=".mp4", td="/tmp", in_path=Path("/tmp/talk.mp4"),
            content=b"", source_file_size_mb=1.0, duration_preflight_sec=60.0,
            options={"skip_summary": "true"} if skip_summary else {},
        )

    return asyncio.run(build())


def test_a_task_that_waited_records_how_long_and_behind_whom(monkeypatch, events, ids):
    monkeypatch.setattr(lp, "JOB_EVENTS", JobEventHub())
    lp._QUEUE_RECENT.clear()
    first, second = ids("first", "second")

    async def scenario():
        ahead = asyncio.Event()
        lp._queue_tail_record(first, ahead)
        previous = lp._queue_tail_barrier()
        waiter = asyncio.create_task(lp.wait_for_queue_turn(previous, second, None))
        await asyncio.sleep(0.2)
        ahead.set()
        await waiter
        await lp.wait_for_queue_turn(None, first, None)

    asyncio.run(scenario())
    lp._QUEUE_RECENT.clear()
    waits = [e for e in events if e["event_name"] == "queue_wait_completed"]
    assert len(waits) == 1, "the task that was first in line did not wait"
    assert waits[0]["task_id"] == second
    assert waits[0]["metadata"]["waited_behind"] == first
    assert waits[0]["duration_seconds"] >= 0.15


def test_a_note_written_from_the_frames_later_is_not_recorded_as_transcript_only(monkeypatch, events, ids):
    monkeypatch.setattr(local_intake_flow, "auto_note_will_run", lambda: True)
    [task] = ids("deferred")

    ctx = _ctx(task, monkeypatch)
    assert ctx.summary_disabled and ctx.note_deferred_to_visual_note

    ctx = _ctx(task, monkeypatch, skip_summary=True)
    assert ctx.summary_disabled and not ctx.note_deferred_to_visual_note


def test_a_task_that_falls_back_to_the_text_note_says_why(monkeypatch, events, ids):
    monkeypatch.setattr(local_intake_flow, "auto_note_enabled", lambda: True)
    monkeypatch.setattr(local_intake_flow, "auto_note_will_run", lambda: False)
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "subscription")
    from backend.core import claude_code_note
    monkeypatch.setattr(claude_code_note, "cli_path", lambda: "/fake/claude")
    monkeypatch.setattr(claude_code_note, "login_state", lambda **_kw: False)
    [task] = ids("fallback")

    ctx = _ctx(task, monkeypatch)

    assert not ctx.summary_disabled, "the text note runs instead"
    [event] = [e for e in events if e["event_name"] == "visual_note_unavailable"]
    assert event["task_id"] == task
    assert event["metadata"]["reason"] == "login_expired"
    assert event["metadata"]["fallback"] == "text_note"


def test_a_transcript_only_task_records_no_fallback(monkeypatch, events, ids):
    monkeypatch.setattr(local_intake_flow, "auto_note_enabled", lambda: True)
    monkeypatch.setattr(local_intake_flow, "auto_note_will_run", lambda: False)
    [task] = ids("transcript-only")

    _ctx(task, monkeypatch, skip_summary=True)

    assert not [e for e in events if e["event_name"] == "visual_note_unavailable"]


def test_events_name_the_entry_a_task_came_in_through(monkeypatch, ids):
    """Requirement: the event log says which entry a task came in through (a
    batch, a folder, a retry), not "/process" for all of them."""
    import inspect

    from backend.core import media_job

    monkeypatch.setattr(lp, "JOB_EVENTS", JobEventHub())
    lp._QUEUE_RECENT.clear()
    [task] = ids("route")
    seen: list[str] = []

    async def fake_pipeline(ctx):
        seen.append(ctx.event_route)

    monkeypatch.setattr(lp, "_run_pipeline", fake_pipeline)
    monkeypatch.setattr(lp.local_intake_flow, "note_is_wanted", lambda *_a: False)

    async def scenario():
        ctx = lp._local_media_job_context(
            task_id=task, client_id=None, source_type="audio", source_filename="talk.m4a",
            raw_title="talk", display_title="talk", suffix=".m4a", td="/tmp", in_path=Path("/tmp/talk.m4a"),
            content=b"", source_file_size_mb=1.0, duration_preflight_sec=60.0, options={},
        )
        await lp.start_media_job_behind_queue(ctx, route="/queue/process-folder")
        for _ in range(20):
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    lp._QUEUE_RECENT.clear()
    assert seen == ["/queue/process-folder"]
    # Guard: the pipeline writes the context's route, never a fixed one.
    assert 'route="/process"' not in inspect.getsource(media_job)


def test_audio_only_tasks_are_recorded_as_audio():
    """Requirement: an audio-only task is not recorded as an audio-and-video one."""
    from backend.core.event_context import pipeline_mode

    assert pipeline_mode("audio") == "audio"
    assert pipeline_mode("video") == "audio_video"
    assert pipeline_mode("transcript_file") == "transcript_file"


def test_a_finished_transcription_reports_the_whole_recording_transcribed():
    """Requirement: once transcription is done the task says so, with the
    recording's full length transcribed, not "preparing audio, 0 seconds"."""
    from types import SimpleNamespace

    from backend.core.media_job import _transcribed_total

    assert _transcribed_total(61.04, SimpleNamespace(duration=59.0)) == 61.0
    assert _transcribed_total(None, SimpleNamespace(duration=59.0)) == 59.0
    assert _transcribed_total(None, SimpleNamespace(duration=None)) is None
