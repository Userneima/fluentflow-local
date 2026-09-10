"""Guards for the two ways a media job ends.

These paths run only when something goes wrong, so they are the least exercised
and the most expensive to get wrong: a run that ends without releasing its quota
silently bills the user for work they never received.
"""

import types

import pytest

from backend.core import media_job_outcome as outcome


class _StubProcess:
    def __init__(self, alive=True):
        self._alive = alive
        self.terminated = False

    def is_alive(self):
        return self._alive


@pytest.fixture
def ctx():
    released = []
    return types.SimpleNamespace(
        task_id_value="task-1",
        client_id="client-1",
        task_started_at=0.0,
        source_type="upload",
        source_filename="lecture.mp4",
        source_file_size_mb=12.5,
        do_lark=False,
        stt_provider_value="local",
        stt_provider_labeler=lambda name: f"label:{name}",
        friendly_error=lambda exc: f"friendly:{exc}",
        release_task_usage=lambda **kwargs: released.append(kwargs),
        released=released,
    )


@pytest.fixture
def recorded(monkeypatch):
    jobs, events = [], []
    monkeypatch.setattr(outcome, "upsert_job", lambda **kw: jobs.append(kw))
    monkeypatch.setattr(outcome, "log_event", lambda **kw: events.append(kw))
    monkeypatch.setattr(outcome, "terminate_process", lambda proc: setattr(proc, "terminated", True))
    return types.SimpleNamespace(jobs=jobs, events=events)


def test_a_cancelled_run_gives_its_quota_back(ctx, recorded):
    outcome.report_cancelled(outcome.TerminalReport(ctx=ctx, current_stage="stt"))
    assert len(ctx.released) == 1
    assert ctx.released[0]["task_id"] == "task-1"
    assert ctx.released[0]["client_id"] == "client-1"


def test_a_cancelled_run_is_recorded_as_cancelled_at_the_stage_it_reached(ctx, recorded):
    outcome.report_cancelled(outcome.TerminalReport(ctx=ctx, current_stage="stt"))
    job = recorded.jobs[-1]
    assert job["status"] == "cancelled"
    assert job["stage"] == "stt"
    assert job["error_reason"] == "client_disconnect"


def test_a_cancelled_run_stops_a_transcription_still_running(ctx, recorded):
    proc = _StubProcess(alive=True)
    outcome.report_cancelled(outcome.TerminalReport(ctx=ctx, stt_process=proc))
    assert proc.terminated is True


def test_a_cancelled_run_leaves_an_already_dead_transcription_alone(ctx, recorded):
    proc = _StubProcess(alive=False)
    outcome.report_cancelled(outcome.TerminalReport(ctx=ctx, stt_process=proc))
    assert proc.terminated is False


def test_a_cancelled_run_reports_the_estimate_when_no_duration_was_measured(ctx, recorded):
    outcome.report_cancelled(
        outcome.TerminalReport(ctx=ctx, duration_sec=None, duration_estimate_sec=61.44)
    )
    completed = [e for e in recorded.events if e["event_name"] == "task_completed"][-1]
    assert completed["source_duration_seconds"] == 61.4


def test_a_failed_run_gives_its_quota_back_and_returns_what_to_show(ctx, recorded):
    message = outcome.report_failed(
        outcome.TerminalReport(ctx=ctx, current_stage="stt"), RuntimeError("boom")
    )
    assert message == "friendly:boom"
    assert len(ctx.released) == 1


def test_a_failed_run_is_recorded_as_failed_with_no_progress(ctx, recorded):
    outcome.report_failed(
        outcome.TerminalReport(ctx=ctx, current_stage="stt"), RuntimeError("boom")
    )
    job = recorded.jobs[-1]
    assert job["status"] == "failed"
    assert job["progress"] == 0
    assert job["error_reason"] == "friendly:boom"


def test_dying_inside_the_summary_stage_marks_the_note_as_failed(ctx, recorded):
    """Otherwise the task shows a missing note with no reason attached to it."""
    report = outcome.TerminalReport(ctx=ctx, current_stage="summary", summary_status=None)
    outcome.report_failed(report, RuntimeError("boom"))
    assert report.summary_status == "failed"
    assert recorded.jobs[-1]["summary_status"] == "failed"


def test_dying_outside_the_summary_stage_does_not_invent_a_note_status(ctx, recorded):
    report = outcome.TerminalReport(ctx=ctx, current_stage="stt", summary_status=None)
    outcome.report_failed(report, RuntimeError("boom"))
    assert report.summary_status is None


def test_a_summary_status_already_set_is_not_overwritten(ctx, recorded):
    report = outcome.TerminalReport(ctx=ctx, current_stage="summary", summary_status="skipped")
    outcome.report_failed(report, RuntimeError("boom"))
    assert report.summary_status == "skipped"


def test_the_cloud_transcription_detail_survives_into_the_failure_record(ctx, recorded):
    report = outcome.TerminalReport(
        ctx=ctx, current_stage="stt", cloud_stt_metadata={"stt_request_id": "req-9"}
    )
    outcome.report_failed(report, RuntimeError("boom"))
    failed = [e for e in recorded.events if e["event_name"] == "task_failed"][-1]
    assert failed["metadata"]["stt_request_id"] == "req-9"
    assert recorded.jobs[-1]["metadata"]["stt_request_id"] == "req-9"


def test_a_job_context_without_quota_release_is_tolerated(recorded):
    """The local composition root passes no quota policy at all."""
    ctx = types.SimpleNamespace(
        task_id_value="t", client_id=None, task_started_at=0.0,
        source_type=None, source_filename=None, source_file_size_mb=None,
        do_lark=False, stt_provider_value=None, stt_provider_labeler=None,
        friendly_error=lambda exc: str(exc), release_task_usage=None,
    )
    outcome.report_cancelled(outcome.TerminalReport(ctx=ctx))
    assert recorded.jobs[-1]["status"] == "cancelled"
