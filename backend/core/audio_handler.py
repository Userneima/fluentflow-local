"""Extract/compress audio from video or audio files (for downstream STT)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from backend.core import device_profile, ffmpeg_cmd
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
    ffmpeg failed; ``failure()`` says so without waiting; ``stop()`` ends an
    encode nobody will wait for, so a cancelled task does not leave it running.

    ffmpeg's messages go to a file beside the MP3, not a pipe: on a damaged
    recording it writes a line per bad packet, and a pipe nobody reads until
    the transcription ends filled up and held the encode still (a 10-minute
    AAC with a corrupted ``mdat`` wrote 1.9 MB and stopped after 0 bytes).
    """

    _ERROR_TAIL_BYTES = 4000

    def __init__(self, input_path: str | Path, *, output_path: str | Path, bitrate: str = "64k") -> None:
        src = Path(input_path).expanduser().resolve()
        if not src.is_file():
            raise FileNotFoundError(f"File not found: {src}")
        self._out = Path(output_path).expanduser().resolve()
        self._out.parent.mkdir(parents=True, exist_ok=True)
        self._log = self._out.with_name(f"{self._out.name}.log")
        self._args = [_require_ffmpeg(), "-y", "-nostdin", "-v", "error", "-i", str(src), "-vn",
                      "-codec:a", "libmp3lame", "-b:a", bitrate, str(self._out)]
        # The child keeps its own handle; this one is closed once it started.
        with self._log.open("wb") as log:
            self._process = ffmpeg_cmd.popen(self._args, stdout=subprocess.DEVNULL, stderr=log)
        # Held to the CPU share the user chose, like every other encode.
        device_profile.lower_priority(self._process.pid)

    def failure(self) -> subprocess.CalledProcessError | None:
        """The error of an encode that already ended badly; None while it
        runs and after it succeeded."""
        returncode = self._process.poll()
        if returncode is None or returncode == 0:
            return None
        return subprocess.CalledProcessError(returncode, self._args, stderr=self._error_tail())

    def path(self) -> Path:
        self._process.wait()
        error = self.failure()
        if error is not None:
            raise error
        return self._out

    def stop(self) -> None:
        # A cancelled task can stop the encode while ``path()`` still waits on it
        # in a worker thread, so this only kills and reaps.
        if self._process.poll() is None:
            self._process.kill()
            self._process.wait()

    def _error_tail(self) -> str:
        try:
            with self._log.open("rb") as log:
                log.seek(0, 2)
                log.seek(max(log.tell() - self._ERROR_TAIL_BYTES, 0))
                return log.read().decode("utf-8", errors="replace")
        except OSError:
            return ""


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
