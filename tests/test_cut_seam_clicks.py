"""No click at the seams of a de-breathed file (2026-10-11).

Requirements:
1. Where two kept ranges meet, the sound does not jump: a recording cut from a
   waveform peak straight into a trough is rendered without a sample-to-sample
   step larger than the waveform itself ever makes.
2. The cut list stays the timeline: the rendered length is the sum of the kept
   ranges, so subtitles and notes keep pointing at the right second. The fades
   happen inside each kept range.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from backend.core import silence_cuts as sc

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

RATE = 48000
FREQ = 200  # slow enough that the waveform's own steps are small
AMPLITUDE = 0.8
# Ends on a peak (200.25 cycles) and resumes on a trough (400.75 cycles): a hard
# splice jumps from +0.8 to -0.8 in one sample.
KEEPS = [sc.TimeRange(0.0, 1.00125), sc.TimeRange(2.00375, 3.0)]
PCM = ("-c:a", "pcm_s16le", "-ar", str(RATE), "-ac", "1")


def _samples(path: Path) -> list[float]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"],
        check=True, capture_output=True,
    ).stdout
    count = len(raw) // 2
    return [value / 32768 for value in struct.unpack(f"<{count}h", raw[: count * 2])]


def test_a_peak_to_trough_cut_renders_without_a_click(tmp_path):
    source = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         f"aevalsrc={AMPLITUDE}*sin(2*PI*{FREQ}*t):s={RATE}:d=4", *PCM, "-y", str(source)],
        check=True,
    )
    output = tmp_path / "cut.wav"

    sc.render_keeps(source, KEEPS, output, audio_only=True, encode_args=PCM, work_dir=tmp_path / "work")

    samples = _samples(output)
    natural_step = 2 * 3.14159 * FREQ / RATE * AMPLITUDE  # the largest step the tone makes
    largest = max(abs(b - a) for a, b in zip(samples, samples[1:]))
    assert largest < natural_step * 2, f"a jump of {largest:.3f} at a seam (tone steps at most {natural_step:.3f})"

    expected = sum(keep.duration for keep in KEEPS)
    assert abs(len(samples) / RATE - expected) < 0.005
