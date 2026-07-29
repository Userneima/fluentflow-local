"""Shared client-event router assembled with an edition-specific scope."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from fastapi import APIRouter, Body, HTTPException, Request

from backend.core.event_context import (
    event_metadata,
    new_task_id,
    pipeline_mode,
    runtime_context_metadata,
)
from backend.core.event_logger import log_event
from backend.core.job_store import cancel_job_steps, get_job


CLIENT_EVENT_NAMES = {
    "summary_downloaded",
    "transcript_downloaded",
    "task_cancelled",
}


class JobEventState(Protocol):
    async def cancel(self, task_id: str) -> bool:
        """Cancel a live task when this process owns one."""


def create_client_events_router(
    *,
    request_client_scope: Callable[[Request], str],
    job_events: JobEventState,
) -> APIRouter:
    router = APIRouter()

    @router.post("/events")
    async def record_client_event(
        request: Request,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        """Record explicit client-side button events without user content."""
        event_name = str(payload.get("event_name") or "")
        if event_name not in CLIENT_EVENT_NAMES:
            raise HTTPException(status_code=400, detail=f"Unsupported event: {event_name}")
        task_id_value = str(payload.get("task_id") or "").strip() or new_task_id()
        client_id = request_client_scope(request)
        if event_name == "task_cancelled" and not get_job(
            task_id_value,
            client_id=client_id,
        ):
            raise HTTPException(status_code=404, detail="Job not found")
        raw_metadata = payload.get("metadata")
        allowed_client_metadata = (
            {
                key: raw_metadata.get(key)
                for key in ("format", "trigger")
                if key in raw_metadata
            }
            if isinstance(raw_metadata, dict)
            else {}
        )
        log_event(
            task_id=task_id_value,
            event_name=event_name,
            source_type=payload.get("source_type"),
            source_filename=payload.get("source_filename"),
            source_duration_seconds=payload.get("source_duration_seconds"),
            source_file_size_mb=payload.get("source_file_size_mb"),
            transcript_length=payload.get("transcript_length"),
            summary_length=payload.get("summary_length"),
            stage=payload.get("stage"),
            duration_seconds=payload.get("duration_seconds"),
            success=payload.get("success"),
            error_reason=payload.get("error_reason"),
            export_target=payload.get("export_target"),
            feishu_doc_url=payload.get("feishu_doc_url"),
            metadata=event_metadata(**allowed_client_metadata),
        )
        if event_name == "task_cancelled":
            await job_events.cancel(task_id_value)
            cancel_job_steps(task_id_value)
            log_event(
                task_id=task_id_value,
                event_name="task_completed",
                source_type=payload.get("source_type"),
                source_filename=payload.get("source_filename"),
                source_duration_seconds=payload.get("source_duration_seconds"),
                source_file_size_mb=payload.get("source_file_size_mb"),
                transcript_length=payload.get("transcript_length"),
                summary_length=payload.get("summary_length"),
                stage="cancelled",
                duration_seconds=payload.get("duration_seconds"),
                success=False,
                metadata=event_metadata(
                    **runtime_context_metadata(),
                    final_status="cancelled",
                    total_duration_seconds=payload.get("duration_seconds"),
                    summary_status=payload.get("summary_status"),
                    lark_requested=payload.get("lark_requested"),
                    lark_success=payload.get("lark_success"),
                    source_type=payload.get("source_type"),
                    pipeline_mode=pipeline_mode(payload.get("source_type")),
                    completion_reason="user_cancelled",
                ),
            )
        return {"ok": True, "task_id": task_id_value}

    return router
