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

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.speaker_diarization import (
    assign_speakers_to_segments,
    diarization_status,
    diarize_audio,
)

logger = logging.getLogger(__name__)


def _text_len(value: str | None) -> int:
    return len(value or "")


_DIARIZATION_EXECUTOR: ThreadPoolExecutor | None = None


def _diarization_executor() -> ThreadPoolExecutor:
    """A pool of its own for diarization, because a timed-out run leaks a thread.

    `asyncio.wait_for` stops waiting; it cannot stop the thread, which stays
    inside pyannote's model download for the life of the process. On the shared
    default executor those leaked threads accumulate until nothing else can run
    — every ffmpeg call, transcription and summarizer call in the pipeline goes
    through `run_in_executor` — which is the same silent hang the timeout exists
    to prevent, arrived at more slowly. One worker is deliberate: a second
    diarization queues behind a stuck one and then hits its own budget, which
    degrades to "no speaker labels" instead of blocking the pipeline.
    """
    global _DIARIZATION_EXECUTOR
    if _DIARIZATION_EXECUTOR is None:
        _DIARIZATION_EXECUTOR = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="diarization"
        )
    return _DIARIZATION_EXECUTOR


def _diarization_timeout_seconds(duration_seconds: float | None) -> float:
    """Budget for one diarization run: generous per audio minute, still bounded.

    Diarizing a long recording legitimately takes minutes on CPU, so the floor
    is high enough not to cut real work short. The cap is what keeps a stalled
    model download from holding a task forever.
    """
    audio_seconds = float(duration_seconds or 0.0)
    return min(2700.0, max(600.0, audio_seconds * 2.0))


async def label_speakers(
    ctx: Any,
    *,
    transcription: Any,
    segments_payload: list[dict[str, Any]],
    duration_sec: float | None,
    audio_path: Any,
    uses_remote_stt: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Attach speaker labels to the segments, and describe what happened.

    Returns the segments (relabelled, or unchanged) and a payload the result
    carries. Speaker labels are optional everywhere: every failure in here is
    recorded and swallowed, because a transcript without labels is still the
    thing the user asked for.

    A cloud engine returns its own labels with the transcript, so that branch
    only counts what came back. A local run has to diarize the audio itself,
    which is the expensive, hang-prone path the timeout exists for.
    """
    speaker_payload: dict[str, Any] = {
        "requested": ctx.diarization_requested,
        "available": True if uses_remote_stt else diarization_status()["available"],
        "applied": False,
    }
    rounded_duration = round(duration_sec, 1) if duration_sec is not None else None
    common: dict[str, Any] = {
        "task_id": ctx.task_id_value,
        "source_type": ctx.source_type,
        "source_filename": ctx.source_filename,
        "source_duration_seconds": rounded_duration,
        "source_file_size_mb": ctx.source_file_size_mb,
        "stage": "speaker_diarization",
    }
    engine_backend = getattr(transcription, "model_source", None) or "cloud_transcription"

    if ctx.diarization_requested and uses_remote_stt:
        speakers = sorted({
            str(segment.get("speaker"))
            for segment in segments_payload
            if isinstance(segment, dict) and segment.get("speaker")
        })
        if speakers:
            speaker_payload.update({
                "applied": True,
                "backend": engine_backend,
                "speaker_count": len(speakers),
            })
            log_event(
                event_name="speaker_diarization_completed",
                success=True,
                metadata=event_metadata(
                    route="/process",
                    backend=engine_backend,
                    speaker_count=len(speakers),
                ),
                **common,
            )
        else:
            error_reason = (
                getattr(transcription, "diarization_error", None)
                or f"{ctx.stt_provider_labeler(ctx.stt_provider_value)} did not return speaker labels"
            )
            speaker_payload.update({
                "applied": False,
                "backend": engine_backend,
                "error_reason": error_reason,
            })
            log_event(
                event_name="speaker_diarization_failed",
                success=False,
                error_reason=error_reason,
                metadata=event_metadata(route="/process", backend=engine_backend),
                **common,
            )
    elif ctx.diarization_requested:
        started_at = time.perf_counter()
        budget = _diarization_timeout_seconds(duration_sec)
        try:
            # Bounded on purpose. pyannote fetches its models on first use
            # and `from_pretrained` has no timeout of its own: on a slow
            # link it neither fails nor finishes, and a 2026-09-03 run sat
            # in it for over an hour with the task stuck at "stt" and no
            # error anywhere. Speaker labels are optional, so give them a
            # budget and move on without them when it is spent. The
            # executor thread is left to finish its download, which warms
            # the cache for the next run.
            turns = await asyncio.wait_for(
                ctx.loop.run_in_executor(
                    _diarization_executor(), lambda: diarize_audio(audio_path)
                ),
                timeout=budget,
            )
            segments_payload = assign_speakers_to_segments(segments_payload, turns)
            speaker_payload.update({
                "applied": True,
                "speaker_count": len({turn.speaker for turn in turns}),
                "turn_count": len(turns),
            })
            log_event(
                event_name="speaker_diarization_completed",
                duration_seconds=round(time.perf_counter() - started_at, 3),
                success=True,
                metadata=event_metadata(
                    route="/process", speaker_count=speaker_payload["speaker_count"]
                ),
                **common,
            )
        except asyncio.TimeoutError:
            error_reason = (
                f"说话人区分超过 {int(budget)} 秒预算，已跳过。"
                "首次使用需要下载 pyannote 模型；下载完成后重试即可。"
            )
            speaker_payload.update({"applied": False, "error_reason": error_reason})
            logger.warning(
                "Speaker diarization timed out for %s: %s", ctx.task_id_value, error_reason
            )
            log_event(
                event_name="speaker_diarization_failed",
                duration_seconds=round(time.perf_counter() - started_at, 3),
                success=False,
                error_reason=error_reason,
                metadata=event_metadata(
                    route="/process",
                    failure_scope="optional_speaker_diarization",
                    timed_out=True,
                ),
                **common,
            )
        except Exception as exc:
            error_reason = str(exc)
            speaker_payload.update({"applied": False, "error_reason": error_reason})
            logger.warning(
                "Speaker diarization skipped for %s: %s", ctx.task_id_value, error_reason
            )
            log_event(
                event_name="speaker_diarization_failed",
                duration_seconds=round(time.perf_counter() - started_at, 3),
                success=False,
                error_reason=error_reason,
                metadata=event_metadata(
                    route="/process", failure_scope="optional_speaker_diarization"
                ),
                **common,
            )

    # Recorded here rather than in the summary stage: the local edition
    # switches that stage off and writes the note from the cut media
    # afterwards, so a flag set inside it is missing on exactly the flow
    # that runs it. Two or more speakers is what makes any note built from
    # these segments carry labels.
    speaker_payload["segments_labeled"] = len({
        str(segment.get("speaker"))
        for segment in segments_payload
        if isinstance(segment, dict) and segment.get("speaker")
    }) >= 2
    return segments_payload, speaker_payload


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
