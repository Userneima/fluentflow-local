from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.core import voice_enhance

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


def _synth(path: Path, *, low_gain: float, presence_gain: float, seconds: int = 6) -> Path:
    """Write a tone pair standing in for a voice: fundamental plus consonant band."""
    expr = (
        f"{low_gain}*sin(2*PI*200*t)+{presence_gain}*sin(2*PI*3000*t)"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-nostdin", "-v", "error",
            "-f", "lavfi",
            "-i", f"aevalsrc={expr}:s=48000:d={seconds}",
            "-ac", "1", "-c:a", "pcm_s16le", str(path),
        ],
        check=True, capture_output=True,
    )
    return path


@needs_ffmpeg
def test_muffled_input_reads_as_a_presence_deficit(tmp_path) -> None:
    muffled = _synth(tmp_path / "muffled.wav", low_gain=0.5, presence_gain=0.005)
    reading = voice_enhance.measure_presence(muffled)
    assert reading.deficit_db < -20


@needs_ffmpeg
def test_bright_input_does_not_read_as_a_deficit(tmp_path) -> None:
    bright = _synth(tmp_path / "bright.wav", low_gain=0.05, presence_gain=0.5)
    reading = voice_enhance.measure_presence(bright)
    assert reading.deficit_db > -6


@needs_ffmpeg
def test_enhancement_lifts_the_consonant_band_it_promises_to_lift(tmp_path) -> None:
    """The point of the feature: a muffled take comes back with more presence."""
    muffled = _synth(tmp_path / "muffled.wav", low_gain=0.5, presence_gain=0.005)
    before = voice_enhance.measure_presence(muffled)

    enhanced = voice_enhance.enhance_voice(muffled, output_path=tmp_path / "fixed.m4a")
    after = voice_enhance.measure_presence(enhanced)

    assert enhanced.is_file()
    assert after.deficit_db > before.deficit_db + 5


@needs_ffmpeg
def test_enhanced_output_keeps_the_declared_sample_rate(tmp_path) -> None:
    """loudnorm reports 192 kHz as preferred; the output must not follow it."""
    src = _synth(tmp_path / "src.wav", low_gain=0.4, presence_gain=0.02)
    enhanced = voice_enhance.enhance_voice(src, output_path=tmp_path / "out.m4a")

    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate",
            "-of", "default=nw=1:nk=1", str(enhanced),
        ],
        check=True, capture_output=True, text=True,
    )
    assert int(completed.stdout.strip()) == voice_enhance.ENHANCED_SAMPLE_RATE


@needs_ffmpeg
def test_measurement_samples_a_window_from_the_middle(tmp_path) -> None:
    src = _synth(tmp_path / "long.wav", low_gain=0.4, presence_gain=0.02, seconds=30)
    reading = voice_enhance.measure_presence(src, sample_seconds=10)
    assert reading.sampled_seconds == pytest.approx(10, abs=0.01)
    assert reading.offset_seconds == pytest.approx(10, abs=0.01)


@needs_ffmpeg
def test_measurement_window_shrinks_to_fit_a_short_clip(tmp_path) -> None:
    src = _synth(tmp_path / "short.wav", low_gain=0.4, presence_gain=0.02, seconds=4)
    reading = voice_enhance.measure_presence(src, sample_seconds=90)
    assert reading.sampled_seconds == pytest.approx(4, abs=0.2)
    assert reading.offset_seconds == pytest.approx(0, abs=0.2)


def test_enhance_refuses_to_overwrite_its_source(tmp_path) -> None:
    src = tmp_path / "same.m4a"
    src.touch()
    with pytest.raises(ValueError, match="refuses to overwrite"):
        voice_enhance.enhance_voice(src, output_path=src)


def test_enhance_rejects_a_missing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        voice_enhance.enhance_voice(tmp_path / "nope.m4a")


def test_measure_rejects_a_missing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        voice_enhance.measure_presence(tmp_path / "nope.m4a")


def test_silent_input_is_rejected_rather_than_reported_as_minus_infinity(monkeypatch, tmp_path) -> None:
    audio = tmp_path / "silent.wav"
    audio.touch()
    monkeypatch.setattr(voice_enhance, "_require", lambda binary: binary)
    monkeypatch.setattr(voice_enhance, "_media_duration_seconds", lambda path: 60.0)
    monkeypatch.setattr(
        voice_enhance.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="",
            stderr="[Parsed_astats_1] RMS level dB: -inf\n",
        ),
    )
    with pytest.raises(RuntimeError, match="没有检测到可测量的声音"):
        voice_enhance.measure_presence(audio)


def test_unparseable_stats_output_is_rejected(monkeypatch, tmp_path) -> None:
    audio = tmp_path / "weird.wav"
    audio.touch()
    monkeypatch.setattr(voice_enhance, "_require", lambda binary: binary)
    monkeypatch.setattr(voice_enhance, "_media_duration_seconds", lambda path: 60.0)
    monkeypatch.setattr(
        voice_enhance.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr="nothing useful here\n",
        ),
    )
    with pytest.raises(RuntimeError, match="无法测量音频频段能量"):
        voice_enhance.measure_presence(audio)


def test_reading_reports_the_deficit_it_measured() -> None:
    reading = voice_enhance.PresenceReading(
        full_band_dbfs=-20.0, presence_dbfs=-39.0,
        sampled_seconds=90.0, offset_seconds=120.0,
    )
    assert reading.deficit_db == pytest.approx(-19.0)
    assert reading.as_dict()["presence_deficit_db"] == pytest.approx(-19.0)


def test_the_recognizer_reads_the_corrected_take_when_there_is_one(tmp_path) -> None:
    original = tmp_path / "source.m4a"
    corrected = tmp_path / "source_enhanced.m4a"

    source, take = voice_enhance.stt_audio_source(corrected, original)

    assert source == corrected
    assert take == "enhanced"


def test_the_recognizer_falls_back_to_the_original_when_correcting_failed(tmp_path) -> None:
    """Clarity is optional; the transcript is not."""
    original = tmp_path / "source.m4a"

    source, take = voice_enhance.stt_audio_source(None, original)

    assert source == original
    assert take == "plain"
