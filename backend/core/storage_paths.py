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


def in_place_source_path(job: dict | None) -> Path | None:
    """Where a by-path task's recording actually lives.

    A task submitted by absolute path — the folder intake, the system file
    dialog, the Agent API — is read where it sits and never copied into
    FluentFlow's own store, so :func:`find_source_file` has nothing to find. Any
    caller that treats that as "the file is gone" tells the user their recording
    expired while it is sitting on their disk. The path was recorded when the
    task was queued; this reads it back and confirms it is still there.
    """
    metadata = (job or {}).get("metadata")
    origin = metadata.get("folder_intake") if isinstance(metadata, dict) else None
    if not isinstance(origin, dict):
        return None
    recorded = str(origin.get("original_path") or "").strip()
    if not recorded:
        return None
    path = Path(recorded).expanduser()
    return path if path.is_file() else None


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
