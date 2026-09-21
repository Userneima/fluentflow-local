"""Guards for the Lark export stage of the media pipeline.

The stage's one load-bearing property is that it never raises: by the time it
runs, the transcript and the note are finished and worth keeping. An export
failure that escaped would send the whole task down the failure path and throw
away work the user already paid for.
"""

import asyncio
import types

import pytest

from backend.core import media_job_stages as stages


class _Loop:
    async def run_in_executor(self, _executor, func):
        return func()


def _ctx(exporter, **overrides):
    base = dict(
        task_id_value="task-1",
        source_type="upload",
        source_filename="lecture.mp4",
        source_file_size_mb=12.5,
        display_title_value="Lecture",
        title="Lecture",
        lark_export_route="cloud",
        lark_via_cli=False,
        lark_app_id="app",
        lark_app_secret="secret",
        folder_token="folder",
        account_user=None,
        auto_lark_exporter=exporter,
        friendly_error=lambda exc: f"friendly:{exc}",
        loop=_Loop(),
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _run(ctx, result=None, duration_sec=61.44, transcript_text="hello", summary_md="# note"):
    return asyncio.run(
        stages.export_note_to_lark(
            ctx,
            result=result if result is not None else {},
            duration_sec=duration_sec,
            transcript_text=transcript_text,
            summary_md=summary_md,
        )
    )


@pytest.fixture
def events(monkeypatch):
    seen = []
    monkeypatch.setattr(stages, "log_event", lambda **kw: seen.append(kw))
    return seen


def test_a_successful_export_records_the_document_on_the_result(events):
    ctx = _ctx(lambda **kw: {
        "doc_title": "Lecture Notes",
        "export_target": "wiki",
        "response": {"url": "https://example.invalid/doc"},
    })
    result = {}
    assert _run(ctx, result) is True
    assert result["lark_doc_title"] == "Lecture Notes"
    assert result["lark_response"]["url"] == "https://example.invalid/doc"
    assert "lark_error" not in result


def test_a_failed_export_does_not_sink_the_finished_note(events):
    """The note is already written; a failed export must not raise past here."""
    def _explode(**_kw):
        raise RuntimeError("feishu said no")

    result = {}
    assert _run(_ctx(_explode), result) is False
    assert result["lark_error"] == "friendly:feishu said no"
    assert "lark_doc_title" not in result


def test_a_failed_export_is_logged_as_a_completed_attempt_that_did_not_succeed(events):
    def _explode(**_kw):
        raise RuntimeError("feishu said no")

    _run(_ctx(_explode))
    completed = [e for e in events if e["event_name"] == "lark_export_completed"][-1]
    assert completed["success"] is False
    assert completed["error_reason"] == "friendly:feishu said no"
    assert completed["metadata"]["raw_error"] == "feishu said no"


def test_the_document_url_is_carried_into_the_completion_event(events):
    ctx = _ctx(lambda **kw: {
        "doc_title": "Lecture Notes",
        "export_target": "wiki",
        "response": {"url": "https://example.invalid/doc"},
    })
    _run(ctx)
    completed = [e for e in events if e["event_name"] == "lark_export_completed"][-1]
    assert completed["feishu_doc_url"] == "https://example.invalid/doc"
    assert completed["export_target"] == "wiki"


def test_a_response_that_is_not_a_mapping_does_not_break_the_event(events):
    ctx = _ctx(lambda **kw: {
        "doc_title": "Lecture Notes",
        "export_target": "wiki",
        "response": "plain text response",
    })
    assert _run(ctx) is True
    completed = [e for e in events if e["event_name"] == "lark_export_completed"][-1]
    assert completed["feishu_doc_url"] is None


def test_both_events_describe_the_same_source(events):
    ctx = _ctx(lambda **kw: {"doc_title": "d", "export_target": "wiki", "response": {}})
    _run(ctx)
    started = [e for e in events if e["event_name"] == "lark_export_started"][-1]
    completed = [e for e in events if e["event_name"] == "lark_export_completed"][-1]
    for field in ("source_filename", "source_duration_seconds", "transcript_length", "stage"):
        assert started[field] == completed[field]
    assert started["source_duration_seconds"] == 61.4


def test_an_unmeasured_duration_does_not_crash_the_export(events):
    """A None duration used to hit `round(None, 1)` and fail a finished task."""
    ctx = _ctx(lambda **kw: {"doc_title": "d", "export_target": "wiki", "response": {}})
    assert _run(ctx, duration_sec=None) is True
    started = [e for e in events if e["event_name"] == "lark_export_started"][-1]
    assert started["source_duration_seconds"] is None


def test_a_context_with_no_export_policy_is_a_programming_error(events):
    with pytest.raises(RuntimeError, match="automatic Lark export policy"):
        _run(_ctx(None))


def test_the_filename_stem_falls_back_to_the_source_when_untitled(events):
    seen = {}

    def _exporter(**kw):
        seen.update(kw)
        return {"doc_title": "d", "export_target": "wiki", "response": {}}

    _run(_ctx(_exporter, display_title_value="", title=""))
    assert seen["filename_stem"] == "lecture"
