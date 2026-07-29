"""Media upload intake helpers, extracted from server_helpers.py so both the
hosted and local editions can validate and persist an uploaded media file
without the hosted helper barrel. Depends only on shared storage_paths plus
stdlib; no server_helpers imports."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import BinaryIO

from backend.core.storage_paths import _source_storage_dir

ALLOWED_SUFFIXES = {
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v",
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus",
}

TRANSCRIPT_SUFFIXES = {".srt", ".vtt", ".txt", ".md"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus"}
VIDEO_SUFFIXES = ALLOWED_SUFFIXES - AUDIO_SUFFIXES
STREAM_COPY_CHUNK_BYTES = 1024 * 1024


class MediaIntakeSizeError(ValueError):
    """Raised after a streamed source crosses its configured byte limit."""

    def __init__(self, byte_count: int, max_bytes: int) -> None:
        super().__init__(f"Media source exceeds {max_bytes} bytes")
        self.byte_count = byte_count
        self.max_bytes = max_bytes


def source_type_for_suffix(suffix: str) -> str:
    if suffix in TRANSCRIPT_SUFFIXES:
        return "transcript_file"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return "unknown"


def file_size_mb(byte_count: int | None) -> float | None:
    if byte_count is None:
        return None
    return round(byte_count / (1024 * 1024), 3)


def persist_source_file(task_id: str, suffix: str, content: bytes) -> Path:
    target_dir = _source_storage_dir() / task_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"source{suffix or '.bin'}"
    target.write_bytes(content)
    return target


def persist_source_stream(
    task_id: str,
    suffix: str,
    source: BinaryIO,
    *,
    max_bytes: int | None = None,
) -> tuple[Path, int]:
    """Atomically persist a file-like source without loading it all in memory."""
    target_dir = _source_storage_dir() / task_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"source{suffix or '.bin'}"
    tmp = target_dir / f".source.{uuid.uuid4().hex}.tmp"
    byte_count = 0
    try:
        with tmp.open("wb") as output:
            while True:
                chunk = source.read(STREAM_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                byte_count += len(chunk)
                if max_bytes is not None and byte_count > max_bytes:
                    raise MediaIntakeSizeError(byte_count, max_bytes)
                output.write(chunk)
        tmp.replace(target)
    except Exception:
        tmp.unlink(missing_ok=True)
        try:
            target_dir.rmdir()
        except OSError:
            pass
        raise
    return target, byte_count


def copy_source_file(task_id: str, suffix: str, source_path: Path | str) -> Path:
    target_dir = _source_storage_dir() / task_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"source{suffix or '.mp4'}"
    tmp = target_dir / f".source.{uuid.uuid4().hex}.tmp"
    try:
        shutil.copyfile(str(source_path), tmp)
        tmp.replace(target)
    except Exception:
        tmp.unlink(missing_ok=True)
        try:
            target_dir.rmdir()
        except OSError:
            pass
        raise
    return target


def path_size_mb(path: Path | str) -> float | None:
    try:
        return file_size_mb(Path(path).stat().st_size)
    except OSError:
        return None
