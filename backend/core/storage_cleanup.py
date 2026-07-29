"""Task-file cleanup, extracted from server_helpers.py so both the hosted and
local editions can delete a task's on-disk files without the hosted helper
barrel. Depends only on the shared storage-path helpers plus stdlib; no
server_helpers imports."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core.storage_paths import (
    _artifact_storage_dir,
    _edited_transcript_dir,
    _source_storage_dir,
    _transcript_edit_records_dir,
)


def remove_tree(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return True
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def safe_filename_stem(value: str | None, fallback: str = "transcript") -> str:
    raw_stem = Path(value or fallback).stem or fallback
    safe_stem = "".join(
        ch if ch.isalnum() or ch in {" ", "-", "_", "."} else "_"
        for ch in raw_stem
    ).strip(" ._")
    return (safe_stem or fallback)[:96]


def cleanup_video_source_temp_files(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    removed: list[str] = []

    video_source = (metadata or {}).get("video_source")
    if isinstance(video_source, dict):
        for key in ("file_path", "metadata_path"):
            raw_path = str(video_source.get(key) or "").strip()
            if not raw_path:
                continue
            path = Path(raw_path).expanduser()
            if remove_tree(path):
                removed.append(str(path))
    return {
        "source_retention_status": "deleted" if removed else "not_found",
        "source_retention_removed_paths": removed,
        "source_retention_cleaned_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def cleanup_task_source_files(task_id: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    removed: list[str] = []
    source_dir = _source_storage_dir() / task_id
    if remove_tree(source_dir):
        removed.append(str(source_dir))
    removed.extend(cleanup_video_source_temp_files(metadata).get("source_retention_removed_paths") or [])
    return {
        "source_retention_status": "deleted" if removed else "not_found",
        "source_retention_removed_paths": removed,
        "source_retention_cleaned_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def cleanup_task_all_files(task_id: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    cleanup = cleanup_task_source_files(task_id, metadata)
    removed = list(cleanup.get("source_retention_removed_paths") or [])
    for path in (
        _artifact_storage_dir() / task_id,
    ):
        if remove_tree(path):
            removed.append(str(path))
    task_suffix = safe_filename_stem(task_id or "task")[:12]
    for folder, pattern in (
        (_edited_transcript_dir(), f"*__{task_suffix}_edited.txt"),
        (_transcript_edit_records_dir(), f"*__{task_suffix}_edit_records.json"),
    ):
        if folder.is_dir():
            for path in folder.glob(pattern):
                if remove_tree(path):
                    removed.append(str(path))
    cleanup["source_retention_removed_paths"] = removed
    cleanup["history_retention_status"] = "deleted" if removed else "not_found"
    return cleanup
