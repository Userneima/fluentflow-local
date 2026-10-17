"""A cut made of many parts is exactly as long as its cut list.

Requirement (2026-10-17): subtitles and the note's pictures are placed by the
cut list. Every rendered part came back one audio block longer, and joined
together the cut ran 1.35 s long over 32 minutes and 2.6 s over three hours,
so subtitles ran ahead of the sound by the end.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.core import silence_cuts as sc
from backend.core.silence_cuts import TimeRange

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _recording(target: Path) -> Path:
    subprocess.run(
        [shutil.which("ffmpeg"), "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=s=160x90:d=60:r=30",
         "-f", "lavfi", "-i", "sine=f=300:d=60:sample_rate=32000",
         "-shortest", "-c:v", "libx264", "-g", "30", "-c:a", "aac", "-ar", "32000", str(target)],
        check=True,
    )
    return target


def test_the_concat_list_holds_each_part_to_its_ranges(tmp_path):
    parts = [tmp_path / "part0000.mp4", tmp_path / "part0001.mp4"]
    batches = [[TimeRange(0.0, 1.5), TimeRange(2.0, 2.5)], [TimeRange(10.0, 10.25)]]

    listing = sc._concat_listing(parts, batches)

    assert "outpoint 2.000000\nduration 2.000000" in listing
    assert "outpoint 0.250000\nduration 0.250000" in listing


@needs_ffmpeg
def test_a_cut_of_many_small_parts_does_not_drift(tmp_path):
    source = _recording(tmp_path / "talk.mp4")
    keeps = [TimeRange(second + 0.1, second + 0.6) for second in range(0, 58)]
    expected = sum(keep.duration for keep in keeps)
    out = tmp_path / "cut.mp4"
    encode = ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-ar", "32000"]

    batches = sc.render_keeps(source, keeps, out, fps=30, ranges_per_batch=2, workers=2,
                              encode_args=encode, runner=sc.run_command)

    assert batches == 29
    # 29 parts, each one 32 ms audio block long without the fix: 0.93 s.
    assert sc.media_duration_seconds(out) == pytest.approx(expected, abs=0.1)
