"""Deterministic admission checks for uploaded media before it enters the queue."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.runtime_env import env_truthy


MEDIA_PREFLIGHT_ENABLED_ENV = "FLUENTFLOW_MEDIA_PREFLIGHT_ENABLED"
EMPTY_FILE_GUARD_ENV = "FLUENTFLOW_MEDIA_GUARD_EMPTY_FILE_ENABLED"
CONTAINER_GUARD_ENV = "FLUENTFLOW_MEDIA_GUARD_CONTAINER_ENABLED"
AUDIO_STREAM_GUARD_ENV = "FLUENTFLOW_MEDIA_GUARD_AUDIO_STREAM_ENABLED"
AUDIO_DECODE_GUARD_ENV = "FLUENTFLOW_MEDIA_GUARD_AUDIO_DECODE_ENABLED"
EXTENSION_GUARD_ENV = "FLUENTFLOW_MEDIA_GUARD_EXTENSION_ENABLED"
SILENCE_GUARD_ENV = "FLUENTFLOW_MEDIA_GUARD_SILENCE_ENABLED"


_FORMAT_ALIASES_BY_SUFFIX = {
    ".mp4": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".mov": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".m4v": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".m4a": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".avi": {"avi"},
    ".mkv": {"matroska", "webm"},
    ".webm": {"matroska", "webm"},
    ".wmv": {"asf"},
    ".wma": {"asf"},
    ".flv": {"flv"},
    ".mp3": {"mp3"},
    ".wav": {"wav"},
    ".flac": {"flac"},
    ".aac": {"aac", "adts"},
    ".ogg": {"ogg"},
    ".opus": {"ogg", "opus"},
}


def media_guard_enabled(name: str) -> bool:
    """Return whether a guard is active; all guards default to enabled."""
    if os.environ.get(MEDIA_PREFLIGHT_ENABLED_ENV) is not None and not env_truthy(MEDIA_PREFLIGHT_ENABLED_ENV):
        return False
    if os.environ.get(name) is None:
        return True
    return env_truthy(name)


class MediaPreflightError(RuntimeError):
    """A stable, user-safe media admission failure."""

    def __init__(self, code: str, message: str, *, metadata: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.metadata = metadata or {}


@dataclass(frozen=True)
class MediaPreflightResult:
    format_name: str | None
    audio_stream_count: int | None
    duration_seconds: float | None
    enabled_guards: tuple[str, ...]

    def as_metadata(self) -> dict[str, Any]:
        return {
            "format_name": self.format_name,
            "audio_stream_count": self.audio_stream_count,
            "duration_seconds": self.duration_seconds,
            "enabled_guards": list(self.enabled_guards),
        }


def _enabled_guards() -> tuple[str, ...]:
    guards = (
        (EMPTY_FILE_GUARD_ENV, "empty_file"),
        (CONTAINER_GUARD_ENV, "container"),
        (EXTENSION_GUARD_ENV, "extension"),
        (AUDIO_STREAM_GUARD_ENV, "audio_stream"),
        (AUDIO_DECODE_GUARD_ENV, "audio_decode"),
    )
    return tuple(label for name, label in guards if media_guard_enabled(name))


def _require_binary(name: str) -> str:
    path = shutil.which(name)
    if path:
        return path
    raise MediaPreflightError(
        "media_preflight_unavailable",
        "媒体预检暂不可用，请稍后重试。",
        metadata={"missing_binary": name},
    )


def _probe_media(path: Path) -> dict[str, Any]:
    ffprobe = _require_binary("ffprobe")
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration:stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        payload = json.loads(completed.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError) as exc:
        raise MediaPreflightError(
            "media_container_unreadable",
            "媒体文件无法读取，可能已损坏或文件内容与扩展名不匹配。",
        ) from exc
    return payload if isinstance(payload, dict) else {}


def _duration_seconds(payload: dict[str, Any]) -> float | None:
    raw = (payload.get("format") or {}).get("duration")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _matches_file_extension(path: Path, format_name: str | None) -> bool:
    expected_aliases = _FORMAT_ALIASES_BY_SUFFIX.get(path.suffix.lower())
    if not expected_aliases or not format_name:
        return True
    detected_aliases = {alias.strip().lower() for alias in format_name.split(",") if alias.strip()}
    return bool(expected_aliases & detected_aliases)


def _verify_first_audio_segment(path: Path) -> None:
    ffmpeg = _require_binary("ffmpeg")
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-v",
                "error",
                "-t",
                "1",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-f",
                "s16le",
                "-",
            ],
            check=True,
            capture_output=True,
            timeout=20,
        )
        if not completed.stdout:
            raise MediaPreflightError(
                "media_audio_unreadable",
                "媒体中的音频无法读取，可能已损坏。请重新导出或更换文件后提交。",
            )
    except subprocess.SubprocessError as exc:
        raise MediaPreflightError(
            "media_audio_unreadable",
            "媒体中的音频无法读取，可能已损坏。请重新导出或更换文件后提交。",
        ) from exc


def preflight_media_file(source_path: str | Path) -> MediaPreflightResult:
    """Reject deterministic bad media before queueing or provider usage."""
    path = Path(source_path).expanduser().resolve()
    if not path.is_file():
        raise MediaPreflightError("media_file_missing", "上传文件未保存成功，请重新提交。")

    enabled_guards = _enabled_guards()
    if "empty_file" in enabled_guards and path.stat().st_size == 0:
        raise MediaPreflightError("media_file_empty", "媒体文件为空，请重新选择文件后提交。")

    needs_probe = bool({"container", "extension", "audio_stream", "audio_decode"} & set(enabled_guards))
    payload = _probe_media(path) if needs_probe else {}
    format_data = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    format_name = str(format_data.get("format_name") or "").strip() or None
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    audio_stream_count = sum(
        1 for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "audio"
    )

    if "container" in enabled_guards and not format_name:
        raise MediaPreflightError(
            "media_container_unreadable",
            "媒体文件无法读取，可能已损坏或文件内容与扩展名不匹配。",
        )
    if "extension" in enabled_guards and not _matches_file_extension(path, format_name):
        raise MediaPreflightError(
            "media_extension_mismatch",
            "媒体内容与文件扩展名不一致，请使用原始文件后重新提交。",
            metadata={"suffix": path.suffix.lower(), "format_name": format_name},
        )
    if "audio_stream" in enabled_guards and audio_stream_count == 0:
        raise MediaPreflightError(
            "media_audio_stream_missing",
            "媒体中没有可转录的音轨，请上传包含系统声音或麦克风声音的音视频文件。",
            metadata={"format_name": format_name},
        )
    if "audio_decode" in enabled_guards:
        _verify_first_audio_segment(path)

    return MediaPreflightResult(
        format_name=format_name,
        audio_stream_count=audio_stream_count if needs_probe else None,
        duration_seconds=_duration_seconds(payload),
        enabled_guards=enabled_guards,
    )
