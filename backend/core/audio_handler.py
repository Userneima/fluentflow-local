"""Extract/compress audio from video or audio files (for downstream STT)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from backend.core import ffmpeg_cmd
from backend.core.local_error_diagnostics import NO_SPEECH

SILENCE_PEAK_THRESHOLD_DB = -80.0


def _require_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH; it is required for audio processing")
    return ffmpeg


def require_audible_audio(audio_path: str | Path) -> float:
    """Return the audio peak level or reject a digitally silent STT input."""
    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {path}")

    ffmpeg = _require_ffmpeg()
    # ``-vn``: a video's picture is not decoded only to be thrown away.
    completed = ffmpeg_cmd.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"max_volume:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*dB", completed.stderr)
    if not match:
        raise RuntimeError("无法检测音频响度，请重新提交媒体文件。")

    peak_db = float(match.group(1))
    if peak_db <= SILENCE_PEAK_THRESHOLD_DB:
        raise RuntimeError(NO_SPEECH)
    return peak_db


class CompressedMp3InBackground:
    """The playback MP3 written while the rest of the task goes on.

    Nothing the transcription does reads it, and on long recordings it is most
    of what extracting the audio costs: LAME encodes on one core, about 1.5
    minutes of a 3 h 20 min recording against 22 seconds for the recognizer's
    WAV (2026-11-25). ``path()`` waits for it and raises when
    ffmpeg failed; ``stop()`` ends an encode nobody will
    wait for, so a cancelled task does not leave it running.
    """

    def __init__(self, input_path: str | Path, *, output_path: str | Path, bitrate: str = "64k") -> None:
        src = Path(input_path).expanduser().resolve()
        if not src.is_file():
            raise FileNotFoundError(f"File not found: {src}")
        self._out = Path(output_path).expanduser().resolve()
        self._out.parent.mkdir(parents=True, exist_ok=True)
        self._args = [_require_ffmpeg(), "-y", "-nostdin", "-v", "error", "-i", str(src), "-vn",
                      "-codec:a", "libmp3lame", "-b:a", bitrate, str(self._out)]
        self._process = ffmpeg_cmd.popen(self._args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def path(self) -> Path:
        _, stderr = self._process.communicate()
        if self._process.returncode != 0:
            raise subprocess.CalledProcessError(self._process.returncode, self._args, stderr=stderr)
        return self._out

    def stop(self) -> None:
        # A cancelled task can stop the encode while ``path()`` still waits on it
        # in a worker thread, so this only kills and reaps: a second
        # ``communicate()`` on the same pipe is not safe across threads.
        if self._process.poll() is None:
            self._process.kill()
            self._process.wait()


def extract_stt_wav(
    input_path: str | Path,
    *,
    output_path: str | Path | None = None,
    sample_rate: int = 16_000,
) -> Path:
    """
    Prepare a mono PCM WAV optimized for Whisper-style STT.

    Avoiding lossy MP3 compression preserves speech detail, while 16 kHz mono
    keeps files small and matches Whisper's expected audio representation.
    """
    src = Path(input_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"File not found: {src}")

    if output_path is None:
        out = src.with_name(f"{src.stem}_fluentflow_stt.wav")
    else:
        out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = _require_ffmpeg()
    ffmpeg_cmd.run(
        [
            ffmpeg,
            "-y",
            "-nostdin",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-codec:a",
            "pcm_s16le",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out
