"""Shared title semantics for user-facing names.

Storage filenames may include source IDs for uniqueness. Display titles should
not expose those implementation prefixes to users.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

GENERATED_VIDEO_ID_PREFIX_RE = re.compile(r"^(?:\d{10,24}|BV[a-zA-Z0-9]{8,})[-_]+(?=.)")


def strip_extension(value: Any) -> str:
    text = str(value or "").strip()
    return Path(text).stem if text else ""


def strip_generated_video_prefix(value: Any) -> str:
    text = strip_extension(value)
    return GENERATED_VIDEO_ID_PREFIX_RE.sub("", text).strip() or text


def display_title_for_user(value: Any, fallback: Any = "") -> str:
    title = strip_generated_video_prefix(value)
    if title:
        return title
    return strip_generated_video_prefix(fallback)
