"""Guards for the transcript-cleanup stage of the media pipeline.

Cleanup rewrites what the engine returned, so the stage's job is as much about
what it keeps as what it strips: the raw take and the word timings have to
survive, because getting them back means paying for the transcription again.
"""

import types

import pytest

from backend.core import media_job_stages as stages


def _segment(start, end, text, *, speaker=None, words=None):
    return types.SimpleNamespace(
        start=start, end=end, text=text, speaker=speaker, words=words
    )


def _ctx():
    return types.SimpleNamespace(
        task_id_value="task-1",
        source_type="upload",
        source_filename="talk.mp4",
        source_file_size_mb=4.0,
    )


@pytest.fixture
def events(monkeypatch):
    seen = []
    monkeypatch.setattr(stages, "log_event", lambda **kw: seen.append(kw))
    return seen


def _stub_cleanup(monkeypatch, *, applied_count, cleaned_text="clean", cleaned_segments=()):
    result = types.SimpleNamespace(
        cleaned_text=cleaned_text,
        cleaned_segments=list(cleaned_segments),
        cleaned_length=len(cleaned_text),
        raw_length=99,
        applied_count=applied_count,
        removed_segment_count=applied_count,
        issues=["repeat"] * applied_count,
    )
    monkeypatch.setattr(stages, "clean_repeated_transcript", lambda segs: result)
    return result


def test_a_transcript_that_needed_no_cleanup_logs_nothing(events, monkeypatch):
    _stub_cleanup(monkeypatch, applied_count=0)
    transcription = types.SimpleNamespace(segments=[_segment(0, 1, "hi")])
    stages.clean_transcript(_ctx(), transcription=transcription, duration_sec=10.0)
    assert events == []


def test_cleanup_that_changed_something_is_recorded_with_its_counts(events, monkeypatch):
    _stub_cleanup(monkeypatch, applied_count=3)
    transcription = types.SimpleNamespace(segments=[_segment(0, 1, "hi hi hi")])
    stages.clean_transcript(_ctx(), transcription=transcription, duration_sec=10.0)
    logged = events[-1]
    assert logged["event_name"] == "transcript_cleanup_completed"
    assert logged["stage"] == "transcript_cleanup"
    assert logged["metadata"]["cleanup_applied_count"] == 3
    assert logged["metadata"]["cleanup_removed_segment_count"] == 3
    assert logged["metadata"]["cleanup_raw_length"] == 99


def test_the_whole_cleanup_result_is_handed_back(events, monkeypatch):
    """The job record keeps both takes, so the caller needs more than the text."""
    stub = _stub_cleanup(monkeypatch, applied_count=1, cleaned_text="cleaned")
    transcription = types.SimpleNamespace(segments=[_segment(0, 1, "hi")])
    result, _raw = stages.clean_transcript(
        _ctx(), transcription=transcription, duration_sec=10.0
    )
    assert result is stub


def test_word_timings_the_engine_charged_for_are_kept(events, monkeypatch):
    """Recovering these costs another transcription, so they are never dropped."""
    _stub_cleanup(monkeypatch, applied_count=0)
    words = [{"word": "hello", "start": 0.0, "end": 0.4}]
    transcription = types.SimpleNamespace(segments=[_segment(0, 1, "hello", words=words)])
    _result, raw = stages.clean_transcript(
        _ctx(), transcription=transcription, duration_sec=10.0
    )
    assert raw[0]["words"] == words


def test_the_raw_take_survives_cleanup(events, monkeypatch):
    _stub_cleanup(monkeypatch, applied_count=2, cleaned_segments=[{"text": "hi"}])
    transcription = types.SimpleNamespace(
        segments=[_segment(0, 1, "hi hi"), _segment(1, 2, "there")]
    )
    _result, raw = stages.clean_transcript(
        _ctx(), transcription=transcription, duration_sec=10.0
    )
    assert [s["text"] for s in raw] == ["hi hi", "there"]


def test_speaker_labels_survive_into_the_raw_segments(events, monkeypatch):
    _stub_cleanup(monkeypatch, applied_count=0)
    transcription = types.SimpleNamespace(segments=[_segment(0, 1, "hi", speaker="A")])
    _result, raw = stages.clean_transcript(
        _ctx(), transcription=transcription, duration_sec=10.0
    )
    assert raw[0]["speaker"] == "A"


def test_a_segment_without_word_timings_carries_no_empty_key(events, monkeypatch):
    _stub_cleanup(monkeypatch, applied_count=0)
    transcription = types.SimpleNamespace(segments=[_segment(0, 1, "hi")])
    _result, raw = stages.clean_transcript(
        _ctx(), transcription=transcription, duration_sec=10.0
    )
    assert "words" not in raw[0]


def test_an_unmeasured_duration_does_not_crash_the_cleanup_event(events, monkeypatch):
    _stub_cleanup(monkeypatch, applied_count=1)
    transcription = types.SimpleNamespace(segments=[_segment(0, 1, "hi")])
    stages.clean_transcript(_ctx(), transcription=transcription, duration_sec=None)
    assert events[-1]["source_duration_seconds"] is None
