"""Shared title semantics for user-facing names.

Storage filenames may include source IDs for uniqueness. Display titles should
not expose those implementation prefixes to users.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

GENERATED_VIDEO_ID_PREFIX_RE = re.compile(r"^(?:\d{10,24}|BV[a-zA-Z0-9]{8,})[-_]+(?=.)")


# Only these count as an extension. Titles are usually a filename's stem already,
# and recordings are commonly named "5.投资人视角下的AI浪潮" or "1.2-1.3 批判…":
# treating whatever follows the last dot as a suffix cut 45 of this archive's
# titles down to "5" or "1.2-1" (found 2026-09-25).
KNOWN_SUFFIXES = frozenset({
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".flv", ".wmv", ".mpg", ".mpeg", ".ts",
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus", ".aiff", ".aif",
    ".srt", ".vtt", ".ass", ".txt", ".md", ".docx", ".pdf",
})


def strip_extension(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    return path.stem if path.suffix.lower() in KNOWN_SUFFIXES else text


def strip_generated_video_prefix(value: Any) -> str:
    text = strip_extension(value)
    return GENERATED_VIDEO_ID_PREFIX_RE.sub("", text).strip() or text


def display_title_for_user(value: Any, fallback: Any = "") -> str:
    title = strip_generated_video_prefix(value)
    if title:
        return title
    return strip_generated_video_prefix(fallback)
