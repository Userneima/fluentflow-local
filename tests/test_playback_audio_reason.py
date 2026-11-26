"""Why a task has no playback audio, in words the editor can show.

Requirement (to-do 2026-11-26): the editor's player, with nothing to play,
says in a sentence a user understands why there is no audio, never ffmpeg's
English; an agent gets ffmpeg's own text beside it.
"""

from __future__ import annotations

import re

import pytest

from backend.core.local_error_diagnostics import playback_audio_diagnosis


@pytest.mark.parametrize("error, cause", [
    ("av_interleaved_write_frame(): No space left on device", "磁盘满了"),
    ("Error opening output file: Invalid argument\nConversion failed!", "出错了"),
])
def test_the_reason_is_said_in_chinese_without_ffmpegs_words(error, cause):
    found = playback_audio_diagnosis({"playback_audio_error": error})
    assert cause in found["detail"]
    assert "转录稿和笔记不受影响" in found["detail"]
    assert not re.search(r"[A-Za-z]{3,}", found["title"] + found["detail"])
    assert found["error"] == error


@pytest.mark.parametrize("result", [None, {}, {"playback_audio_available": True}, {"playback_audio_error": "  "}])
def test_no_reason_when_nothing_failed(result):
    assert playback_audio_diagnosis(result) is None


@pytest.mark.parametrize("made_later", [
    {"artifacts": {"playback_audio": {"filename": "a_audio.mp3"}}},
    {"playback_audio_available": True},
])
def test_no_reason_once_the_audio_was_made_after_all(made_later):
    """A retry that made the audio leaves the old error on the result; the
    page and agents must not still say it failed."""
    assert playback_audio_diagnosis({"playback_audio_error": "No space left on device", **made_later}) is None
