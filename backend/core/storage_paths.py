"""Storage-directory path helpers, extracted from server_helpers.py.
Thin wrappers over runtime_paths — re-imported by server_helpers so
H._artifact_storage_dir etc. keep working. No server_helpers imports."""

from __future__ import annotations

from pathlib import Path

from backend.core.runtime_paths import (
    default_artifact_dir,
    default_edited_transcript_dir,
    default_source_dir,
    default_transcript_edit_records_dir,
    default_video_source_dir,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _source_storage_dir() -> Path:
    return default_source_dir()


def _video_source_storage_dir() -> Path:
    return default_video_source_dir()


def _edited_transcript_dir() -> Path:
    return default_edited_transcript_dir()


def _artifact_storage_dir() -> Path:
    return default_artifact_dir()


def _transcript_edit_records_dir() -> Path:
    return default_transcript_edit_records_dir()


def find_source_file(task_id: str) -> Path | None:
    """Return the saved ``source.*`` file for a task, if present. Edition-neutral:
    depends only on the shared source-storage directory."""
    if not task_id:
        return None
    target_dir = _source_storage_dir() / task_id
    if not target_dir.is_dir():
        return None
    candidates = sorted(path for path in target_dir.glob("source.*") if path.is_file())
    return candidates[0] if candidates else None
