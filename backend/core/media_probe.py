"""Low-level media probing used by the transcription pipeline."""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

FFPROBE_TIMEOUT_SECONDS = 10


def wav_duration_seconds(path: Path | str) -> float | None:
    """Read a duration straight out of a wav header, or None if it cannot."""
    try:
        with wave.open(str(path), "rb") as wav:
            frame_rate = wav.getframerate()
            if frame_rate > 0:
                return wav.getnframes() / frame_rate
    except Exception:
        return None
    return None


def media_duration_seconds(path: Path | str) -> float | None:
    """Duration in seconds for any media file, or None if it cannot be read.

    Tries the wav header first because it costs no process spawn, then falls
    back to ffprobe. The ffprobe call is bounded by a timeout: without one a
    stalled probe would hang whichever pipeline stage asked for the duration.
    """
    from_header = wav_duration_seconds(path)
    if from_header is not None:
        return from_header
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )
        value = float((result.stdout or "").strip())
        return value if value > 0 else None
    except Exception:
        return None
