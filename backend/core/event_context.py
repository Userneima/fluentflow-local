"""Edition-neutral metadata attached to product events."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
import uuid
from functools import lru_cache
from typing import Any

from backend.core.schema_versions import EVENT_SCHEMA_VERSION
from backend.core.versioning import get_app_version


APP_VERSION = get_app_version()


def new_task_id() -> str:
    return uuid.uuid4().hex


def event_metadata(**values: Any) -> dict[str, Any]:
    return {
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "app_version": APP_VERSION,
        **{key: value for key, value in values.items() if value is not None},
    }


def _package_version(package_name: str) -> str | None:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None


@lru_cache(maxsize=1)
def _ffmpeg_version() -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        completed = subprocess.run(
            [ffmpeg, "-version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    first_line = (completed.stdout or completed.stderr or "").splitlines()
    return first_line[0][:160] if first_line else None


@lru_cache(maxsize=1)
def runtime_context_metadata() -> dict[str, Any]:
    return event_metadata(
        runtime_os=platform.system(),
        runtime_machine=platform.machine(),
        runtime_cpu_count=os.cpu_count(),
        python_version=(
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        faster_whisper_version=_package_version("faster-whisper"),
        ctranslate2_version=_package_version("ctranslate2"),
        ffmpeg_version=_ffmpeg_version(),
    )


def pipeline_mode(source_type: str | None) -> str | None:
    if source_type == "transcript_file":
        return "transcript_file"
    if source_type in {"audio", "video"}:
        return "audio_video"
    return None
