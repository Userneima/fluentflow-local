"""How a media job reports that it ended, whether it was cancelled or it failed.

Split out of `_stream_media_job`, which had grown to 1381 lines. These two paths
sit at the very end of that function and are the easiest part of it to lift:
they run once, they produce no pipeline output, and everything they touch is
either read off the job context or captured in `TerminalReport`.

Nothing here imports `media_job`. The context is typed as `Any` on purpose so
the import only ever goes one way and the pipeline cannot start depending on
its own ending.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from backend.core.event_context import (
    event_metadata,
    pipeline_mode,
    runtime_context_metadata,
)
from backend.core.event_logger import log_event
from backend.core.job_store import upsert_job
from backend.core.stt_process import terminate_process

logger = logging.getLogger(__name__)


def _text_len(value: str | None) -> int:
    return len(value or "")


def _log_task_completed(
    *,
    task_id: str,
    started_at: float,
    final_status: str,
    source_type: str | None = None,
    source_filename: str | None = None,
    source_duration_seconds: float | None = None,
    source_file_size_mb: float | None = None,
    transcript_length: int | None = None,
    summary_length: int | None = None,
    summary_status: str | None = None,
    lark_requested: bool | None = None,
    lark_success: bool | None = None,
    stt_provider: str | None = None,
    stt_provider_labeler: Any = None,
    completion_reason: str | None = None,
) -> None:
    total_duration = round(time.perf_counter() - started_at, 3)
    log_event(
        task_id=task_id,
        event_name="task_completed",
        source_type=source_type,
        source_filename=source_filename,
        source_duration_seconds=source_duration_seconds,
        source_file_size_mb=source_file_size_mb,
        transcript_length=transcript_length,
        summary_length=summary_length,
        stage="done" if final_status == "completed" else final_status,
        duration_seconds=total_duration,
        success=final_status == "completed",
        metadata=event_metadata(
            **runtime_context_metadata(),
            final_status=final_status,
            total_duration_seconds=total_duration,
            summary_status=summary_status,
            lark_requested=lark_requested,
            lark_success=lark_success,
            stt_provider=stt_provider,
            stt_provider_label=(
                stt_provider_labeler(stt_provider)
                if stt_provider and stt_provider_labeler
                else None
            ),
            source_type=source_type,
            pipeline_mode=pipeline_mode(source_type),
            completion_reason=completion_reason,
        ),
    )


def _release_task_usage(ctx: Any, *, reason: str, metadata: dict[str, Any]) -> None:
    if ctx.release_task_usage:
        ctx.release_task_usage(
            client_id=ctx.client_id,
            task_id=ctx.task_id_value,
            reason=reason,
            metadata=metadata,
        )


@dataclass
class TerminalReport:
    """The pipeline state an ended run has to report.

    Only the values that change while the job runs live here. Everything that is
    mirrored from the context once and never reassigned (source type, filename,
    size, task id, start time, STT provider) is read back off `ctx`, so the two
    cannot drift apart.
    """

    ctx: Any
    current_stage: str = "import"
    stt_process: Any = None
    duration_sec: float | None = None
    duration_estimate_sec: float | None = None
    transcript_text: str = ""
    summary_md: str = ""
    summary_status: str | None = None
    lark_success: bool | None = None
    cloud_stt_metadata: dict[str, Any] = field(default_factory=dict)


def _stop_stt_process(report: TerminalReport) -> None:
    if report.stt_process is not None and report.stt_process.is_alive():
        terminate_process(report.stt_process)


def report_cancelled(report: TerminalReport) -> None:
    """Record a run the client walked away from, and give its quota back."""
    ctx = report.ctx
    logger.info(
        "Processing stream cancelled by client at stage=%s", report.current_stage
    )
    _stop_stt_process(report)
    _release_task_usage(
        ctx,
        reason="Task cancelled before completion",
        metadata={"stage": report.current_stage},
    )
    source_duration = report.duration_sec or report.duration_estimate_sec
    _log_task_completed(
        task_id=ctx.task_id_value,
        started_at=ctx.task_started_at,
        final_status="cancelled",
        source_type=ctx.source_type,
        source_filename=ctx.source_filename,
        source_duration_seconds=(
            round(source_duration, 1) if source_duration is not None else None
        ),
        source_file_size_mb=ctx.source_file_size_mb,
        transcript_length=_text_len(report.transcript_text),
        summary_length=_text_len(report.summary_md),
        summary_status=report.summary_status,
        lark_requested=ctx.do_lark,
        lark_success=report.lark_success,
        stt_provider=ctx.stt_provider_value,
        stt_provider_labeler=ctx.stt_provider_labeler,
        completion_reason="client_disconnect",
    )
    upsert_job(
        task_id=ctx.task_id_value,
        status="cancelled",
        stage=report.current_stage,
        source_type=ctx.source_type,
        source_filename=ctx.source_filename,
        source_file_size_mb=ctx.source_file_size_mb,
        summary_status=report.summary_status,
        error_reason="client_disconnect",
    )


def report_failed(report: TerminalReport, exc: Exception) -> str:
    """Record a run that raised, give its quota back, and return what to show.

    Mutates `report.summary_status` when the job died inside the summary stage,
    because the caller has no other way to learn that the note never landed.
    """
    ctx = report.ctx
    logger.exception("Processing failed")
    friendly_error = ctx.friendly_error(exc)
    _stop_stt_process(report)
    if report.summary_status is None and report.current_stage == "summary":
        report.summary_status = "failed"
    _release_task_usage(
        ctx,
        reason="Task failed before charge finalization",
        metadata={"stage": report.current_stage, "raw_error": str(exc)},
    )
    log_event(
        task_id=ctx.task_id_value,
        event_name="task_failed",
        source_type=ctx.source_type,
        source_filename=ctx.source_filename,
        source_file_size_mb=ctx.source_file_size_mb,
        stage=report.current_stage,
        success=False,
        error_reason=friendly_error,
        metadata=event_metadata(
            route="/process",
            stt_provider=ctx.stt_provider_value,
            **report.cloud_stt_metadata,
            raw_error=str(exc),
        ),
    )
    _log_task_completed(
        task_id=ctx.task_id_value,
        started_at=ctx.task_started_at,
        final_status="failed",
        source_type=ctx.source_type,
        source_filename=ctx.source_filename,
        source_duration_seconds=(
            round(report.duration_sec, 1) if report.duration_sec is not None else None
        ),
        source_file_size_mb=ctx.source_file_size_mb,
        transcript_length=_text_len(report.transcript_text),
        summary_length=_text_len(report.summary_md),
        summary_status=report.summary_status,
        lark_requested=ctx.do_lark,
        lark_success=report.lark_success,
        stt_provider=ctx.stt_provider_value,
        stt_provider_labeler=ctx.stt_provider_labeler,
        completion_reason=report.current_stage,
    )
    upsert_job(
        task_id=ctx.task_id_value,
        status="failed",
        stage=report.current_stage,
        progress=0,
        source_type=ctx.source_type,
        source_filename=ctx.source_filename,
        source_file_size_mb=ctx.source_file_size_mb,
        summary_status=report.summary_status,
        error_reason=friendly_error,
        metadata={
            "stt_provider": ctx.stt_provider_value,
            **report.cloud_stt_metadata,
        },
    )
    return friendly_error
