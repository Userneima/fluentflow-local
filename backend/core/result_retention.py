"""Result-storage finalization shared by local and hosted media pipelines."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from backend.core.retention_time import source_retention_expiry
from backend.core.storage_cleanup import cleanup_video_source_temp_files


def finalize_completed_result_storage(task_id: str, result: dict[str, Any], metadata: dict[str, Any] | None, *, source_retention_days: int, find_source_file: Callable[[str], Path | None]) -> dict[str, Any]:
    next_result = dict(result)
    artifacts = dict(next_result.get("artifacts") or {})
    if artifacts.get("playback_audio"):
        next_result["playback_audio_available"] = True
    cleanup_video_source_temp_files(metadata)
    if not find_source_file(task_id):
        next_result.update({"source_file_available": False, "source_retention_status": "not_found"})
        return next_result
    next_result.update({"source_file_available": True, "source_file_storage": "local", "source_retention_status": "retained"})
    if source_retention_days <= 0:
        # 0 (or less) means keep forever, as the maintenance doc promises: no
        # expiry is written, so the history pass never finds this source due.
        # It used to delete the source here, at once, including a link's only
        # downloaded copy.
        next_result.update({"source_retention_days": 0, "source_retention_expires_at": None})
        return next_result
    next_result.update({"source_retention_days": source_retention_days, "source_retention_expires_at": source_retention_expiry(source_retention_days)})
    return next_result
