"""Stages of the media pipeline, lifted one at a time out of `_stream_media_job`.

That function ran to 1381 lines and announced its own seams: every stage marks
itself with a status update before it starts. Those markers are the cut lines.
A stage moves here once it can be expressed as "take the job context and what
earlier stages produced, do one thing, hand back what changed" — the streaming
of progress events stays with the caller, so nothing in here has to be an async
generator.

Nothing here imports `media_job`. The context is typed `Any` so the import only
ever goes one way.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event

logger = logging.getLogger(__name__)


def _text_len(value: str | None) -> int:
    return len(value or "")


async def export_note_to_lark(
    ctx: Any,
    *,
    result: dict[str, Any],
    duration_sec: float | None,
    transcript_text: str,
    summary_md: str,
) -> bool:
    """Push the finished note to Lark, and say whether it landed.

    Writes its outcome into `result` in place: `lark_doc_title` and
    `lark_response` on success, `lark_error` on failure. A failed export is
    deliberately not raised — the transcript and the note are already done and
    worth keeping, so the task completes and carries the export error with it.
    """
    exporter = ctx.auto_lark_exporter
    if not exporter:
        raise RuntimeError(
            "Media job context is missing an automatic Lark export policy"
        )

    export_target = "unknown"
    doc_title = ""
    # The same descriptive fields ride along on all three export events so the
    # rows can be read against each other and against the task's other stages.
    common: dict[str, Any] = {
        "task_id": ctx.task_id_value,
        "source_type": ctx.source_type,
        "source_filename": ctx.source_filename,
        "source_duration_seconds": (
            round(duration_sec, 1) if duration_sec is not None else None
        ),
        "source_file_size_mb": ctx.source_file_size_mb,
        "transcript_length": _text_len(transcript_text),
        "summary_length": _text_len(summary_md),
        "stage": "export",
    }

    log_event(
        event_name="lark_export_started",
        export_target=export_target,
        metadata=event_metadata(route="/process", trigger="auto", doc_title=doc_title),
        **common,
    )

    export_started_at = time.perf_counter()
    try:
        export = await ctx.loop.run_in_executor(
            None,
            lambda: exporter(
                task_id=ctx.task_id_value,
                summary_markdown=summary_md,
                filename_stem=ctx.display_title_value
                or Path(ctx.source_filename or "media").stem,
                form_title=ctx.title or ctx.display_title_value,
                lark_export_route=ctx.lark_export_route,
                lark_via_cli=ctx.lark_via_cli,
                lark_app_id=ctx.lark_app_id,
                lark_app_secret=ctx.lark_app_secret,
                folder_token=ctx.folder_token,
                account_user=ctx.account_user,
            ),
        )
    except Exception as exc:
        friendly = ctx.friendly_error(exc)
        result["lark_error"] = friendly
        log_event(
            event_name="lark_export_completed",
            duration_seconds=round(time.perf_counter() - export_started_at, 3),
            success=False,
            error_reason=friendly,
            export_target=export_target,
            metadata=event_metadata(
                route="/process",
                trigger="auto",
                doc_title=doc_title,
                raw_error=str(exc),
            ),
            **common,
        )
        return False

    doc_title = export["doc_title"]
    export_target = export["export_target"]
    response = export["response"]
    result["lark_doc_title"] = doc_title
    result["lark_response"] = response
    log_event(
        event_name="lark_export_completed",
        duration_seconds=round(time.perf_counter() - export_started_at, 3),
        success=True,
        export_target=export_target,
        feishu_doc_url=response.get("url") if isinstance(response, dict) else None,
        metadata=event_metadata(route="/process", trigger="auto", doc_title=doc_title),
        **common,
    )
    return True
