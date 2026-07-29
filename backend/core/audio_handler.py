"""Extract/compress audio from video or audio files (for downstream STT)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus", ".webm"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".ts", ".m4v"}
SILENCE_PEAK_THRESHOLD_DB = -80.0


def _is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_SUFFIXES


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
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"max_volume:\s*(-?(?:\d+(?:\.\d+)?|inf))\s*dB", completed.stderr)
    if not match:
        raise RuntimeError("无法检测音频响度，请重新提交媒体文件。")

    peak_db = float(match.group(1))
    if peak_db <= SILENCE_PEAK_THRESHOLD_DB:
        raise RuntimeError("音频中没有检测到可转录的声音，请确认录制时包含系统声音或麦克风声音后重新提交。")
    return peak_db


def extract_compressed_mp3(
    input_path: str | Path,
    *,
    output_path: str | Path | None = None,
    bitrate: str = "64k",
) -> Path:
    """
    Prepare a compressed MP3 from a video **or** audio source.

    Uses ffmpeg directly for both video and audio — avoids moviepy compatibility
    issues and works reliably across all ffmpeg versions.

    Returns:
        Absolute path to the written MP3.
    """
    src = Path(input_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"File not found: {src}")

    if output_path is None:
        out = src.with_name(f"{src.stem}_fluentflow.mp3")
    else:
        out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = _require_ffmpeg()
    subprocess.run(
        [ffmpeg, "-y", "-i", str(src), "-vn", "-codec:a", "libmp3lame", "-b:a", bitrate, str(out)],
        check=True, capture_output=True,
    )
    return out


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
    subprocess.run(
        [
            ffmpeg,
            "-y",
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
