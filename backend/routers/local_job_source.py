"""Local-only recovery route for a browser-selected source media file."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from backend.core.job_store import get_job, upsert_job
from backend.core.local_request_scope import request_client_id
from backend.core.local_retention_config import source_retention_days
from backend.core.local_limits_config import max_upload_mb
from backend.core.media_intake import (
    ALLOWED_SUFFIXES,
    MediaIntakeSizeError,
    file_size_mb,
    persist_source_stream,
    source_type_for_suffix,
)
from backend.core.retention_time import source_retention_expiry
from backend.core.storage_paths import find_source_file
from backend.core.title_display import display_title_for_user

router = APIRouter()


def _client_scope(request: Request) -> str:
    return request_client_id(request) or "anonymous"


def _remove_replaced_sources(task_id: str, retained: Path) -> None:
    source_dir = retained.parent
    for candidate in source_dir.glob("source.*"):
        if candidate != retained and candidate.is_file():
            candidate.unlink(missing_ok=True)


@router.post("/jobs/{task_id}/source")
async def replace_job_source(
    request: Request,
    task_id: str,
    file: UploadFile = File(...),
) -> dict:
    """Persist an explicitly re-selected local source for future review.

    Browsers cannot reopen a historical local file without a fresh user choice.
    This local-only route stores that chosen source under the existing task and
    leaves its transcript and note untouched.
    """
    client_id = _client_scope(request)
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if not filename or suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="Choose a supported audio or video file")

    max_bytes = int(max_upload_mb() * 1024 * 1024)
    try:
        await file.seek(0)
        stored_path, byte_count = await asyncio.to_thread(
            persist_source_stream,
            task_id,
            suffix,
            file.file,
            max_bytes=max_bytes,
        )
    except MediaIntakeSizeError as exc:
        raise HTTPException(status_code=413, detail=f"Source file exceeds the {max_upload_mb():g} MB limit") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Could not save the selected source file") from exc
    finally:
        await file.close()

    await asyncio.to_thread(_remove_replaced_sources, task_id, stored_path)
    result = dict(job.get("result") or {})
    raw_title = Path(filename).stem
    result.update({
        "task_id": result.get("task_id") or task_id,
        "filename": filename,
        "raw_title": raw_title,
        "display_title": display_title_for_user(raw_title, filename),
        "source_file_available": bool(find_source_file(task_id)),
        "source_file_storage": "local",
        "source_retention_status": "retained",
        "source_retention_days": source_retention_days(),
        "source_retention_expires_at": source_retention_expiry(source_retention_days()),
        "source_relinked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    })
    upsert_job(
        task_id=task_id,
        status=job.get("status") or "completed",
        client_id=job.get("client_id") or client_id,
        stage=job.get("stage") or "done",
        progress=job.get("progress"),
        source_type=source_type_for_suffix(suffix),
        source_filename=filename,
        source_file_size_mb=file_size_mb(byte_count),
        summary_status=job.get("summary_status"),
        error_reason=job.get("error_reason"),
        result=result,
        metadata=job.get("metadata"),
    )
    return {"ok": True, "task_id": task_id, "result": result}
