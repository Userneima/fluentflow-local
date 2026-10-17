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


def _child(result=None, error=None):
    """Stands in for ``diarize_in_child``: the separation as the child returns it."""
    async def run(_path, *, budget_seconds):
        if error is not None:
            raise error
        return result if result is not None else []
    return run


def test_a_local_run_relabels_the_segments(events, monkeypatch):
    monkeypatch.setattr(stages, "diarize_in_child", _child([_Turn("A"), _Turn("B")]))
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
    """A stalled diarization run must not hold the task forever."""
    monkeypatch.setattr(stages, "_diarization_timeout_seconds", lambda _d: 600.0)
    monkeypatch.setattr(stages, "diarize_in_child", _child(error=asyncio.TimeoutError()))
    original = [{"text": "hi"}]
    segments, payload = _run(_ctx(), segments=original)
    assert payload["applied"] is False
    assert "10 分钟上限" in payload["error_reason"] and "照常出" in payload["error_reason"]
    assert segments == original


def test_a_local_run_that_blows_up_is_skipped_not_raised(events, monkeypatch):
    monkeypatch.setattr(stages, "diarize_in_child", _child(error=RuntimeError("no model on disk")))
    original = [{"text": "hi"}]
    segments, payload = _run(_ctx(), segments=original)
    assert payload["applied"] is False
    assert payload["error_reason"] == "no model on disk"
    assert segments == original


def test_a_task_that_did_not_ask_for_labels_runs_nothing(events, monkeypatch):
    monkeypatch.setattr(stages, "diarize_in_child", _child(error=AssertionError("diarization ran without being asked")))
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
    monkeypatch.setattr(stages, "diarize_in_child", _child([]))
    monkeypatch.setattr(stages, "assign_speakers_to_segments", lambda segs, turns: segs)
    _, payload = _run(_ctx(), segments=[{"text": "hi"}])
    assert payload["available"] is False


def test_the_limit_grows_with_the_recording_and_leaves_room_for_a_busy_machine():
    # A short clip still gets a floor: loading the models is slower than the audio.
    assert stages._diarization_timeout_seconds(None) == 900.0
    assert stages._diarization_timeout_seconds(120) == 900.0
    # The slowest run measured, a 20-minute meeting on a busy machine (0.62x), fits.
    assert stages._diarization_timeout_seconds(1229) > 0.62 * 1229
    # A five-hour recording at the measured pace (about 0.14x) is not cut off.
    assert stages._diarization_timeout_seconds(5 * 3600) > 0.14 * 5 * 3600 * 2
    # A run of many hours on a 3.5-hour recording is.
    assert stages._diarization_timeout_seconds(12878) < 4 * 3600


def test_asking_on_a_machine_without_diarization_is_a_skip_not_a_failure(events, monkeypatch):
    monkeypatch.setattr(stages, "diarization_status", lambda: {"available": False})

    monkeypatch.setattr(stages, "diarize_in_child",
                        _child(error=AssertionError("nothing to run on a machine without the packages")))
    segments, payload = _run(_ctx(), segments=[{"text": "hi"}])

    assert payload["applied"] is False
    assert "已跳过" in payload["skipped_reason"]
    assert "error_reason" not in payload
    assert [event["event_name"] for event in events] == ["speaker_diarization_skipped"]
    assert events[0]["success"] is True
    assert segments == [{"text": "hi"}]
