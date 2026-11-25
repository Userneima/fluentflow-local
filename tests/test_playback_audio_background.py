"""The playback MP3 encodes beside the transcription (2026-11-25).

What should hold:
- the transcription starts without waiting for the playback MP3, and the MP3
  is waited for before the result records it;
- the background encode writes the same kind of file the step wrote before,
  fails loudly when ffmpeg fails, and is stopped when the task ends without it;
- the digital-silence check reads the recognizer's WAV, not a third pass.
"""
from __future__ import annotations

import inspect
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from backend.core import audio_handler, media_job
from backend.core.audio_handler import CompressedMp3InBackground

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _tone(path: Path, seconds: int) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-ac", "2", "-ar", "48000", str(path)],
        check=True,
    )
    return path


def test_the_transcription_starts_before_the_playback_mp3_is_waited_for():
    source = inspect.getsource(media_job._stream_media_job)
    started = source.index("pending_playback = CompressedMp3InBackground(")
    transcribing = source.index("start_transcription_process(")
    waited = source.index("pending_playback.path")
    recorded = source.index("_transcribed_checkpoint(")
    assert started < transcribing < waited < recorded
    assert "extract_compressed_mp3" not in source


def test_a_task_that_ends_early_stops_the_encode():
    source = inspect.getsource(media_job._stream_media_job)
    finally_block = source[source.rindex("    finally:"):]
    assert "pending_playback.stop()" in finally_block


@needs_ffmpeg
def test_the_background_encode_writes_an_mp3(tmp_path):
    source = _tone(tmp_path / "tone.wav", 3)
    pending = CompressedMp3InBackground(source, output_path=tmp_path / "out" / "playback.mp3")
    out = pending.path()
    assert out == (tmp_path / "out" / "playback.mp3").resolve()
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert probe.stdout.strip() == "mp3"


@needs_ffmpeg
def test_a_failed_encode_raises_when_waited_for(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a recording")
    pending = CompressedMp3InBackground(broken, output_path=tmp_path / "playback.mp3")
    with pytest.raises(subprocess.CalledProcessError):
        pending.path()


@needs_ffmpeg
def test_stop_ends_a_running_encode(tmp_path):
    source = _tone(tmp_path / "long.wav", 1800)
    pending = CompressedMp3InBackground(source, output_path=tmp_path / "playback.mp3")
    started = time.monotonic()
    pending.stop()
    assert time.monotonic() - started < 5
    assert pending._process.returncode is not None
    pending.stop()  # stopping twice is harmless


@needs_ffmpeg
def test_the_silence_check_reads_a_video_without_its_picture(tmp_path, monkeypatch):
    seen: list[list[str]] = []
    real_run = subprocess.run

    def run(args, **kwargs):
        seen.append(list(args))
        return real_run(args, **kwargs)

    monkeypatch.setattr(audio_handler.subprocess, "run", run)
    audio_handler.require_audible_audio(_tone(tmp_path / "tone.wav", 2))
    assert "-vn" in seen[-1]
