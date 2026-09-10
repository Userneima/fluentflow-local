"""Media job pipeline extracted from routers/processing.py.

Holds MediaJobContext, the _stream_media_job pipeline generator, execute_media_job,
and the transcript-correction / source-language helpers. Re-imported by processing.py
(facade) so route handlers and the server_helpers queue worker keep working unchanged.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Optional
import functools
import json
import os
import uuid
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import asyncio
import shutil
import tempfile
import time
import logging

from fastapi import Request

from backend.core.audio_handler import (
    extract_compressed_mp3,
    extract_stt_wav,
    require_audible_audio,
)
from backend.core.ai_summarizer import (
    generate_bilingual_segments_zh,
    plan_visual_evidence_requests,
    select_visual_evidence_frames,
    summarize_transcript_with_metadata,
    visual_requests_to_frame_segments,
)
from backend.core.media_probe import media_duration_seconds
from backend.core.media_job_stages import export_note_to_lark, label_speakers
from backend.core.media_job_outcome import (
    TerminalReport,
    _log_task_completed,
    _text_len,
    report_cancelled,
    report_failed,
)
from backend.core.media_preflight import SILENCE_GUARD_ENV, media_guard_enabled
from backend.core.media_intake import path_size_mb
from backend.core.chapter_coverage import bind_chapter_coverage_time_ranges
from backend.core.event_context import (
    event_metadata,
    runtime_context_metadata,
)
from backend.core.event_logger import log_event
from backend.core.job_event_hub import JobEventHub, _sse, event_from_sse_chunk
from backend.core.job_lifecycle import (
    result_for_summary_failure,
    result_for_summary_success,
    result_for_transcript_only,
)
from backend.core.job_store import get_job, upsert_job
from backend.core.result_artifacts import (
    DEBREATH_MEDIA_KIND,
    TRANSCRIPT_MEDIA_CUT,
    TRANSCRIPT_MEDIA_SOURCE,
    _attach_enhanced_playback_audio_artifact,
    _attach_playback_audio_artifact,
    _attach_result_artifacts,
    _write_file_artifact,
)
from backend.core.speaker_diarization import build_speaker_annotated_transcript
from backend.core.voice_enhance import enhance_voice, measure_presence, stt_audio_source
from backend.core.stt_process import drain_queue, start_transcription_process, terminate_process
from backend.core.storage_paths import _artifact_storage_dir
from backend.core.transcript_cleaner import clean_repeated_transcript
from backend.core.transcript_correction import (
    correction_result_fields,
    correct_transcript_segments,
    transcript_correction_enabled,
)
from backend.core.visual_evidence import (
    build_visual_evidence_from_note_images,
    build_visual_key_moments,
    inject_visual_evidence_references,
    rewrite_note_image_references,
)


logger = logging.getLogger(__name__)


def _stt_realtime_factor(
    stt_elapsed_seconds: float | None,
    duration_seconds: float | None,
) -> float | None:
    if not stt_elapsed_seconds or not duration_seconds or duration_seconds <= 0:
        return None
    return max(round(stt_elapsed_seconds / duration_seconds, 4), 0.0001)


def _summary_result_metadata(summary_result: Any) -> dict[str, Any]:
    return {
        "resolved_note_mode": getattr(summary_result, "resolved_mode", None),
        "note_mode_chunk_count": getattr(summary_result, "chunk_count", None),
        "note_mode_transcript_length": getattr(summary_result, "transcript_length", None),
        "coverage_checked": getattr(summary_result, "coverage_checked", None),
        "coverage_revision_used": getattr(summary_result, "coverage_revision_used", None),
        "note_mode_segment_count": getattr(summary_result, "segment_count", None),
        "note_mode_evidence_count": getattr(summary_result, "evidence_count", None),
        "note_mode_chapter_count": getattr(summary_result, "chapter_count", None),
        "note_mode_important_evidence_count": getattr(summary_result, "important_evidence_count", None),
        "note_mode_covered_important_evidence_count": getattr(summary_result, "covered_important_evidence_count", None),
        "note_mode_coverage_missing_count": getattr(summary_result, "coverage_missing_count", None),
    }


def _cleanup_payload(cleanup_result: Any) -> dict[str, Any]:
    return {
        "applied_count": cleanup_result.applied_count,
        "removed_segment_count": cleanup_result.removed_segment_count,
        "raw_length": cleanup_result.raw_length,
        "cleaned_length": cleanup_result.cleaned_length,
        "issues": [asdict(item) for item in cleanup_result.issues[:20]],
        "issue_count": len(cleanup_result.issues),
    }


# Probed through media_probe. Kept as a module-level name here because tests
# monkeypatch it on this module.
_media_duration_seconds = media_duration_seconds


def _duration_limit_error(
    duration_seconds: float,
    filename: str | None,
    limit_seconds: float | None,
) -> str | None:
    if not limit_seconds or limit_seconds <= 0 or duration_seconds <= limit_seconds:
        return None
    name = f"「{filename}」" if filename else "当前媒体"
    return f"{name}时长过长：约 {duration_seconds / 60:.1f} 分钟，当前限制为 {limit_seconds / 60:.1f} 分钟。"


def stt_engine_refusal(
    provider: str,
    *,
    has_remote_policy: bool,
    provider_label: str,
    local_allowed: bool = True,
) -> str | None:
    """Why this job must stop rather than transcribe locally, or None to proceed.

    Local faster-whisper is not a silent stand-in for the engine the submitter
    chose. On 2026-08-06 a 3-hour recording reached the local engine on the
    2 GiB hosted box because the queue had lost the engine name; memory ran out,
    the machine stopped answering, and two hours later the job failed with a
    message about video downloads. Refusing costs one clear error; substituting
    cost the whole site.
    """

    if has_remote_policy:
        return None
    if provider != "local":
        return (
            f"云端转写引擎 {provider_label} 当前不可用：未找到可用的 API Key。"
            "任务已停止，不会退回本地转写。"
        )
    if not local_allowed:
        return "本地转写在公开服务上不可用：请选择云端转写引擎后重试。"
    return None


def _stale_job_seconds() -> float:
    try:
        return max(float(os.environ.get("FLUENTFLOW_STALE_JOB_SECONDS", "7200")), 60.0)
    except ValueError:
        return 7200.0


def _normalized_source_language(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    if not text:
        return None
    if text.startswith("en") or text in {"english"}:
        return "en"
    if text.startswith("zh") or text in {"chinese", "mandarin"}:
        return "zh"
    return text.split("-", 1)[0]


def _is_english_source(value: str | None) -> bool:
    return _normalized_source_language(value) == "en"


def _translation_ai_kwargs(ai_kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in ai_kwargs.items()
        if key in {"api_key", "model", "provider"}
    }


async def _run_transcript_correction_stage(
    *,
    loop: asyncio.AbstractEventLoop,
    task_id: str,
    route: str,
    source_type: str,
    source_filename: str | None,
    source_duration_seconds: float | None,
    source_file_size_mb: float | None,
    transcript_text: str,
    segments: list[dict[str, Any]],
    deepseek_api_key: str | None,
    secret_resolver: Any = None,
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    """Run optional conservative transcript correction without failing the task."""
    if not transcript_correction_enabled():
        return {"note_generation_transcript_source": "transcript_text"}, transcript_text, segments
    if secret_resolver is None:
        raise RuntimeError("Transcript correction requires a secret resolver")

    started_at = time.perf_counter()
    correction_result = await loop.run_in_executor(
        None,
        lambda: correct_transcript_segments(
            segments,
            api_key=secret_resolver(deepseek_api_key, "deepseek_api_key"),
            provider="deepseek",
        ),
    )
    note_transcript_text = correction_result.corrected_text or transcript_text
    note_segments = correction_result.corrected_segments or segments
    fields = correction_result_fields(
        correction_result,
        note_input_applied=bool(correction_result.corrected_text),
    )
    fields["note_generation_transcript_source"] = (
        "corrected_transcript" if correction_result.corrected_text else "transcript_text"
    )
    log_event(
        task_id=task_id,
        event_name="transcript_correction_completed" if correction_result.status in {"completed", "no_changes"} else "transcript_correction_unavailable",
        source_type=source_type,
        source_filename=source_filename,
        source_duration_seconds=source_duration_seconds,
        source_file_size_mb=source_file_size_mb,
        transcript_length=_text_len(transcript_text),
        stage="transcript_correction",
        duration_seconds=round(time.perf_counter() - started_at, 3),
        success=correction_result.status in {"completed", "no_changes"},
        error_reason=correction_result.error,
        metadata=event_metadata(
            route=route,
            correction_status=correction_result.status,
            correction_applied_count=correction_result.applied_count,
            correction_rejected_count=correction_result.rejected_count,
            correction_provider=correction_result.provider,
            correction_model=correction_result.model,
            note_generation_transcript_source=fields["note_generation_transcript_source"],
        ),
    )
    return fields, note_transcript_text, note_segments


@dataclass
class MediaJobContext:
    """All state the media processing pipeline needs, decoupled from FastAPI Request."""

    task_id_value: str
    client_id: Any
    source_type: str
    source_filename: str
    raw_title_value: str
    display_title_value: str
    suffix: str
    td: str
    in_path: Any
    content: bytes
    source_fingerprint: Any
    source_file_size_mb: float | None
    max_upload_mb: Any
    duration_preflight_sec: float | None
    quota_estimate: Any
    quota_reservation: Any
    task_started_at: float
    loop: Any
    model_size: str
    speed_profile: str
    language: str
    stt_provider_value: str
    diarization_requested: bool
    do_lark: bool
    summary_disabled: bool
    generate_visuals: bool
    source_last_modified_ms: Any
    export_to_lark: Any
    lark_export_route: Any
    lark_via_cli: Any
    folder_token: Any
    deepseek_api_key: Any
    openai_api_key: Any
    qwen_api_key: Any
    ai_provider: Any
    ai_model: Any
    note_mode: Any
    skip_summary: Any
    system_prompt: Any
    prompt_preset: Any
    prompt_preset_label: Any
    account_user: dict[str, Any] | None
    title: Any
    lark_app_id: Any
    lark_app_secret: Any
    duration_limit_seconds: float | None
    # Whether to correct the muffled far-field profile: measure the consonant
    # band, write a clearer take, and transcribe from it. Off unless asked,
    # because most material does not need it and the correction damages an
    # already-mixed soundtrack. Off also means the recognizer reads the plain
    # audio, so an upload nobody asked about behaves exactly as it did before.
    voice_enhance_requested: bool = False
    # Edition-owned AI credential policy. Hosted callers leave this unset and
    # retain the existing server helper behavior; local callers inject their
    # strict provider-to-key matcher.
    ai_kwargs_builder: Any = None
    # Event hub the worker publishes to. Defaults to the hosted hub via
    # execute_media_job when unset; a local composition root can inject the
    # local hub so live progress and cancellation share one hub.
    job_events: Any = None
    # Whether the terminal job state is propagated to the hosted desktop-sync
    # service. Hosted default is True; the local edition must pass False so a
    # local worker never triggers cloud side effects.
    sync_terminal_result: bool = True
    # Hosted composition owns the actual sync action. Keeping it out of the
    # worker makes the local pipeline independent of the desktop-sync module.
    terminal_result_sync: Any = None
    # Account quota is a hosted lifecycle concern. Local contexts leave both
    # hooks empty so terminal processing cannot touch account quota state.
    finalize_task_usage: Any = None
    release_task_usage: Any = None
    finalize_result_storage: Any = None
    auto_lark_exporter: Any = None
    enforce_history_retention: Any = None
    secret_resolver: Any = None
    keyframe_extractor: Any = None
    remote_stt_policy: Any = None
    friendly_error: Any = None
    stt_provider_labeler: Any = None
    # Whether this edition may transcribe on the machine running the job. The
    # composition root decides; the pipeline only obeys. Defaults to True so the
    # local edition keeps working without opting in.
    local_stt_allowed: bool = True
    # Optional: give the pipeline a different file to work from than the one that
    # was uploaded. Called once, before audio extraction, with (task_id, path);
    # returns an object carrying `path`, `state` and `artifacts`, or None to leave
    # the recording alone. The local edition injects breath-gap removal here so
    # the transcript is made from the shortened audio and therefore carries that
    # file's timestamps from the start — nothing downstream has to remap anything.
    # No composition root that passes nothing behaves differently in any way.
    media_preprocessor: Any = None


def _finalize_task_usage(
    ctx: MediaJobContext,
    *,
    duration_seconds: float | None,
    transcript_text: str,
    summary_text: str,
    skip_summary: bool,
    reason: str | None = None,
) -> dict[str, Any] | None:
    if not ctx.finalize_task_usage:
        return None
    return ctx.finalize_task_usage(
        client_id=ctx.client_id,
        task_id=ctx.task_id_value,
        duration_seconds=duration_seconds,
        transcript_text=transcript_text,
        summary_text=summary_text,
        skip_summary=skip_summary,
        reason=reason,
        stt_provider=ctx.stt_provider_value,
    )


def _finalize_result_storage(ctx: MediaJobContext, result: dict[str, Any]) -> dict[str, Any]:
    if not ctx.finalize_result_storage:
        return result
    job = get_job(ctx.task_id_value)
    metadata = job.get("metadata") if isinstance(job, dict) else None
    return ctx.finalize_result_storage(ctx.task_id_value, result, metadata)

def _raw_segment_payload(segment: Any) -> dict[str, Any]:
    """Persist everything the engine gave us for this segment.

    Word timings used to be dropped here even when the engine returned them, so
    a stored transcript could only ever answer "which sentence", never "which
    word". Getting them back costs another transcription — real money — so
    anything the provider paid to compute is written down.
    """
    payload: dict[str, Any] = {
        "start": segment.start,
        "end": segment.end,
        "text": segment.text,
        "speaker": getattr(segment, "speaker", None),
    }
    words = getattr(segment, "words", None)
    if words:
        payload["words"] = [dict(word) for word in words]
    return payload


def _enforce_history_retention(ctx: MediaJobContext) -> None:
    if ctx.enforce_history_retention: ctx.enforce_history_retention(ctx.client_id)


async def _stream_media_job(ctx: MediaJobContext) -> AsyncGenerator[str, None]:
    """Run the full media pipeline for one job, yielding SSE chunks. No FastAPI Request."""
    task_id_value = ctx.task_id_value
    client_id = ctx.client_id
    source_type = ctx.source_type
    source_filename = ctx.source_filename
    raw_title_value = ctx.raw_title_value
    display_title_value = ctx.display_title_value
    suffix = ctx.suffix
    td = ctx.td
    in_path = ctx.in_path
    content = ctx.content
    source_fingerprint = ctx.source_fingerprint
    source_file_size_mb = ctx.source_file_size_mb
    max_upload_mb = ctx.max_upload_mb
    duration_preflight_sec = ctx.duration_preflight_sec
    quota_estimate = ctx.quota_estimate
    quota_reservation = ctx.quota_reservation
    task_started_at = ctx.task_started_at
    loop = ctx.loop
    model_size = ctx.model_size
    speed_profile = ctx.speed_profile
    language = ctx.language
    stt_provider_value = ctx.stt_provider_value
    diarization_requested = ctx.diarization_requested
    voice_enhance_requested = bool(ctx.voice_enhance_requested)
    do_lark = ctx.do_lark
    summary_disabled = ctx.summary_disabled
    generate_visuals = ctx.generate_visuals
    source_last_modified_ms = ctx.source_last_modified_ms
    export_to_lark = ctx.export_to_lark
    lark_export_route = ctx.lark_export_route
    lark_via_cli = ctx.lark_via_cli
    folder_token = ctx.folder_token
    deepseek_api_key = ctx.deepseek_api_key
    openai_api_key = ctx.openai_api_key
    qwen_api_key = ctx.qwen_api_key
    ai_provider = ctx.ai_provider
    ai_model = ctx.ai_model
    note_mode = ctx.note_mode
    skip_summary = ctx.skip_summary
    system_prompt = ctx.system_prompt
    prompt_preset = ctx.prompt_preset
    prompt_preset_label = ctx.prompt_preset_label
    account_user = ctx.account_user
    title = ctx.title
    lark_app_id = ctx.lark_app_id
    lark_app_secret = ctx.lark_app_secret
    duration_limit_seconds = ctx.duration_limit_seconds
    secret_resolver = ctx.secret_resolver
    ai_kwargs_builder = ctx.ai_kwargs_builder
    keyframe_extractor = ctx.keyframe_extractor
    remote_stt_policy = ctx.remote_stt_policy
    friendly_error_message = ctx.friendly_error
    stt_provider_label = ctx.stt_provider_labeler
    media_preprocessor = ctx.media_preprocessor

    current_stage = "import"
    duration_sec: float | None = None
    duration_estimate_sec: float | None = None
    transcript_text = ""
    summary_md = ""
    summary_status: str | None = None
    lark_success: bool | None = None
    stt_process = None
    stt_queue = None
    playback_audio_path: Path | None = None
    enhanced_playback_audio_path: Path | None = None
    voice_presence: dict[str, float] | None = None
    stt_audio_take = "plain"
    cloud_stt_metadata: dict[str, Any] = {}
    # Filled by the optional preprocessor below, merged into the result once
    # there is one. Held here rather than written straight to the job row because
    # the result does not exist yet at that point in the pipeline.
    preprocess_state: dict[str, Any] = {}
    preprocess_artifacts: dict[str, dict[str, Any]] = {}
    try:
        if secret_resolver is None:
            raise RuntimeError("Media job context is missing a secret resolver")
        if ai_kwargs_builder is None:
            raise RuntimeError("Media job context is missing an AI credential policy")
        if friendly_error_message is None:
            raise RuntimeError("Media job context is missing an error presentation policy")
        if stt_provider_label is None:
            raise RuntimeError("Media job context is missing an STT provider label policy")
        uses_remote_stt = remote_stt_policy is not None
        refusal = stt_engine_refusal(
            stt_provider_value,
            has_remote_policy=uses_remote_stt,
            provider_label=stt_provider_label(stt_provider_value),
            local_allowed=ctx.local_stt_allowed,
        )
        if refusal:
            raise RuntimeError(refusal)

        # ── Stage 0: Prepare the media the rest of the job reads ───
        # Optional and injected. The local edition removes the breath gaps here,
        # so everything after this line — the audio, the transcript and its
        # timestamps, the note, the page's player — belongs to one file. It runs
        # before the duration limit is measured on purpose: the shortened file is
        # what the user is going to work with, so it is what should be measured.
        if media_preprocessor is not None:
            current_stage = "prepare_media"
            upsert_job(task_id=task_id_value, status="running", stage="prepare_media", progress=2)
            yield _sse({"stage": "prepare_media", "progress": 2})
            prepared = await loop.run_in_executor(
                None, lambda: media_preprocessor(task_id_value, in_path)
            )
            if prepared is not None:
                preprocess_state = dict(getattr(prepared, "state", None) or {})
                preprocess_artifacts = dict(getattr(prepared, "artifacts", None) or {})
                prepared_path = getattr(prepared, "path", None)
                if prepared_path:
                    in_path = Path(prepared_path)

        # ── Stage 1: Audio extraction ──────────────────────
        current_stage = "audio"
        upsert_job(task_id=task_id_value, status="running", stage="audio", progress=5)
        yield _sse({"stage": "audio", "progress": 5})
        audio_started_at = time.perf_counter()

        # Only when asked. Most material does not need correcting, the
        # correction damages an already-mixed soundtrack, and no threshold
        # separates those cases (see `voice_enhance`) — so an upload nobody said
        # anything about is left alone entirely, including what the recognizer
        # reads. Correcting it also changes what silence looks like: `loudnorm`
        # lifts the gaps between words, which is why de-breathing measures its
        # own threshold per file instead of trusting a constant.
        #
        # When asked: measure the consonant band, write a clearer take, and
        # transcribe from that. Measured on four speakers with two whisper sizes,
        # it transcribes at least as well and better on consonant-dense words —
        # "IG ID / EVN ID" came back as "Agent ID / Event ID".
        if voice_enhance_requested:
            try:
                reading = await loop.run_in_executor(None, lambda: measure_presence(in_path))
                voice_presence = reading.as_dict()
                enhanced_playback_audio_path = await loop.run_in_executor(
                    None,
                    lambda: enhance_voice(in_path, output_path=Path(td) / "playback_enhanced.m4a"),
                )
            except Exception as exc:  # noqa: BLE001 - clarity is optional, the transcript is not
                logger.warning("Voice enhancement skipped for %s: %s", task_id_value, exc)
                enhanced_playback_audio_path = None

        # Derived from the corrected take that was just written, not by filtering
        # the original a second time. The two are not interchangeable, and the
        # measurement that justified feeding the recognizer corrected audio was
        # made on this one. Filtering the original straight to a recognizer input
        # skips a lossy generation and should be the better of the two, but when
        # compared it dropped a company name the m4a-derived take recovered
        # ("MyFitnessPal" survived as "MetfitnessPal", against nothing at all),
        # so the measured path wins over the one that ought to be better.
        stt_source, stt_audio_take = stt_audio_source(enhanced_playback_audio_path, in_path)
        if uses_remote_stt:
            audio_output_format = "mp3"
            out_audio = await loop.run_in_executor(
                None, lambda: extract_compressed_mp3(stt_source, output_path=Path(td) / "cloud_stt.mp3")
            )
        else:
            audio_output_format = "wav"
            out_audio = await loop.run_in_executor(
                None, lambda: extract_stt_wav(stt_source, output_path=Path(td) / "stt.wav")
            )
        # The plain take is always kept: it is what the editor plays by default
        # and what a re-transcription reads, so a re-run reproduces this run.
        playback_audio_path = await loop.run_in_executor(
            None, lambda: extract_compressed_mp3(in_path, output_path=Path(td) / "playback.mp3")
        )
        if media_guard_enabled(SILENCE_GUARD_ENV):
            # Checked on the plain take on purpose. `loudnorm` lifts a near-silent
            # recording's noise floor into audible range, so the corrected take
            # would report sound where the recording has none.
            await loop.run_in_executor(None, lambda: require_audible_audio(playback_audio_path))

        audio_elapsed_sec = time.perf_counter() - audio_started_at
        log_event(
            task_id=task_id_value,
            event_name="audio_extracted",
            source_type=source_type,
            source_filename=source_filename,
            source_file_size_mb=source_file_size_mb,
            stage="audio",
            duration_seconds=round(audio_elapsed_sec, 3),
            success=True,
            metadata=event_metadata(
                route="/process",
                stt_provider=stt_provider_value,
                stt_provider_label=stt_provider_label(stt_provider_value),
                audio_output_format=audio_output_format,
                audio_output_size_mb=path_size_mb(out_audio),
                stt_audio_take=stt_audio_take,
                presence_deficit_db=(voice_presence or {}).get("presence_deficit_db"),
            ),
        )
        upsert_job(task_id=task_id_value, status="running", stage="audio", progress=20)
        yield _sse({"stage": "audio", "progress": 20})

        # ── Stage 1.5: Visual evidence placeholders ──
        # Actual frame extraction happens after the text note exists, so the
        # text model can request only the time windows where screenshots help.
        frame_paths: list[str] = []
        frame_metadata: list[dict[str, Any]] = []
        visual_requests: list[dict[str, Any]] = []
        visual_selections: list[dict[str, Any]] = []
        visual_evidence_error: str | None = None

        # ── Stage 2: STT transcription ─────────────────────
        current_stage = "stt"
        upsert_job(task_id=task_id_value, status="running", stage="stt", progress=22)
        yield _sse({"stage": "stt", "progress": 22, "stt_progress": 0, "stt_status": "starting"})

        duration_estimate_sec = _media_duration_seconds(out_audio)
        if duration_estimate_sec:
            duration_error = _duration_limit_error(duration_estimate_sec, source_filename, duration_limit_seconds)
            if duration_error:
                raise RuntimeError(duration_error)
        status_progress_floor = {
            "starting": 22.0,
            "loading_model": 23.0,
            "chunking_audio": 24.0,
            "preparing_audio": 24.0,
            "waiting_first_segment": 25.0,
            "transcribing_chunks": 25.0,
            "transcribing_segments": 25.0,
        }
        progress_state: dict[str, Any] = {
            "latest": 22.0,
            "stt_progress": 0.0,
            "transcribed_seconds": 0.0,
            "duration_seconds": duration_estimate_sec,
            "stt_status": "starting",
        }

        stt_started_at = time.perf_counter()
        stt_timeout = _stale_job_seconds()
        if uses_remote_stt:
            cloud_stt_metadata = remote_stt_policy.initial_metadata(out_audio, duration_estimate_sec)

            def on_remote_stt_progress(status: str, metadata: dict[str, Any] | None = None) -> None:
                progress_state["stt_status"] = status
                if metadata:
                    cloud_stt_metadata.update(metadata)

            progress_state["stt_status"] = remote_stt_policy.initial_status
            remote_stt_task = loop.run_in_executor(
                None,
                lambda: remote_stt_policy.transcribe(
                    out_audio,
                    language=language,
                    diarization_enabled=diarization_requested,
                    timeout=stt_timeout,
                    progress_callback=on_remote_stt_progress,
                ),
            )
            last_emit_at = time.perf_counter()
            while not remote_stt_task.done():
                await asyncio.sleep(1)
                now = time.perf_counter()
                if now - stt_started_at > stt_timeout:
                    remote_stt_task.cancel()
                    try:
                        await remote_stt_task
                    except Exception:
                        pass
                    raise RuntimeError("STT processing timed out")
                if now - last_emit_at < 2:
                    continue
                last_emit_at = now
                upsert_job(
                    task_id=task_id_value,
                    status="running",
                    stage="stt",
                    progress=25,
                    metadata={
                        "stt_provider": stt_provider_value,
                        "stt_provider_label": stt_provider_label(stt_provider_value),
                        **cloud_stt_metadata,
                        "duration_seconds": round(duration_estimate_sec, 1) if duration_estimate_sec else None,
                        "stt_elapsed_seconds": round(now - stt_started_at, 1),
                        "stt_status": progress_state.get("stt_status"),
                    },
                )
                yield _sse({
                    "stage": "stt",
                    "progress": 25,
                    **cloud_stt_metadata,
                    "duration_seconds": round(duration_estimate_sec, 1) if duration_estimate_sec else None,
                    "stt_elapsed_seconds": round(now - stt_started_at, 1),
                    "stt_status": progress_state.get("stt_status"),
                    "stt_provider": stt_provider_value,
                })
            tr = remote_stt_task.result()
        else:
            stt_process, stt_queue = start_transcription_process(
                out_audio,
                model_size=model_size,
                speed_profile=speed_profile,
                language=language,
            )
            stt_result = None
            stt_error: str | None = None
            last_sent_progress = 22.0
            last_emit_at = time.perf_counter()
            while True:
                if time.perf_counter() - stt_started_at > stt_timeout:
                    if stt_process is not None and stt_process.is_alive():
                        stt_process.terminate()
                        stt_process.join(timeout=5)
                    raise RuntimeError("STT processing timed out")
                for message in drain_queue(stt_queue):
                    message_type = message.get("type")
                    if message_type == "progress":
                        safe_frac = max(0.0, min(float(message.get("value") or 0), 1.0))
                        progress_state["stt_progress"] = safe_frac
                        progress_state["latest"] = max(
                            float(progress_state.get("latest") or 22.0),
                            22 + safe_frac * 38,  # 22–60 range
                        )
                        if duration_estimate_sec:
                            progress_state["transcribed_seconds"] = safe_frac * duration_estimate_sec
                    elif message_type == "status":
                        status = message.get("status") or progress_state["stt_status"]
                        progress_state["stt_status"] = status
                        progress_state["latest"] = max(
                            float(progress_state.get("latest") or 22.0),
                            status_progress_floor.get(str(status), 22.0),
                        )
                    elif message_type == "result":
                        stt_result = message.get("result")
                    elif message_type == "error":
                        stt_error = message.get("error") or "STT worker failed"

                if stt_result is not None:
                    break
                if stt_error:
                    raise RuntimeError(stt_error)
                if stt_process is not None and not stt_process.is_alive():
                    for message in drain_queue(stt_queue):
                        if message.get("type") == "result":
                            stt_result = message.get("result")
                        elif message.get("type") == "error":
                            stt_error = message.get("error") or "STT worker failed"
                    if stt_result is not None:
                        break
                    if stt_error:
                        raise RuntimeError(stt_error)
                    raise RuntimeError(f"STT worker exited unexpectedly with code {stt_process.exitcode}")

                await asyncio.sleep(0.5)
                latest_progress = float(progress_state.get("latest") or 22.0)
                now = time.perf_counter()
                if latest_progress >= last_sent_progress + 1 or now - last_emit_at >= 2:
                    last_sent_progress = max(last_sent_progress, latest_progress)
                    last_emit_at = now
                    upsert_job(
                        task_id=task_id_value,
                        status="running",
                        stage="stt",
                        progress=round(latest_progress, 1),
                        metadata={
                            "stt_provider": stt_provider_value,
                            "stt_provider_label": stt_provider_label(stt_provider_value),
                            "stt_progress": round(float(progress_state.get("stt_progress") or 0), 4),
                            "transcribed_seconds": round(float(progress_state.get("transcribed_seconds") or 0), 1),
                            "duration_seconds": round(duration_estimate_sec, 1) if duration_estimate_sec else None,
                            "stt_elapsed_seconds": round(now - stt_started_at, 1),
                            "stt_status": progress_state.get("stt_status"),
                        },
                    )
                    yield _sse({
                        "stage": "stt",
                        "progress": round(latest_progress, 1),
                        "stt_progress": round(float(progress_state.get("stt_progress") or 0), 4),
                        "transcribed_seconds": round(float(progress_state.get("transcribed_seconds") or 0), 1),
                        "duration_seconds": round(duration_estimate_sec, 1) if duration_estimate_sec else None,
                        "stt_elapsed_seconds": round(now - stt_started_at, 1),
                        "stt_status": progress_state.get("stt_status"),
                        "stt_provider": stt_provider_value,
                    })
            if stt_process is not None:
                stt_process.join(timeout=2)
            tr = stt_result
        stt_elapsed_sec = time.perf_counter() - stt_started_at
        upsert_job(
            task_id=task_id_value,
            status="running",
            stage="stt",
            progress=60,
            metadata={
                "stt_provider": stt_provider_value,
                "stt_provider_label": stt_provider_label(stt_provider_value),
                **cloud_stt_metadata,
                "stt_progress": 1,
                "duration_seconds": round(duration_estimate_sec, 1) if duration_estimate_sec else None,
            },
        )
        yield _sse({
            "stage": "stt",
            "progress": 60,
            "stt_progress": 1,
            **cloud_stt_metadata,
            "transcribed_seconds": round(duration_estimate_sec, 1) if duration_estimate_sec else None,
            "duration_seconds": round(duration_estimate_sec, 1) if duration_estimate_sec else None,
            "stt_provider": stt_provider_value,
        })

        duration_sec = tr.duration or (tr.segments[-1].end if tr.segments else 0)
        stt_realtime_factor = _stt_realtime_factor(stt_elapsed_sec, duration_sec)
        transcript_text = tr.text
        stt_model_for_result = remote_stt_policy.model_name if uses_remote_stt else model_size
        log_event(
            task_id=task_id_value,
            event_name="stt_completed",
            source_type=source_type,
            source_filename=source_filename,
            source_duration_seconds=round(duration_sec, 1),
            source_file_size_mb=source_file_size_mb,
            transcript_length=_text_len(transcript_text),
            stage="stt",
            duration_seconds=round(stt_elapsed_sec, 3),
            success=True,
            metadata=event_metadata(
                **runtime_context_metadata(),
                route="/process",
                source_fingerprint=source_fingerprint,
                stt_provider=stt_provider_value,
                stt_provider_label=stt_provider_label(stt_provider_value),
                stt_model=stt_model_for_result,
                stt_speed=speed_profile,
                stt_language=language,
                **cloud_stt_metadata,
                device_requested=getattr(tr, "device_requested", None) or "auto",
                device_resolved=getattr(tr, "device_resolved", None),
                vad_filter=getattr(tr, "vad_filter", None),
                cpu_threads=getattr(tr, "cpu_threads", None),
                num_workers=getattr(tr, "num_workers", None),
                detected_language=tr.language,
                language_probability=tr.language_probability,
                segment_count=len(tr.segments),
                stt_realtime_factor=stt_realtime_factor,
                model_cache_hit=getattr(tr, "model_cache_hit", None),
                model_load_seconds=getattr(tr, "model_load_seconds", None),
                model_source=getattr(tr, "model_source", None),
                compute_type=getattr(tr, "compute_type", None),
            ),
        )
        base_result: dict[str, Any] = {
            "task_id": task_id_value,
            "filename": source_filename,
            "raw_title": raw_title_value,
            "display_title": display_title_value,
            "source_file_available": True,
        }
        if preprocess_state:
            base_result["debreath"] = preprocess_state
            # Which file this transcript describes, and therefore which file the
            # page should play. Recorded rather than inferred: a transcript's
            # timestamps only mean something against one file, and getting that
            # wrong is invisible — the captions are simply progressively late.
            if preprocess_state.get("used_for_transcription"):
                base_result["transcript_media"] = TRANSCRIPT_MEDIA_CUT
                base_result["playback_media_kind"] = DEBREATH_MEDIA_KIND
            else:
                base_result["transcript_media"] = TRANSCRIPT_MEDIA_SOURCE
        if uses_remote_stt:
            base_result["cloud_transcription"] = remote_stt_policy.result_diagnostics(cloud_stt_metadata)
        cleanup_started_at = time.perf_counter()
        cleanup_result = clean_repeated_transcript(tr.segments)
        if cleanup_result.applied_count > 0:
            log_event(
                task_id=task_id_value,
                event_name="transcript_cleanup_completed",
                source_type=source_type,
                source_filename=source_filename,
                source_duration_seconds=round(duration_sec, 1),
                source_file_size_mb=source_file_size_mb,
                transcript_length=cleanup_result.cleaned_length,
                stage="transcript_cleanup",
                duration_seconds=round(time.perf_counter() - cleanup_started_at, 3),
                success=True,
                metadata=event_metadata(
                    route="/process",
                    cleanup_issue_count=len(cleanup_result.issues),
                    cleanup_applied_count=cleanup_result.applied_count,
                    cleanup_removed_segment_count=cleanup_result.removed_segment_count,
                    cleanup_raw_length=cleanup_result.raw_length,
                    cleanup_cleaned_length=cleanup_result.cleaned_length,
                ),
            )
        transcript_text = cleanup_result.cleaned_text
        segments_payload = list(cleanup_result.cleaned_segments)
        raw_segments_payload = [
            _raw_segment_payload(s)
            for s in tr.segments
        ]
        segments_payload, speaker_payload = await label_speakers(
            ctx,
            transcription=tr,
            segments_payload=segments_payload,
            duration_sec=duration_sec,
            audio_path=out_audio,
            uses_remote_stt=uses_remote_stt,
        )
        source_language = _normalized_source_language(getattr(tr, "language", None)) or _normalized_source_language(language)
        bilingual_segments: list[dict[str, Any]] = []
        translation_status = "not_applicable"
        translation_error: str | None = None
        if _is_english_source(source_language) and segments_payload:
            current_stage = "translation"
            translation_status = "running"
            upsert_job(task_id=task_id_value, status="running", stage="translation", progress=61)
            yield _sse({"stage": "translation", "progress": 61})
            translation_started_at = time.perf_counter()
            try:
                translation_kwargs = _translation_ai_kwargs(ai_kwargs_builder(
                    deepseek_api_key=deepseek_api_key,
                    openai_api_key=openai_api_key,
                    qwen_api_key=qwen_api_key,
                    ai_provider=ai_provider,
                    ai_model=ai_model,
                    system_prompt=None,
                ))
                translation_result = await loop.run_in_executor(
                    None,
                    lambda: generate_bilingual_segments_zh(segments_payload, **translation_kwargs),
                )
                bilingual_segments = translation_result.segments
                translation_status = "completed" if bilingual_segments else "failed"
                if not bilingual_segments:
                    translation_error = "AI returned no usable bilingual subtitle segments"
                log_event(
                    task_id=task_id_value,
                    event_name="translation_completed" if bilingual_segments else "translation_failed",
                    source_type=source_type,
                    source_filename=source_filename,
                    source_duration_seconds=round(duration_sec, 1),
                    source_file_size_mb=source_file_size_mb,
                    transcript_length=_text_len(transcript_text),
                    stage="translation",
                    duration_seconds=round(time.perf_counter() - translation_started_at, 3),
                    success=bool(bilingual_segments),
                    error_reason=translation_error,
                    metadata=event_metadata(
                        route="/process",
                        source_language=source_language,
                        bilingual_segment_count=len(bilingual_segments),
                        translated_segment_count=len([segment for segment in bilingual_segments if segment.get("text_zh")]),
                        translation_chunk_count=translation_result.chunk_count,
                    ),
                )
            except Exception as exc:
                translation_status = "failed"
                translation_error = friendly_error_message(exc)
                logger.warning("Segment translation failed for %s: %s", task_id_value, exc)
                log_event(
                    task_id=task_id_value,
                    event_name="translation_failed",
                    source_type=source_type,
                    source_filename=source_filename,
                    source_duration_seconds=round(duration_sec, 1),
                    source_file_size_mb=source_file_size_mb,
                    transcript_length=_text_len(transcript_text),
                    stage="translation",
                    duration_seconds=round(time.perf_counter() - translation_started_at, 3),
                    success=False,
                    error_reason=translation_error,
                    metadata=event_metadata(route="/process", source_language=source_language, raw_error=str(exc)),
                )
        base_result.update({
            "task_id": task_id_value,
            "filename": source_filename,
            "raw_title": raw_title_value,
            "display_title": display_title_value,
            "transcript_text": transcript_text,
            "raw_transcript_text": tr.text,
            "cleaned_transcript_text": cleanup_result.cleaned_text,
            "transcript_text_preview": transcript_text[:200],
            "summary_markdown": "",
            "audio_duration_seconds": round(duration_sec, 1),
            "stt_elapsed_seconds": round(stt_elapsed_sec, 1),
            "stt_realtime_factor": stt_realtime_factor,
            "stt_provider": stt_provider_value,
            "stt_provider_label": stt_provider_label(stt_provider_value),
            "stt_model": stt_model_for_result,
            "stt_speed": speed_profile,
            "stt_language": language,
            "detected_language": tr.language,
            "source_language": source_language,
            "subtitle_mode": "bilingual_zh" if bilingual_segments else "source_only",
            "translation_status": translation_status,
            "translation_error": translation_error,
            "source_fingerprint": source_fingerprint,
            "display_segments": bilingual_segments or segments_payload,
            "raw_segments": segments_payload,
            "speaker_diarization": speaker_payload,
            "stt_raw_segments": raw_segments_payload,
            "transcript_cleanup": _cleanup_payload(cleanup_result),
            "status": "transcript_ready",
            "source": source_type,
            "summary_skipped": summary_disabled,
        })
        note_transcript_text = transcript_text
        note_segments_payload = segments_payload
        if not summary_disabled and transcript_correction_enabled():
            current_stage = "transcript_correction"
            upsert_job(task_id=task_id_value, status="running", stage="transcript_correction", progress=61)
            yield _sse({"stage": "transcript_correction", "progress": 61})
            correction_fields, note_transcript_text, note_segments_payload = await _run_transcript_correction_stage(
                loop=loop,
                task_id=task_id_value,
                route="/process",
                source_type=source_type,
                source_filename=source_filename,
                source_duration_seconds=round(duration_sec, 1),
                source_file_size_mb=source_file_size_mb,
                transcript_text=transcript_text,
                segments=segments_payload,
                deepseek_api_key=deepseek_api_key,
                secret_resolver=secret_resolver,
            )
            base_result.update(correction_fields)
        elif not summary_disabled:
            base_result["note_generation_transcript_source"] = "transcript_text"
        if playback_audio_path is not None:
            base_result = _attach_playback_audio_artifact(task_id_value, base_result, playback_audio_path)
        if enhanced_playback_audio_path is not None:
            base_result = _attach_enhanced_playback_audio_artifact(
                task_id_value, base_result, enhanced_playback_audio_path, voice_presence
            )
        elif voice_presence:
            # Measured but not corrected: the number is still worth reporting.
            base_result["voice_presence"] = voice_presence
        # Which take this transcript actually came from, so a re-run can
        # reproduce it and a reader can tell whether enhancement was in play.
        base_result["transcription_audio_take"] = stt_audio_take
        base_result = _attach_result_artifacts(task_id_value, base_result)
        if preprocess_artifacts:
            # After the standard artifacts, so the cut outputs cannot be dropped
            # by the pass that rewrites transcript and note files.
            base_result["artifacts"] = {
                **dict(base_result.get("artifacts") or {}),
                **preprocess_artifacts,
            }
        current_stage = "transcript_ready"
        log_event(
            task_id=task_id_value,
            event_name="transcript_ready",
            source_type=source_type,
            source_filename=source_filename,
            source_duration_seconds=round(duration_sec, 1),
            source_file_size_mb=source_file_size_mb,
            transcript_length=_text_len(transcript_text),
            stage="transcript_ready",
            success=True,
            metadata=event_metadata(route="/process", source_fingerprint=source_fingerprint),
        )
        upsert_job(
            task_id=task_id_value,
            status="running",
            stage="transcript_ready",
            progress=60,
            result=base_result,
            summary_status="pending",
        )
        yield _sse({
            "stage": "transcript_ready",
            "progress": 60,
            "result": base_result,
        })

        if summary_disabled:
            summary_status = "skipped"
            log_event(
                task_id=task_id_value,
                event_name="summary_skipped",
                source_type=source_type,
                source_filename=source_filename,
                source_duration_seconds=round(duration_sec, 1),
                source_file_size_mb=source_file_size_mb,
                transcript_length=_text_len(note_transcript_text),
                stage="summary",
                success=True,
                metadata=event_metadata(route="/process", reason="transcript_only_mode"),
            )
            result = result_for_transcript_only(base_result)
            quota_final = _finalize_task_usage(
                ctx,
                duration_seconds=duration_sec,
                transcript_text=transcript_text,
                summary_text="",
                skip_summary=True,
                reason="Finalize transcript-only task charge",
            )
            if quota_final:
                result["quota"] = quota_final
            result = _attach_result_artifacts(task_id_value, result)
            result = _finalize_result_storage(ctx, result)
            upsert_job(
                task_id=task_id_value,
                status="completed",
                stage="done",
                progress=100,
                result=result,
                summary_status=summary_status,
            )
            _log_task_completed(
                task_id=task_id_value,
                started_at=task_started_at,
                final_status="completed",
                source_type=source_type,
                source_filename=source_filename,
                source_duration_seconds=round(duration_sec, 1),
                source_file_size_mb=source_file_size_mb,
                transcript_length=_text_len(transcript_text),
                summary_length=0,
                summary_status=summary_status,
                lark_requested=do_lark,
                lark_success=None,
                stt_provider=stt_provider_value,
                stt_provider_labeler=stt_provider_label,
                completion_reason="summary_skipped",
            )
            _enforce_history_retention(ctx)
            yield _sse({"stage": "done", "progress": 100, "result": result})
            return

        # ── Stage 3: AI summarization ──────────────────────
        current_stage = "summary"
        upsert_job(task_id=task_id_value, status="running", stage="summary", progress=62)
        yield _sse({"stage": "summary", "progress": 62})

        summary_error: str | None = None
        summary_result = None
        note_mode_plan: dict[str, Any] = {}
        summary_started_at = time.perf_counter()
        try:
            kwargs = ai_kwargs_builder(
                deepseek_api_key=deepseek_api_key,
                openai_api_key=openai_api_key,
                qwen_api_key=qwen_api_key,
                ai_provider=ai_provider,
                ai_model=ai_model,
                system_prompt=system_prompt,
                note_mode=note_mode,
            )
            # Mode planning is intentionally a no-op; the summarizer resolves
            # "auto" from transcript length in the same model call.
            note_mode_plan = {}
            # The note is written from one flat string, so a speaker that only
            # lives in a segment field never reaches the writing model. Build a
            # prefixed copy for the note call and leave the stored transcript
            # text alone: downstream consumers read that one.
            note_speaker_text = build_speaker_annotated_transcript(note_segments_payload)
            note_input_text = note_speaker_text or note_transcript_text
            # Same dict the result carries, so whether the note actually saw
            # speaker labels is answerable from the stored task.
            speaker_payload["note_input_labeled"] = bool(note_speaker_text)
            summary_result = await loop.run_in_executor(
                None,
                lambda: summarize_transcript_with_metadata(
                    note_input_text,
                    speaker_labeled=bool(note_speaker_text),
                    **kwargs,
                ),
            )
            summary_md = summary_result.markdown
            if not summary_md.strip():
                raise ValueError("AI summarization returned empty result")
            summary_status = "completed"
            if generate_visuals and source_type == "video" and note_segments_payload:
                try:
                    if keyframe_extractor is None:
                        raise RuntimeError("Media job context is missing a keyframe extraction policy")
                    visual_plan = await loop.run_in_executor(
                        None,
                        lambda: plan_visual_evidence_requests(
                            summary_md,
                            note_segments_payload,
                            api_key=kwargs.get("api_key"),
                            model=kwargs.get("model"),
                            provider=kwargs.get("provider"),
                        ),
                    )
                    visual_requests = visual_plan.requests
                    if visual_requests:
                        frames_output_dir = _artifact_storage_dir() / task_id_value / "frames"
                        frames_output_dir.mkdir(parents=True, exist_ok=True)
                        keyframe_result = await loop.run_in_executor(
                            None,
                            lambda: keyframe_extractor(
                                str(in_path),
                                frames_output_dir,
                                segments=visual_requests_to_frame_segments(visual_requests),
                                scene_threshold=0.3,
                                max_scene_frames=0,
                                min_gap_seconds=0.8,
                            ),
                        )
                        frame_paths = [str(f["path"]) for f in keyframe_result.frames]
                        frame_metadata = keyframe_result.frames
                        if keyframe_result.skipped_reason:
                            visual_evidence_error = keyframe_result.skipped_reason
                            logger.info(
                                "Frame extraction skipped for %s via %s: %s",
                                task_id_value,
                                keyframe_result.provider,
                                keyframe_result.skipped_reason,
                            )
                    if frame_metadata:
                        visual_api_key = secret_resolver(qwen_api_key, "qwen_api_key")
                        if not visual_api_key and (kwargs.get("provider") == "qwen"):
                            visual_api_key = kwargs.get("api_key") or ""
                        visual_selection = await loop.run_in_executor(
                            None,
                            lambda: select_visual_evidence_frames(
                                visual_requests,
                                frame_metadata,
                                api_key=visual_api_key or None,
                                provider="qwen",
                            ),
                        )
                        visual_selections = visual_selection.selections
                except Exception as exc:
                    visual_evidence_error = friendly_error_message(exc)
                    logger.warning("Visual evidence planning failed for %s: %s", task_id_value, exc)
            log_event(
                task_id=task_id_value,
                event_name="summary_completed",
                source_type=source_type,
                source_filename=source_filename,
                source_duration_seconds=round(duration_sec, 1),
                source_file_size_mb=source_file_size_mb,
                transcript_length=_text_len(note_transcript_text),
                summary_length=_text_len(summary_md),
                stage="summary",
                duration_seconds=round(time.perf_counter() - summary_started_at, 3),
                success=True,
                metadata=event_metadata(
                    route="/process",
                    ai_provider=(ai_provider or "").strip() or None,
                    ai_model=(ai_model or "").strip() or None,
                    requested_note_mode=note_mode_plan.get("requested_note_mode") or (summary_result.requested_mode if summary_result is not None else None),
                    **_summary_result_metadata(summary_result),
                    **{key: value for key, value in note_mode_plan.items() if key.startswith("note_mode_plan_")},
                    visual_request_count=len(visual_requests) if visual_requests else None,
                    visual_selection_count=len(visual_selections) if visual_selections else None,
                    visual_evidence_error=visual_evidence_error,
                    frame_count=len(frame_paths) if frame_paths else None,
                    note_generation_transcript_source=base_result.get("note_generation_transcript_source"),
                ),
            )
        except Exception as exc:
            logger.warning("AI summarization failed: %s", exc)
            summary_error = friendly_error_message(exc)
            summary_md = ""
            summary_status = "failed"
            summary_elapsed_sec = time.perf_counter() - summary_started_at
            log_event(
                task_id=task_id_value,
                event_name="summary_failed",
                source_type=source_type,
                source_filename=source_filename,
                source_duration_seconds=round(duration_sec, 1),
                source_file_size_mb=source_file_size_mb,
                transcript_length=_text_len(transcript_text),
                stage="summary",
                duration_seconds=round(summary_elapsed_sec, 3),
                success=False,
                error_reason=summary_error,
                metadata=event_metadata(
                    route="/process",
                    ai_provider=(ai_provider or "").strip() or None,
                    ai_model=(ai_model or "").strip() or None,
                    requested_note_mode=note_mode_plan.get("requested_note_mode") or (note_mode or "").strip() or None,
                    raw_error=str(exc),
                    note_generation_transcript_source=base_result.get("note_generation_transcript_source"),
                    **{key: value for key, value in note_mode_plan.items() if key.startswith("note_mode_plan_")},
                ),
            )
            result = result_for_summary_failure(base_result, summary_error)
            result.update({key: value for key, value in note_mode_plan.items() if key.startswith("note_mode_plan_")})
            quota_final = _finalize_task_usage(
                ctx,
                duration_seconds=duration_sec,
                transcript_text=note_transcript_text,
                summary_text="",
                skip_summary=True,
                reason="Finalize transcription charge after summary failure",
            )
            if quota_final:
                result["quota"] = quota_final
            result = _attach_result_artifacts(task_id_value, result)
            result = _finalize_result_storage(ctx, result)
            upsert_job(
                task_id=task_id_value,
                status="completed",
                stage="done",
                progress=100,
                result=result,
                summary_status=summary_status,
                error_reason=summary_error,
            )
            _log_task_completed(
                task_id=task_id_value,
                started_at=task_started_at,
                final_status="completed",
                source_type=source_type,
                source_filename=source_filename,
                source_duration_seconds=round(duration_sec, 1),
                source_file_size_mb=source_file_size_mb,
                transcript_length=_text_len(note_transcript_text),
                summary_length=0,
                summary_status=summary_status,
                lark_requested=do_lark,
                lark_success=False if do_lark else None,
                stt_provider=stt_provider_value,
                stt_provider_labeler=stt_provider_label,
                completion_reason="summary_failed",
            )
            _enforce_history_retention(ctx)
            yield _sse({"stage": "done", "progress": 100, "result": result})
            return

        upsert_job(task_id=task_id_value, status="running", stage="summary", progress=88)
        yield _sse({"stage": "summary", "progress": 88})

        # ── Build result ───────────────────────────────────
        result = result_for_summary_success(
            base_result,
            summary_md,
            requested_note_mode=note_mode_plan.get("requested_note_mode") or (summary_result.requested_mode if summary_result is not None else None),
            resolved_note_mode=summary_result.resolved_mode if summary_result is not None else None,
            note_mode_chunk_count=summary_result.chunk_count if summary_result is not None else None,
            note_mode_segment_count=getattr(summary_result, "segment_count", None) if summary_result is not None else None,
            note_mode_evidence_count=getattr(summary_result, "evidence_count", None) if summary_result is not None else None,
            note_mode_chapter_count=getattr(summary_result, "chapter_count", None) if summary_result is not None else None,
            note_mode_important_evidence_count=getattr(summary_result, "important_evidence_count", None) if summary_result is not None else None,
            note_mode_covered_important_evidence_count=getattr(summary_result, "covered_important_evidence_count", None) if summary_result is not None else None,
            note_mode_coverage_missing_count=getattr(summary_result, "coverage_missing_count", None) if summary_result is not None else None,
            chapter_coverage=getattr(summary_result, "chapter_coverage", None) if summary_result is not None else None,
            note_mode_plan_reason=note_mode_plan.get("note_mode_plan_reason"),
            note_mode_plan_confidence=note_mode_plan.get("note_mode_plan_confidence"),
            note_mode_plan_warnings=note_mode_plan.get("note_mode_plan_warnings"),
            note_mode_plan_provider=note_mode_plan.get("note_mode_plan_provider"),
            note_mode_plan_model=note_mode_plan.get("note_mode_plan_model"),
            note_mode_plan_fallback=note_mode_plan.get("note_mode_plan_fallback"),
            note_mode_plan_error=note_mode_plan.get("note_mode_plan_error"),
            note_mode_plan_selected_mode=note_mode_plan.get("note_mode_plan_selected_mode"),
            prompt_preset=(prompt_preset or "").strip() or None,
            prompt_preset_label=(prompt_preset_label or "").strip() or None,
        )
        if visual_requests:
            result["visual_requests"] = visual_requests
        if visual_selections:
            result["visual_frame_selections"] = visual_selections
            summary_md = inject_visual_evidence_references(summary_md, visual_selections)

        # Register frame files as artifacts
        if frame_paths:
            frame_artifacts = []
            for fm in frame_metadata:
                frame_name = Path(fm["path"]).name
                try:
                    art = _write_file_artifact(task_id_value, "frame", f"frames/{frame_name}", fm["path"])
                    for key in (
                        "timestamp_seconds",
                        "source",
                        "provider",
                        "visual_hash",
                        "brightness",
                        "contrast",
                        "edge_contrast",
                        "low_information",
                        "visual_request_id",
                        "note_section",
                        "query",
                        "reason",
                        "purpose",
                    ):
                        if fm.get(key) is not None:
                            art[key] = fm.get(key)
                    art["content_type"] = "image/jpeg"
                    frame_artifacts.append(art)
                except Exception as exc:
                    logger.warning("Frame artifact write failed for %s: %s", task_id_value, exc)
            if frame_artifacts:
                result["frame_artifacts"] = frame_artifacts
                result["visual_evidence_pipeline"] = "text_plan_qwen_local_window"
                result["frame_count"] = len(frame_artifacts)
                summary_md = rewrite_note_image_references(summary_md, frame_artifacts)
                result["summary_markdown"] = summary_md
                visual_payload = build_visual_evidence_from_note_images(
                    summary_md,
                    frame_artifacts,
                    provider=frame_artifacts[0].get("provider"),
                )
                result.update(visual_payload)
                result.update(build_visual_key_moments(
                    visual_selections,
                    frame_artifacts,
                    visual_evidence=visual_payload.get("visual_evidence") if isinstance(visual_payload.get("visual_evidence"), list) else [],
                    provider=frame_artifacts[0].get("provider"),
                ))
            elif visual_requests:
                summary_md = str(result.get("summary_markdown") or summary_md)
                result["visual_evidence"] = []
                result["visual_artifacts"] = {}
                result["visual_key_moments"] = []
                result["visual_key_moments_status"] = "unavailable"
                result["visual_key_moments_reason"] = "没有成功写入候选帧产物；关键画面候选不可用。"
                result["visual_evidence_status"] = "unavailable"
                result["visual_evidence_reason"] = (
                    visual_evidence_error
                    or "截图候选帧没有成功写入产物，最终笔记不插入截图。"
                )
        elif visual_requests:
            result["visual_evidence"] = []
            result["visual_artifacts"] = {}
            result["visual_key_moments"] = []
            result["visual_key_moments_status"] = "unavailable"
            result["visual_key_moments_reason"] = "文本模型提出了截图需求，但当前任务没有生成可用候选帧。"
            result["visual_evidence_status"] = "unavailable"
            result["visual_evidence_reason"] = (
                visual_evidence_error
                or "文本模型提出了截图需求，但当前任务没有生成可用候选帧。"
            )

        # ── Stage 4: Lark export (optional) ───────────────
        if do_lark:
            current_stage = "export"
            yield _sse({"stage": "export", "progress": 90})
            lark_success = await export_note_to_lark(
                ctx,
                result=result,
                duration_sec=duration_sec,
                transcript_text=transcript_text,
                summary_md=summary_md,
            )

        # ── Done ───────────────────────────────────────────
        quota_final = _finalize_task_usage(
            ctx,
            duration_seconds=duration_sec,
            transcript_text=note_transcript_text,
            summary_text=summary_md,
            skip_summary=False,
        )
        if quota_final:
            result["quota"] = quota_final
        result = _attach_result_artifacts(task_id_value, result)
        result = _finalize_result_storage(ctx, result)
        _log_task_completed(
            task_id=task_id_value,
            started_at=task_started_at,
            final_status="completed",
            source_type=source_type,
            source_filename=source_filename,
            source_duration_seconds=round(duration_sec, 1) if duration_sec is not None else None,
            source_file_size_mb=source_file_size_mb,
            transcript_length=_text_len(transcript_text),
            summary_length=_text_len(summary_md),
            summary_status=summary_status,
            lark_requested=do_lark,
            lark_success=lark_success,
            stt_provider=stt_provider_value,
            stt_provider_labeler=stt_provider_label,
        )
        upsert_job(
            task_id=task_id_value,
            status="completed",
            stage="done",
            progress=100,
            result=result,
            summary_status=summary_status,
        )
        _enforce_history_retention(ctx)
        yield _sse({"stage": "done", "progress": 100, "result": result})

    except asyncio.CancelledError:
        report_cancelled(
            TerminalReport(
                ctx=ctx,
                current_stage=current_stage,
                stt_process=stt_process,
                duration_sec=duration_sec,
                duration_estimate_sec=duration_estimate_sec,
                transcript_text=transcript_text,
                summary_md=summary_md,
                summary_status=summary_status,
                lark_success=lark_success,
                cloud_stt_metadata=cloud_stt_metadata,
            )
        )
        raise
    except Exception as exc:
        friendly_error = report_failed(
            TerminalReport(
                ctx=ctx,
                current_stage=current_stage,
                stt_process=stt_process,
                duration_sec=duration_sec,
                duration_estimate_sec=duration_estimate_sec,
                transcript_text=transcript_text,
                summary_md=summary_md,
                summary_status=summary_status,
                lark_success=lark_success,
                cloud_stt_metadata=cloud_stt_metadata,
            ),
            exc,
        )
        yield _sse({"stage": "error", "progress": 0, "error": friendly_error})
    finally:
        if stt_process is not None and stt_process.is_alive():
            terminate_process(stt_process)
        if stt_queue is not None:
            try:
                stt_queue.close()
                stt_queue.join_thread()
            except Exception:
                pass
        shutil.rmtree(td, ignore_errors=True)


async def execute_media_job(ctx: MediaJobContext) -> None:
    """Drain the pipeline stream into the job event hub."""
    hub = ctx.job_events
    if hub is None:
        raise RuntimeError("Media job context is missing an event hub")
    terminal_sent = False
    try:
        async for chunk in _stream_media_job(ctx):
            event = event_from_sse_chunk(chunk)
            if event is None:
                continue
            if JobEventHub.is_terminal(event):
                terminal_sent = True
            await hub.publish(ctx.task_id_value, event)
    except asyncio.CancelledError:
        terminal_sent = True
        await hub.publish(
            ctx.task_id_value,
            {"stage": "error", "progress": 0, "error": "Task cancelled"},
        )
    except Exception as exc:
        logger.exception("Background processing failed")
        await hub.publish(
            ctx.task_id_value,
            {"stage": "error", "progress": 0, "error": str(exc)},
        )
    finally:
        job = get_job(ctx.task_id_value)
        if not terminal_sent:
            if job and job.get("status") in {"completed", "failed", "cancelled"}:
                await hub.publish(ctx.task_id_value, JobEventHub.event_from_job(job))
        if (
            ctx.sync_terminal_result
            and ctx.terminal_result_sync
            and job
            and job.get("status") in {"completed", "failed", "cancelled"}
        ):
            try:
                await asyncio.to_thread(ctx.terminal_result_sync, job)
            except Exception:  # pragma: no cover - local sync must never hide a completed task
                logger.exception("Terminal result synchronization could not be queued for %s", ctx.task_id_value)
