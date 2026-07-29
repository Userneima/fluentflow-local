"""Edition-neutral completed-job retention executor."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from backend.core.retention_time import parse_job_time
from backend.core.storage_cleanup import cleanup_task_all_files, cleanup_task_source_files

def enforce_history_retention(client_id: str | None, *, keep_count: int, artifact_days: int, source_days: int, list_jobs: Callable[..., list[dict]], update_result: Callable[..., Any], delete_jobs: Callable[..., Any]) -> dict[str, Any]:
    if not client_id: return {"pruned_count": 0, "task_ids": []}
    cutoff = datetime.now(timezone.utc) - timedelta(days=artifact_days) if artifact_days > 0 else None
    pruned, expired = [], 0
    now = datetime.now(timezone.utc)
    for index, job in enumerate(list_jobs(client_id=client_id)):
        task_id = str(job.get("task_id") or "")
        if not task_id or job.get("status") not in {"completed", "failed", "cancelled"}: continue
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        expires = parse_job_time(result.get("source_retention_expires_at"))
        if result.get("source_file_available") and expires and expires <= now:
            cleanup = cleanup_task_source_files(task_id, job.get("metadata"))
            update_result(task_id, {**result, "source_file_available": False, "source_file_storage": None, "source_retention_status": "expired", "source_retention_cleaned_at": cleanup.get("source_retention_cleaned_at")}, client_id=job.get("client_id"), touch_updated_at=False)
            expired += 1
        updated = parse_job_time(job.get("updated_at") or job.get("created_at"))
        if (keep_count > 0 and index >= keep_count) or (cutoff and updated and updated < cutoff):
            cleanup_task_all_files(task_id, job.get("metadata")); pruned.append(task_id)
    if pruned: delete_jobs(pruned, client_id=client_id)
    return {"pruned_count": len(pruned), "task_ids": pruned, "expired_source_count": expired}
