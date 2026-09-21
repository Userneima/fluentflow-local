"""Shared title semantics for user-facing names.

Storage filenames may include source IDs for uniqueness. Display titles should
not expose those implementation prefixes to users.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

GENERATED_VIDEO_ID_PREFIX_RE = re.compile(r"^(?:\d{10,24}|BV[a-zA-Z0-9]{8,})[-_]+(?=.)")
DISPLAY_FILE_SUFFIXES = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v",
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus",
    ".srt", ".vtt", ".txt", ".md",
})


def strip_extension(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    suffix = Path(text).suffix.lower()
    # ``Path.stem`` considers every final dot-delimited part an extension.
    # That turns a legitimate title such as "4.5期 kickoff" into "4".
    # Only remove filename extensions FluentFlow actually supports.
    return text[:-len(suffix)] if suffix in DISPLAY_FILE_SUFFIXES else text


def strip_generated_video_prefix(value: Any) -> str:
    text = strip_extension(value)
    return GENERATED_VIDEO_ID_PREFIX_RE.sub("", text).strip() or text


def display_title_for_user(value: Any, fallback: Any = "") -> str:
    title = strip_generated_video_prefix(value)
    if title:
        return title
    return strip_generated_video_prefix(fallback)
