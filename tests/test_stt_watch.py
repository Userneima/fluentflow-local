"""Requirement (2026-10-14): a transcription is stopped only when it has gone
quiet for the stale limit, never for being long. A recording that keeps sending
progress runs as long as it takes."""

from backend.core.media_job import _SttWatch


def test_a_long_transcription_that_keeps_moving_is_not_stopped():
    watch = _SttWatch(limit_seconds=7200, now=0)
    for minute in range(1, 10 * 60):  # ten hours, a progress message each minute
        watch.heard(minute * 60)
        assert not watch.stalled(minute * 60 + 30)


def test_a_transcription_quiet_for_the_limit_is_stopped():
    watch = _SttWatch(limit_seconds=7200, now=0)
    watch.heard(3000)
    assert not watch.stalled(3000 + 7200)
    assert watch.stalled(3000 + 7201)
