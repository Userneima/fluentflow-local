"""Guards for the speaker-labelling stage of the media pipeline.

Speaker labels are optional everywhere, so the property that matters is that
none of the ways this stage can fail reach the caller: a transcript without
labels is still the thing the user asked for, and a raise here would send a
finished transcription down the failure path.
"""

import asyncio
import types

import pytest

from backend.core import media_job_stages as stages


class _Loop:
    def __init__(self, result=None, hang=False):
        self._result = result
        self._hang = hang

    async def run_in_executor(self, _executor, func):
        if self._hang:
            await asyncio.sleep(30)
        return func()


class _Turn:
    def __init__(self, speaker):
        self.speaker = speaker


def _ctx(**overrides):
    base = dict(
        task_id_value="task-1",
        source_type="upload",
        source_filename="meeting.mp4",
        source_file_size_mb=8.0,
        diarization_requested=True,
        stt_provider_value="local",
        stt_provider_labeler=lambda name: f"label:{name}",
        loop=_Loop(),
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _run(ctx, *, segments, duration_sec=120.0):
    return asyncio.run(
        stages.label_speakers(
            ctx,
            segments_payload=segments,
            duration_sec=duration_sec,
            audio_path="/tmp/audio.wav",
        )
    )


@pytest.fixture
def events(monkeypatch):
    seen = []
    monkeypatch.setattr(stages, "log_event", lambda **kw: seen.append(kw))
    monkeypatch.setattr(stages, "diarization_status", lambda: {"available": True})
    return seen


def test_a_local_run_relabels_the_segments(events, monkeypatch):
    monkeypatch.setattr(stages, "diarize_audio", lambda path: [_Turn("A"), _Turn("B")])
    monkeypatch.setattr(
        stages, "assign_speakers_to_segments",
        lambda segs, turns: [{"speaker": "A"}, {"speaker": "B"}],
    )
    segments, payload = _run(_ctx(), segments=[{"text": "hi"}, {"text": "yo"}])
    assert payload["applied"] is True
    assert payload["speaker_count"] == 2
    assert payload["turn_count"] == 2
    assert segments == [{"speaker": "A"}, {"speaker": "B"}]


def test_a_local_run_that_overruns_its_budget_is_skipped_not_raised(events, monkeypatch):
    """A stalled pyannote download must not hold the task forever."""
    monkeypatch.setattr(stages, "_diarization_timeout_seconds", lambda _d: 0.01)
    monkeypatch.setattr(stages, "diarize_audio", lambda path: [])
    original = [{"text": "hi"}]
    segments, payload = _run(_ctx(loop=_Loop(hang=True)), segments=original)
    assert payload["applied"] is False
    assert "预算" in payload["error_reason"]
    assert segments == original


def test_a_local_run_that_blows_up_is_skipped_not_raised(events, monkeypatch):
    def _explode(_path):
        raise RuntimeError("no model on disk")

    monkeypatch.setattr(stages, "diarize_audio", _explode)
    original = [{"text": "hi"}]
    segments, payload = _run(_ctx(), segments=original)
    assert payload["applied"] is False
    assert payload["error_reason"] == "no model on disk"
    assert segments == original


def test_a_task_that_did_not_ask_for_labels_runs_nothing(events, monkeypatch):
    def _never(_path):
        raise AssertionError("diarization ran without being asked")

    monkeypatch.setattr(stages, "diarize_audio", _never)
    _, payload = _run(_ctx(diarization_requested=False), segments=[{"text": "hi"}])
    assert payload["requested"] is False
    assert payload["applied"] is False
    assert events == []


def test_two_speakers_is_what_makes_a_note_carry_labels(events):
    _, payload = _run(
        _ctx(diarization_requested=False), segments=[{"speaker": "A"}, {"speaker": "B"}]
    )
    assert payload["segments_labeled"] is True


def test_one_speaker_is_not_worth_labelling(events):
    _, payload = _run(_ctx(diarization_requested=False), segments=[{"speaker": "A"}, {"speaker": "A"}])
    assert payload["segments_labeled"] is False


def test_a_local_run_reports_the_local_models_availability(events, monkeypatch):
    monkeypatch.setattr(stages, "diarization_status", lambda: {"available": False})
    monkeypatch.setattr(stages, "diarize_audio", lambda path: [])
    monkeypatch.setattr(stages, "assign_speakers_to_segments", lambda segs, turns: segs)
    _, payload = _run(_ctx(), segments=[{"text": "hi"}])
    assert payload["available"] is False


def test_the_budget_scales_with_the_audio_and_stays_bounded():
    assert stages._diarization_timeout_seconds(None) == 600.0
    assert stages._diarization_timeout_seconds(1229) == 2458.0
    assert stages._diarization_timeout_seconds(9000) == 2700.0
