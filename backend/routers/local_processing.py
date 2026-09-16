"""Local-edition processing entry: upload one media file, run the pipeline on
the local event hub, and stream progress via SSE.

Functionally local: no accounts, no quota or rate limits, no cloud STT, no
desktop-sync side effects. The worker is started on
``local_job_runtime.JOB_EVENTS`` — the same hub the local read (SSE) and cancel
routes use — so live progress, subscription, and cancellation share one hub.

This router is ``local_ready``: it composes only local and shared policies into
the edition-neutral media worker. It has no account, quota, cloud-STT, or
desktop-sync side effect.
"""

from __future__ import annotations

import asyncio
import functools
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.job_store import delete_jobs, get_job, list_jobs_for_retention, recent_local_folders, update_job_result, upsert_job
from backend.core.history_retention import enforce_history_retention
from backend.core.local_retention_config import artifact_retention_days
from backend.core.local_entry_guards import (
    claim_task_id,
    friendly_error,
    local_ai_kwargs,
    run_worker_with_terminal_state,
)
from backend.core import local_file_chooser, local_folder_intake, local_intake_flow
from backend.core.local_config import resolve_secret
from backend.core.local_job_runtime import JOB_EVENTS
from backend.core.local_limits_config import (
    max_media_duration_seconds,
    max_queue_files,
    max_upload_mb,
)
from backend.core.local_request_scope import request_client_id, request_is_localhost
from backend.core.local_retention_config import source_retention_days
from backend.core.local_stt_policy import DEFAULT_LOCAL_STT_MODEL, LOCAL_STT_PROVIDER
from backend.core.local_keyframe_provider import extract_keyframes as extract_local_keyframes
from backend.core.local_task_detail import build_task_snapshot
from backend.core.media_intake import (
    ALLOWED_SUFFIXES,
    MediaIntakeSizeError,
    copy_source_file,
    file_size_mb,
    path_size_mb,
    persist_source_stream,
    source_type_for_suffix,
)
from backend.core.lark_cli_exporter import export_markdown_via_lark_cli
from backend.core.lark_exporter import export_markdown_to_lark
from backend.core.media_job import MediaJobContext, execute_media_job
from backend.core.media_preflight import MediaPreflightError, preflight_media_file
from backend.core.result_retention import finalize_completed_result_storage
from backend.core.queue_options import _queue_options_from_mapping
from backend.core.storage_cleanup import remove_tree
from backend.core.storage_paths import find_source_file
from backend.core.title_display import display_title_for_user
from backend.core.note_title import resolve_lark_doc_title
from backend.core.storage_paths import _artifact_storage_dir

router = APIRouter()


def _local_stt_provider_label(_provider: str) -> str:
    return "faster-whisper"


def _finalize_local_result_storage(task_id: str, result: dict, metadata: dict | None) -> dict:
    return finalize_completed_result_storage(task_id, result, metadata, source_retention_days=source_retention_days(), find_source_file=find_source_file)


def _auto_export_local_lark(**values: object) -> dict:
    route = str(values.get("lark_export_route") or "").lower()
    if route in {"user_oauth", "feishu_user", "feishu_user_oauth", "lark_user_oauth"}:
        raise RuntimeError("本地版不支持飞书账号 OAuth 导出。")
    target = "lark_cli" if route in {"local_cli", "lark_cli"} or _truthy(values.get("lark_via_cli")) else "lark_openapi"
    title = resolve_lark_doc_title(str(values["summary_markdown"]), filename_stem=str(values["filename_stem"]), form_title=str(values.get("form_title") or ""))
    if target == "lark_cli":
        response = export_markdown_via_lark_cli(title, str(values["summary_markdown"]))
    else:
        kwargs = {}
        if app_id := resolve_secret(values.get("lark_app_id"), "lark_app_id"): kwargs["app_id"] = app_id
        if app_secret := resolve_secret(values.get("lark_app_secret"), "lark_app_secret"): kwargs["app_secret"] = app_secret
        if values.get("folder_token"): kwargs["folder_token"] = values["folder_token"]
        response = export_markdown_to_lark(title, str(values["summary_markdown"]), task_id=str(values["task_id"]), artifact_root=_artifact_storage_dir(), **kwargs)
    return {"doc_title": title, "export_target": target, "response": response}

def _enforce_local_history_retention(client_id: str | None) -> dict:
    return enforce_history_retention(client_id, keep_count=0, artifact_days=artifact_retention_days(), source_days=source_retention_days(), list_jobs=list_jobs_for_retention, update_result=update_job_result, delete_jobs=delete_jobs)


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _effective_duration_limit(requested: object = None) -> float | None:
    configured = _positive_float(max_media_duration_seconds())
    override = _positive_float(requested)
    if configured is not None and override is not None:
        return min(configured, override)
    return configured if configured is not None else override


def _collect_options(**raw_options: Optional[str]) -> dict[str, str]:
    """Normalize form fields into the persisted queue-options vocabulary
    (drop empties, strip values) shared by /process, /queue/process, and retry."""
    return {
        key: value.strip()
        for key, value in raw_options.items()
        if isinstance(value, str) and value.strip()
    }


async def _persist_uploaded_source(
    task_id: str,
    suffix: str,
    upload: UploadFile,
    limit_mb: float,
) -> tuple[Path, float | None]:
    max_bytes = int(limit_mb * 1024 * 1024) if limit_mb > 0 else None
    await upload.seek(0)
    try:
        path, byte_count = await asyncio.to_thread(
            persist_source_stream,
            task_id,
            suffix,
            upload.file,
            max_bytes=max_bytes,
        )
    except MediaIntakeSizeError as exc:
        actual_mb = file_size_mb(exc.byte_count)
        raise HTTPException(
            status_code=413,
            detail=f"File is too large: {actual_mb} MB. Limit is {limit_mb:g} MB.",
        ) from exc
    return path, file_size_mb(byte_count)


# Serial-processing chain for the local edition: one live worker at a time.
# Each queued runner waits for its predecessor's completion event before
# running the pipeline. Single-event-loop assumption (uvicorn / the local
# composition root); the chain is process-local state like the hub itself.
#
# The tail carries the predecessor's task id as well as its event, because the
# event alone cannot answer the question that matters: an unset event means
# "still working" and "gone without releasing" equally well. Waiting on it
# forever is what left every later upload sitting at "queued 0%" on an idle
# machine, with restarting the service as the only exit. The id makes the
# difference checkable — the hub knows whether that task is still running.
_QUEUE_TAIL: dict[str, Any] = {"task_id": None, "event": None}

# How often a waiting runner looks up from the event to ask whether the job
# ahead of it still exists. Not a limit on how long a job may take: a lecture
# takes hours and that is normal, so nothing here ever cuts a live job short.
_QUEUE_HEARTBEAT_SECONDS = 5.0


def _record_queue_wait(
    task_id: str, client_id: Optional[str], waiting_for: Optional[str]
) -> None:
    """Put the wait on the job row, where the task list will read it.

    The list polls stored jobs rather than the live event stream, so a wait that
    only ever went out as an event is a wait the screen cannot show. ``since``
    is what separates "this only just joined the queue" from "the one ahead has
    not moved in an hour" — the failure this cannot heal by itself, because no
    fixed number tells a wedged job apart from a long lecture.
    """
    # ``queued`` is what this job is at both moments this runs — it starts waiting
    # and it stops waiting, both before the pipeline begins. Writing the status
    # rather than leaving it out also keeps the store's own guard in play: a row
    # already cancelled is not updated by a write that is not a cancellation, so
    # a job cancelled mid-wait is not resurrected by its own bookkeeping.
    upsert_job(
        task_id=task_id,
        status="queued",
        client_id=client_id,
        metadata={
            "queue_wait": None if waiting_for is None else {
                "waiting_for": waiting_for,
                "since": datetime.now(timezone.utc).isoformat(),
            }
        },
    )


async def _await_predecessor(
    previous: tuple[Optional[str], asyncio.Event], ctx: MediaJobContext
) -> None:
    """Wait for the job ahead — but only while it is still there.

    Returns when the predecessor releases the chain, or when it has left without
    releasing it. The second case is the repair: a runner is only created if the
    hub was not already running that task id, and a runner that was never created
    has no ``finally`` to set its event.

    Reports who it is waiting for while it waits, so a queue that is not moving
    can be read on screen instead of guessed at.
    """
    previous_id, previous_event = previous
    task_id = ctx.task_id_value
    announced = False
    try:
        while not previous_event.is_set():
            if not announced:
                _record_queue_wait(task_id, ctx.client_id, previous_id)
                await JOB_EVENTS.publish(
                    task_id,
                    {"stage": "queued", "progress": 0, "waiting_for": previous_id},
                )
                announced = True
            try:
                await asyncio.wait_for(previous_event.wait(), _QUEUE_HEARTBEAT_SECONDS)
                return
            except asyncio.TimeoutError:
                # Released on the heartbeat boundary: the predecessor set its event
                # and then ended, so asking the hub would answer "gone" for a job
                # that finished normally. Check the flag before the liveness
                # question, or a healthy handover gets filed as a repair and the one
                # signal a later session has for a real stuck queue stops meaning
                # anything.
                if previous_event.is_set():
                    return
                if previous_id and await JOB_EVENTS.has_running_task(previous_id):
                    continue
                log_event(
                    task_id=task_id,
                    event_name="queue_chain_healed",
                    stage="queued",
                    success=True,
                    metadata={"waiting_for": previous_id},
                )
                return
    finally:
        # The wait is over however it ended, so the row must stop saying it is
        # waiting — a stale "waiting for" on a running job is worse than none.
        if announced:
            _record_queue_wait(task_id, ctx.client_id, None)


def _queue_tail_barrier() -> Optional[tuple[Optional[str], asyncio.Event]]:
    """The job this one has to wait for, or None when the queue is empty."""
    event = _QUEUE_TAIL["event"]
    if event is None:
        return None
    return (_QUEUE_TAIL["task_id"], event)


async def _write_note_after_transcript(task_id: str, client_id: Optional[str]) -> None:
    """The last step of the flow: the note, from the file the transcript came from.

    Runs here rather than inside the pipeline because the note job persists its own
    progress and the pipeline owns the result until it completes. The seam is
    visible to the user as a task that is finished and readable while its note is
    still being written, which is why the result is marked accordingly first.

    Never raises: the transcript and the cut file are already the user's, and a
    note failure must not take them away. `write_note` records the reason where the
    note belongs.
    """
    if not local_intake_flow.note_is_wanted(task_id, client_id):
        return
    local_intake_flow.mark_note_running(task_id, client_id)
    await asyncio.to_thread(local_intake_flow.write_note, task_id, client_id)


async def _run_job_then_note(ctx: MediaJobContext) -> None:
    """The whole flow for one task: the pipeline, then its note."""
    await execute_media_job(ctx)
    await _write_note_after_transcript(ctx.task_id_value, ctx.client_id)


async def _run_serially(
    previous: Optional[tuple[Optional[str], asyncio.Event]],
    done: asyncio.Event,
    ctx: MediaJobContext,
) -> None:
    try:
        if previous is not None:
            try:
                await _await_predecessor(previous, ctx)
            except asyncio.CancelledError:
                # This runner may be cancelled while it is only a queue
                # placeholder. Keep its successor behind the same predecessor
                # barrier; releasing ``done`` immediately would let the next
                # worker overlap the still-running previous worker.
                await _await_predecessor(previous, ctx)
                raise
        await _run_job_then_note(ctx)
    finally:
        # Always release the chain — including when this job is cancelled while
        # still waiting (the cancel route persists the cancelled state).
        done.set()


def _local_media_job_context(
    *,
    task_id: str,
    client_id: Optional[str],
    source_type: str,
    source_filename: str,
    raw_title: str,
    display_title: str,
    suffix: str,
    td: str,
    in_path: Path,
    content: bytes,
    source_file_size_mb: float | None,
    duration_preflight_sec: float | None,
    options: dict[str, str],
    deepseek_api_key: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    qwen_api_key: Optional[str] = None,
    duration_limit_seconds: float | None = None,
) -> MediaJobContext:
    """Build the pipeline context with the local edition's invariants: local
    STT, no quota/account, the local event hub, and no desktop-sync side
    effects. ``options`` uses the persisted queue-options vocabulary so the
    upload and retry entries cannot drift apart."""
    return MediaJobContext(
        task_id_value=task_id,
        client_id=client_id,
        source_type=source_type,
        source_filename=source_filename,
        raw_title_value=raw_title,
        display_title_value=display_title,
        suffix=suffix,
        td=td,
        in_path=in_path,
        content=content,
        source_fingerprint=None,
        source_file_size_mb=source_file_size_mb,
        max_upload_mb=max_upload_mb(),
        duration_preflight_sec=duration_preflight_sec,
        quota_estimate=None,
        quota_reservation=None,
        task_started_at=time.perf_counter(),
        loop=asyncio.get_event_loop(),
        model_size=(options.get("stt_model") or "").strip() or DEFAULT_LOCAL_STT_MODEL,
        speed_profile=(options.get("stt_speed") or "").strip() or "balanced",
        language="auto",
        stt_provider_value=LOCAL_STT_PROVIDER,
        diarization_requested=_truthy(options.get("speaker_diarization")),
        voice_enhance_requested=_truthy(options.get("voice_enhance")),
        do_lark=_truthy(options.get("export_to_lark")),
        # The pipeline's own note stage is switched off whenever this edition is
        # going to write the note itself from the cut media. That is the "remove
        # the old flow" half of the owner's decision: without this the user gets a
        # note from the text model first and the real one a minute later, which is
        # the two-notes shape the whole change exists to end.
        summary_disabled=_truthy(options.get("skip_summary")) or local_intake_flow.auto_note_enabled(),
        generate_visuals=_truthy(options.get("generate_visuals")),
        source_last_modified_ms=None,
        export_to_lark=options.get("export_to_lark"),
        lark_export_route=options.get("lark_export_route"),
        lark_via_cli=options.get("lark_via_cli"),
        folder_token=options.get("folder_token"),
        deepseek_api_key=deepseek_api_key,
        openai_api_key=openai_api_key,
        qwen_api_key=qwen_api_key,
        ai_provider=options.get("ai_provider"),
        ai_model=options.get("ai_model"),
        note_mode=options.get("note_mode"),
        skip_summary=options.get("skip_summary"),
        system_prompt=options.get("system_prompt"),
        prompt_preset=options.get("prompt_preset"),
        prompt_preset_label=options.get("prompt_preset_label"),
        account_user=None,
        title=options.get("title"),
        lark_app_id=None,
        lark_app_secret=None,
        duration_limit_seconds=duration_limit_seconds,
        ai_kwargs_builder=local_ai_kwargs,
        secret_resolver=resolve_secret,
        keyframe_extractor=extract_local_keyframes,
        friendly_error=friendly_error,
        stt_provider_labeler=_local_stt_provider_label,
        job_events=JOB_EVENTS,
        sync_terminal_result=False,
        finalize_result_storage=_finalize_local_result_storage,
        auto_lark_exporter=_auto_export_local_lark,
        enforce_history_retention=_enforce_local_history_retention,
        media_preprocessor=local_intake_flow.preprocess_media,
    )


def _preflight_rejection(
    *,
    task_id: str,
    source_path: Path,
    source_type: str,
    source_filename: str,
    source_file_size_mb: float | None,
    client_id: Optional[str],
    error: MediaPreflightError,
    route: str = "/process",
    **extra_metadata: object,
) -> HTTPException:
    log_event(
        task_id=task_id,
        event_name="media_preflight_rejected",
        source_type=source_type,
        source_filename=source_filename,
        source_file_size_mb=source_file_size_mb,
        stage="import",
        success=False,
        error_reason=str(error),
        metadata=event_metadata(
            route=route,
            media_preflight={"status": "rejected", "code": error.code, **error.metadata},
            **extra_metadata,
        ),
    )
    upsert_job(
        task_id=task_id,
        status="failed",
        client_id=client_id,
        stage="import",
        progress=100,
        source_type=source_type,
        source_filename=source_filename,
        source_file_size_mb=source_file_size_mb,
        error_reason=str(error),
    )
    remove_tree(source_path.parent)
    return HTTPException(status_code=422, detail=str(error))


@router.post("/process")
async def process_media(
    request: Request,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    raw_title: Optional[str] = Form(None),
    display_title: Optional[str] = Form(None),
    export_to_lark: Optional[str] = Form(None),
    lark_export_route: Optional[str] = Form(None),
    lark_via_cli: Optional[str] = Form(None),
    folder_token: Optional[str] = Form(None),
    deepseek_api_key: Optional[str] = Form(None),
    openai_api_key: Optional[str] = Form(None),
    qwen_api_key: Optional[str] = Form(None),
    ai_provider: Optional[str] = Form(None),
    ai_model: Optional[str] = Form(None),
    note_mode: Optional[str] = Form(None),
    skip_summary: Optional[str] = Form(None),
    generate_visuals: Optional[str] = Form(None),
    stt_model: Optional[str] = Form(None),
    stt_speed: Optional[str] = Form(None),
    speaker_diarization: Optional[str] = Form(None),
    system_prompt: Optional[str] = Form(None),
    prompt_preset: Optional[str] = Form(None),
    prompt_preset_label: Optional[str] = Form(None),
    duration_limit_seconds: Optional[float] = Form(None),
    task_id: Optional[str] = Form(None),
) -> StreamingResponse:
    """Upload a media file and stream local processing progress via SSE."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")
    suffix = Path(file.filename).suffix.lower() or ".mp4"
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    client_id = _local_client_scope(request)
    task_id_value = claim_task_id(task_id, client_id=client_id)
    source_filename = file.filename
    raw_title_value = (raw_title or title or Path(source_filename).stem).strip()
    display_title_value = (display_title or display_title_for_user(raw_title_value, source_filename)).strip()
    source_type = source_type_for_suffix(suffix)
    limit_mb = max_upload_mb()
    try:
        in_path, source_file_size_mb = await _persist_uploaded_source(
            task_id_value,
            suffix,
            file,
            limit_mb,
        )
    except Exception:
        delete_jobs([task_id_value], client_id=client_id)
        raise
    try:
        media_preflight = preflight_media_file(in_path)
    except MediaPreflightError as exc:
        rejection = _preflight_rejection(
            task_id=task_id_value,
            source_path=in_path,
            source_type=source_type,
            source_filename=source_filename,
            source_file_size_mb=source_file_size_mb,
            client_id=client_id,
            error=exc,
        )
        raise rejection from exc
    duration_preflight_sec = media_preflight.duration_seconds
    td = tempfile.mkdtemp()
    effective_duration_limit = _effective_duration_limit(duration_limit_seconds)

    # Persist the processing options with the job so a later local retry can
    # recover them (same vocabulary as _queue_options_from_mapping).
    options = _collect_options(
        export_to_lark=export_to_lark,
        lark_export_route=lark_export_route,
        lark_via_cli=lark_via_cli,
        title=title,
        folder_token=folder_token,
        ai_provider=ai_provider,
        ai_model=ai_model,
        note_mode=note_mode,
        skip_summary=skip_summary,
        generate_visuals=generate_visuals,
        stt_model=stt_model,
        stt_speed=stt_speed,
        speaker_diarization=speaker_diarization,
        system_prompt=system_prompt,
        prompt_preset=prompt_preset,
        prompt_preset_label=prompt_preset_label,
        duration_limit_seconds=(
            str(effective_duration_limit) if effective_duration_limit is not None else None
        ),
    )
    job_metadata = event_metadata(
        route="/process",
        raw_title=raw_title_value,
        display_title=display_title_value,
        queue_options=options,
        media_preflight={"status": "passed", **media_preflight.as_metadata()},
    )
    log_event(
        task_id=task_id_value,
        event_name="source_imported",
        source_type=source_type,
        source_filename=source_filename,
        source_file_size_mb=source_file_size_mb,
        stage="import",
        success=True,
        metadata=job_metadata,
    )
    upsert_job(
        task_id=task_id_value,
        status="running",
        client_id=client_id,
        stage="import",
        progress=0,
        source_type=source_type,
        source_filename=source_filename,
        source_file_size_mb=source_file_size_mb,
        metadata=job_metadata,
    )

    ctx = _local_media_job_context(
        task_id=task_id_value,
        client_id=client_id,
        source_type=source_type,
        source_filename=source_filename,
        raw_title=raw_title_value,
        display_title=display_title_value,
        suffix=suffix,
        td=td,
        in_path=in_path,
        content=b"",
        source_file_size_mb=source_file_size_mb,
        duration_preflight_sec=duration_preflight_sec,
        options=options,
        deepseek_api_key=deepseek_api_key,
        openai_api_key=openai_api_key,
        qwen_api_key=qwen_api_key,
        duration_limit_seconds=effective_duration_limit,
    )

    await JOB_EVENTS.start(
        task_id_value,
        functools.partial(
            run_worker_with_terminal_state,
            task_id=task_id_value,
            client_id=client_id,
            hub=JOB_EVENTS,
            route="/process",
            stage="processing",
            # Same tail as the queued entry, so the two upload paths cannot end in
            # different products: one with a note, one without.
            worker=functools.partial(_run_job_then_note, ctx),
        ),
    )

    return StreamingResponse(
        JOB_EVENTS.subscribe(task_id_value),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/queue/process")
async def queue_process(
    request: Request,
    files: list[UploadFile] = File(...),
    title: Optional[str] = Form(None),
    export_to_lark: Optional[str] = Form(None),
    lark_export_route: Optional[str] = Form(None),
    lark_via_cli: Optional[str] = Form(None),
    folder_token: Optional[str] = Form(None),
    deepseek_api_key: Optional[str] = Form(None),
    openai_api_key: Optional[str] = Form(None),
    qwen_api_key: Optional[str] = Form(None),
    ai_provider: Optional[str] = Form(None),
    ai_model: Optional[str] = Form(None),
    note_mode: Optional[str] = Form(None),
    skip_summary: Optional[str] = Form(None),
    generate_visuals: Optional[str] = Form(None),
    stt_model: Optional[str] = Form(None),
    stt_speed: Optional[str] = Form(None),
    speaker_diarization: Optional[str] = Form(None),
    system_prompt: Optional[str] = Form(None),
    prompt_preset: Optional[str] = Form(None),
    prompt_preset_label: Optional[str] = Form(None),
    duration_limit_seconds: Optional[float] = Form(None),
) -> dict:
    """Queue several media files for serial local processing (one live worker
    at a time), all on the local event hub."""
    client_id = _local_client_scope(request)
    limit_files = max_queue_files()
    if limit_files > 0 and len(files) > limit_files:
        raise HTTPException(
            status_code=413,
            detail=f"Too many files uploaded: {len(files)}. Limit is {limit_files}.",
        )
    for upload in files:
        if not upload.filename:
            raise HTTPException(status_code=400, detail="Uploaded file is missing a filename")
        suffix = Path(upload.filename).suffix.lower() or ".mp4"
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    effective_duration_limit = _effective_duration_limit(duration_limit_seconds)
    options = _collect_options(
        export_to_lark=export_to_lark,
        lark_export_route=lark_export_route,
        lark_via_cli=lark_via_cli,
        title=title if len(files) == 1 else None,
        folder_token=folder_token,
        ai_provider=ai_provider,
        ai_model=ai_model,
        note_mode=note_mode,
        skip_summary=skip_summary,
        generate_visuals=generate_visuals,
        stt_model=stt_model,
        stt_speed=stt_speed,
        speaker_diarization=speaker_diarization,
        system_prompt=system_prompt,
        prompt_preset=prompt_preset,
        prompt_preset_label=prompt_preset_label,
        duration_limit_seconds=(
            str(effective_duration_limit) if effective_duration_limit is not None else None
        ),
    )
    limit_mb = max_upload_mb()
    total = len(files)

    # Prepare every source before queueing anything (all-or-nothing, matching
    # the hosted contract): persist, size-check, preflight.
    prepared_sources: list[dict] = []
    claimed_task_ids: list[str] = []
    try:
        for index, upload in enumerate(files, start=1):
            filename = Path(upload.filename or f"source-{index}").name
            suffix = Path(filename).suffix.lower() or ".mp4"
            task_id_value = claim_task_id(None, client_id=client_id)
            claimed_task_ids.append(task_id_value)
            source_path, source_file_size_mb = await _persist_uploaded_source(
                task_id_value,
                suffix,
                upload,
                limit_mb,
            )
            prepared = {
                "task_id": task_id_value,
                "filename": filename,
                "suffix": suffix,
                "source_type": source_type_for_suffix(suffix),
                "source_file_size_mb": source_file_size_mb,
                "source_path": source_path,
                "index": index,
            }
            prepared_sources.append(prepared)
            preflight = preflight_media_file(source_path)
            prepared["media_preflight"] = preflight.as_metadata()
    except Exception as exc:
        for prepared in prepared_sources:
            remove_tree(Path(prepared["source_path"]).parent)
        delete_jobs(claimed_task_ids, client_id=client_id)
        if isinstance(exc, MediaPreflightError) and prepared_sources:
            rejected = prepared_sources[-1]
            log_event(
                task_id=str(rejected["task_id"]),
                event_name="media_preflight_rejected",
                source_type=str(rejected["source_type"]),
                source_filename=str(rejected["filename"]),
                source_file_size_mb=rejected["source_file_size_mb"],
                stage="import",
                success=False,
                error_reason=str(exc),
                metadata=event_metadata(
                    route="/queue/process",
                    media_preflight={"status": "rejected", "code": exc.code, **exc.metadata},
                ),
            )
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, MediaPreflightError):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise HTTPException(
            status_code=500,
            detail="Could not prepare uploaded sources.",
        ) from exc

    queued: list[dict] = []
    for prepared in prepared_sources:
        task_id_value = str(prepared["task_id"])
        filename = str(prepared["filename"])
        source_type = str(prepared["source_type"])
        source_file_size_mb = prepared["source_file_size_mb"]
        index = int(prepared["index"])
        raw_title_value = (options.get("title") or Path(filename).stem).strip()
        display_title_value = display_title_for_user(raw_title_value, filename).strip()
        job_metadata = event_metadata(
            route="/queue/process",
            raw_title=raw_title_value,
            display_title=display_title_value,
            queue_options=options,
            queue_position=index,
            queue_total=total,
            source_path=str(prepared["source_path"]),
            media_preflight={"status": "passed", **prepared["media_preflight"]},
        )
        log_event(
            task_id=task_id_value,
            event_name="source_queued",
            source_type=source_type,
            source_filename=filename,
            source_file_size_mb=source_file_size_mb,
            stage="queued",
            success=True,
            metadata=job_metadata,
        )
        upsert_job(
            task_id=task_id_value,
            status="queued",
            client_id=client_id,
            stage="queued",
            progress=0,
            source_type=source_type,
            source_filename=filename,
            source_file_size_mb=source_file_size_mb,
            metadata=job_metadata,
        )
        ctx = _local_media_job_context(
            task_id=task_id_value,
            client_id=client_id,
            source_type=source_type,
            source_filename=filename,
            raw_title=raw_title_value,
            display_title=display_title_value,
            suffix=str(prepared["suffix"]),
            td=tempfile.mkdtemp(),
            in_path=prepared["source_path"],
            content=b"",
            source_file_size_mb=source_file_size_mb,
            duration_preflight_sec=prepared["media_preflight"].get("duration_seconds"),
            options=options,
            deepseek_api_key=deepseek_api_key,
            openai_api_key=openai_api_key,
            qwen_api_key=qwen_api_key,
            duration_limit_seconds=effective_duration_limit,
        )
        previous = _queue_tail_barrier()
        done = asyncio.Event()
        _QUEUE_TAIL["task_id"] = task_id_value
        _QUEUE_TAIL["event"] = done
        started = await JOB_EVENTS.start(
            task_id_value,
            functools.partial(
                run_worker_with_terminal_state,
                task_id=task_id_value,
                client_id=client_id,
                hub=JOB_EVENTS,
                route="/queue/process",
                stage="processing",
                worker=functools.partial(_run_serially, previous, done, ctx),
            ),
        )
        if not started:
            # Nothing will run that worker, so nothing will run its ``finally``.
            # Release the chain here rather than leaving the next upload behind a
            # barrier that has no one left to lift it.
            done.set()
        queued.append({
            "task_id": task_id_value,
            "filename": filename,
            "source_type": source_type,
            "source_file_size_mb": source_file_size_mb,
            "status": "queued",
            "queue_position": index,
            "queue_total": total,
        })
    return {"ok": True, "queued": queued, "count": len(queued)}


async def queue_local_media_file(
    source_path: Path,
    *,
    client_id: Optional[str],
    options: dict,
    duration_limit_seconds: float | None,
    route: str,
    origin: dict,
    index: int = 1,
    total: int = 1,
) -> dict:
    """Queue one recording that stays where it is, and answer what happened to it.

    Shared by the two local path entries — the system file dialog and a named
    folder — so a guard added for one cannot be missing from the other. The file is
    not copied into FluentFlow's store: the pipeline reads it in place, which is
    what makes the cut version's home ("beside this file") a real location.
    """
    try:
        preflight = preflight_media_file(source_path)
    except MediaPreflightError as exc:
        # Refused before a task row exists, so an unreadable file does not leave a
        # queued task that can never run.
        return {"filename": source_path.name, "status": "rejected", "reason": str(exc)}
    task_id_value = claim_task_id(None, client_id=client_id)
    suffix = source_path.suffix.lower() or ".mp4"
    raw_title_value = source_path.stem.strip()
    display_title_value = display_title_for_user(raw_title_value, source_path.name).strip()
    size_mb = file_size_mb(source_path.stat().st_size)
    job_metadata = event_metadata(
        route=route,
        raw_title=raw_title_value,
        display_title=display_title_value,
        queue_options=options,
        queue_position=index,
        queue_total=total,
        # Deliberately not under `video_source.file_path`: retention cleanup reads
        # that key, and this path is the user's own file. Cleanup also refuses
        # anything outside FluentFlow storage now, so this is the second lock
        # rather than the first.
        folder_intake={**origin, "original_path": str(source_path)},
        media_preflight={"status": "passed", **preflight.as_metadata()},
    )
    log_event(
        task_id=task_id_value,
        event_name="source_queued",
        source_type=source_type_for_suffix(suffix),
        source_filename=source_path.name,
        source_file_size_mb=size_mb,
        stage="queued",
        success=True,
        metadata=job_metadata,
    )
    upsert_job(
        task_id=task_id_value,
        status="queued",
        client_id=client_id,
        stage="queued",
        progress=0,
        source_type=source_type_for_suffix(suffix),
        source_filename=source_path.name,
        source_file_size_mb=size_mb,
        metadata=job_metadata,
    )
    ctx = _local_media_job_context(
        task_id=task_id_value,
        client_id=client_id,
        source_type=source_type_for_suffix(suffix),
        source_filename=source_path.name,
        raw_title=raw_title_value,
        display_title=display_title_value,
        suffix=suffix,
        td=tempfile.mkdtemp(),
        in_path=source_path,
        content=b"",
        source_file_size_mb=size_mb,
        duration_preflight_sec=preflight.as_metadata().get("duration_seconds"),
        options=options,
        duration_limit_seconds=duration_limit_seconds,
    )
    previous = _queue_tail_barrier()
    done = asyncio.Event()
    _QUEUE_TAIL["task_id"] = task_id_value
    _QUEUE_TAIL["event"] = done
    started = await JOB_EVENTS.start(
        task_id_value,
        functools.partial(
            run_worker_with_terminal_state,
            task_id=task_id_value,
            client_id=client_id,
            hub=JOB_EVENTS,
            route=route,
            stage="processing",
            worker=functools.partial(_run_serially, previous, done, ctx),
        ),
    )
    if not started:
        done.set()
    return {
        "task_id": task_id_value,
        "filename": source_path.name,
        "status": "queued",
        "queue_position": index,
        "queue_total": total,
    }


def _option_default_on(payload: dict, key: str) -> str:
    """The caller's value for a switch that is on unless they turned it off."""
    if key not in (payload or {}):
        return "true"
    raw = (payload or {}).get(key)
    if raw is None:
        return "true"
    return "true" if _truthy(str(raw)) else ""


def local_path_options(payload: dict) -> tuple[dict, float | None]:
    limit = _effective_duration_limit(payload.get("duration_limit_seconds"))
    return _collect_options(
        skip_summary=payload.get("skip_summary"),
        note_mode=payload.get("note_mode"),
        stt_model=payload.get("stt_model"),
        stt_speed=payload.get("stt_speed"),
        prompt_preset=payload.get("prompt_preset"),
        prompt_preset_label=payload.get("prompt_preset_label"),
        # Read back at :366 as diarization_requested / voice_enhance_requested.
        # Left out of this list, a caller's switch is dropped without an error
        # and the run reports success with the feature never having run.
        #
        # Speaker separation defaults ON here, not just in the settings page:
        # the page's default never reaches a caller that has no page — the Agent
        # API, the MCP tool, a curl. A 2026-09-03 report of an 8-person meeting
        # transcribed with no speakers was exactly that gap. An explicit false
        # still wins.
        speaker_diarization=_option_default_on(payload, "speaker_diarization"),
        voice_enhance=payload.get("voice_enhance"),
        duration_limit_seconds=(str(limit) if limit is not None else None),
    ), limit


@router.post("/local/locate-dropped")
def locate_dropped_media(request: Request, payload: dict = Body(None)) -> dict:
    """Say where a dropped file lives, if this machine already knows the folder.

    The page sends three labels — name, size, modification time — and no bytes.
    A browser gives it nothing else about a dropped file, on purpose: a web page
    must not learn the shape of somebody's disk. This edition is not a web page,
    it runs on the machine holding the file, so it can look.

    Answering "yes, it is here" before the upload starts is the whole point. It
    saves copying a gigabyte into the store, and it gives the cut file somewhere
    to land. "No" costs nothing: the page uploads, which is what it does today.

    Local requests only, and only folders this owner has already pointed at.
    """
    if not request_is_localhost(request):
        raise HTTPException(status_code=403, detail="只有本机能查本地文件位置。")
    body = payload or {}
    modified = body.get("modified_ms")
    found = local_folder_intake.locate_dropped_file(
        str(body.get("name") or ""),
        int(body.get("size_bytes") or 0),
        recent_local_folders(),
        modified_ms=float(modified) if isinstance(modified, (int, float)) else None,
    )
    if not found:
        return {"ok": True, "found": False}
    try:
        resolved = local_folder_intake.resolve_media_file(str(found))
    except local_folder_intake.FolderIntakeError as exc:
        return {"ok": True, "found": False, "reason": str(exc)}
    return {
        "ok": True,
        "found": True,
        "path": str(resolved),
        "folder": str(resolved.parent),
        "name": resolved.name,
    }


@router.post("/local/choose-media")
def choose_media_with_system_dialog(request: Request, payload: dict = Body(None)) -> dict:
    """Open this machine's own file dialog and report what was chosen.

    Local edition only. The browser's picker cannot tell the page which folder a
    file came from — that is a deliberate browser boundary — so the product has no
    way to save the cut version "next to the original" for an upload. The system
    dialog answers with a real path, which is the whole reason this exists.

    It makes a window appear on somebody's screen, so it is refused unless the
    request came from this machine. A cancelled dialog is a normal answer.
    """
    if not request_is_localhost(request):
        raise HTTPException(status_code=403, detail="只有本机能调起文件选择框。")
    reason = local_file_chooser.unavailable_reason()
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    body = payload or {}
    try:
        recent = recent_local_folders(limit=1)
        outcome = local_file_chooser.choose_media_files(
            prompt=str(body.get("prompt") or "选择要做笔记的录像"),
            allow_multiple=bool(body.get("allow_multiple", True)),
            start_in=recent[0] if recent else None,
        )
    except local_file_chooser.FileChooserError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if outcome.cancelled or not outcome.paths:
        return {"ok": True, "cancelled": True, "files": []}

    files: list[dict] = []
    for path in outcome.paths:
        try:
            resolved = local_folder_intake.resolve_media_file(str(path))
        except local_folder_intake.FolderIntakeError as exc:
            files.append({"path": str(path), "name": path.name, "usable": False, "reason": str(exc)})
            continue
        files.append({
            "path": str(resolved),
            "name": resolved.name,
            "folder": str(resolved.parent),
            "size_mb": file_size_mb(resolved.stat().st_size),
            "usable": True,
        })
    return {"ok": True, "cancelled": False, "files": files}


@router.post("/local/choose-folder")
def choose_folder_with_system_dialog(request: Request, payload: dict = Body(None)) -> dict:
    """Open this machine's own folder dialog, and say what is in what was chosen.

    The counterpart to ``/local/choose-media`` for the case the local edition is
    actually for: the recordings are already on the disk, in a folder, and asking
    someone to re-upload a morning's material one file at a time is asking them to
    copy gigabytes they already have.

    Answers with the listing as well as the path, in one call, because the count
    is what the next decision is about. A folder is however many Claude calls it
    has recordings in it, and that number has to be on screen before the button
    is pressed rather than discovered afterwards.
    """
    if not request_is_localhost(request):
        raise HTTPException(status_code=403, detail="只有本机能调起文件夹选择框。")
    reason = local_file_chooser.unavailable_reason()
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    body = payload or {}
    try:
        recent = recent_local_folders(limit=1)
        outcome = local_file_chooser.choose_media_folder(
            prompt=str(body.get("prompt") or "选择装着录像的文件夹"),
            start_in=recent[0] if recent else None,
        )
    except local_file_chooser.FileChooserError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if outcome.cancelled or not outcome.paths:
        return {"ok": True, "cancelled": True}
    chosen = outcome.paths[0]
    try:
        folder = local_folder_intake.resolve_folder(str(chosen))
        listing = local_folder_intake.list_media(folder)
    except local_folder_intake.FolderIntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "cancelled": False,
        "path": str(folder),
        **local_folder_intake.describe(listing),
    }


@router.post("/queue/process-local-files")
async def queue_process_local_files(request: Request, payload: dict = Body(...)) -> dict:
    """Process recordings that stay where they are, by absolute path.

    The entry the system file dialog feeds. Each recording is read in place and its
    cut version is written beside it; nothing is copied into the store first.
    """
    if not request_is_localhost(request):
        raise HTTPException(status_code=403, detail="只有本机能按路径处理文件。")
    client_id = _local_client_scope(request)
    raw_paths = payload.get("paths")
    if isinstance(raw_paths, str):
        raw_paths = [raw_paths]
    if not isinstance(raw_paths, list) or not raw_paths:
        raise HTTPException(status_code=400, detail="没有收到要处理的文件路径。")

    resolved: list[Path] = []
    for raw in raw_paths[: local_folder_intake.MAX_FILES_PER_FOLDER]:
        try:
            resolved.append(local_folder_intake.resolve_media_file(str(raw)))
        except local_folder_intake.FolderIntakeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    options, duration_limit = local_path_options(payload)
    queued: list[dict] = []
    for index, source_path in enumerate(resolved, start=1):
        queued.append(await queue_local_media_file(
            source_path,
            client_id=client_id,
            options=options,
            duration_limit_seconds=duration_limit,
            route="/queue/process-local-files",
            origin={"chosen_with": "system_dialog", "folder": str(source_path.parent)},
            index=index,
            total=len(resolved),
        ))
    return {"ok": True, "queued": queued, "count": len(queued)}


@router.post("/queue/process-folder")
async def queue_process_folder(request: Request, payload: dict = Body(...)) -> dict:
    """Process every recording in a folder on this machine, in place.

    Local edition only, and it must stay that way: a hosted server taking a
    filesystem path would read its own disk on a stranger's request.

    ``{"preview": true}`` is free: it validates the path and answers what would be
    queued, so the count and the file names can be shown before minutes of CPU and
    a Claude call per file are committed.
    """
    if not request_is_localhost(request):
        raise HTTPException(status_code=403, detail="只有本机能按路径处理文件夹。")
    client_id = _local_client_scope(request)
    try:
        folder = local_folder_intake.resolve_folder(payload.get("path"))
        listing = local_folder_intake.list_media(folder)
    except local_folder_intake.FolderIntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    described = local_folder_intake.describe(listing)
    if bool(payload.get("preview")):
        return {"ok": True, "preview": True, **described}

    options, duration_limit = local_path_options(payload)
    queued: list[dict] = []
    for index, source_path in enumerate(listing.files, start=1):
        queued.append(await queue_local_media_file(
            source_path,
            client_id=client_id,
            options=options,
            duration_limit_seconds=duration_limit,
            route="/queue/process-folder",
            origin={"chosen_with": "folder_path", "folder": str(folder)},
            index=index,
            total=len(listing.files),
        ))
    return {"ok": True, **described, "queued": queued, "count": len(queued)}


def _in_place_origin(job: dict) -> Optional[dict]:
    """Where this task's recording lives, when the task never copied it in.

    A task from a folder or the system file dialog is read where it sits, so
    FluentFlow's own store has nothing for it and the retry that looks there
    answers "source file not found" for a file that is sitting on the disk.
    The path was recorded when the task was queued; this reads it back.
    """
    metadata = job.get("metadata")
    origin = metadata.get("folder_intake") if isinstance(metadata, dict) else None
    if not isinstance(origin, dict) or not origin.get("original_path"):
        return None
    return origin


def _stored_queue_options(job: dict) -> dict:
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    stored = metadata.get("queue_options")
    return _queue_options_from_mapping(stored if isinstance(stored, dict) else metadata)


async def _retry_in_place(
    request: Request,
    *,
    task_id: str,
    client_id: Optional[str],
    job: dict,
    origin: dict,
) -> dict:
    """Re-run a task from the file it was read from, where that file still is.

    Goes back through ``queue_local_media_file`` rather than repeating its steps:
    the same preflight, the same serial chain, the same note afterwards. A retry
    that reimplemented them would be a second entry for anyone adding a guard to
    remember, and the last one to be remembered.
    """
    if not request_is_localhost(request):
        raise HTTPException(status_code=403, detail="只有本机能按路径处理文件。")
    original = Path(str(origin.get("original_path")))
    if not original.is_file():
        # Named, because the recording is the user's own file in their own folder
        # and only they can say where it went.
        raise HTTPException(
            status_code=404,
            detail=f"原文件已不在这个位置，无法重新处理：{original}",
        )
    options = _stored_queue_options(job)
    duration_limit = _effective_duration_limit(options.get("duration_limit_seconds"))
    queued = await queue_local_media_file(
        original,
        client_id=client_id,
        options=options,
        duration_limit_seconds=duration_limit,
        route="/jobs/{task_id}/retry",
        origin={
            **{key: value for key, value in origin.items() if key != "original_path"},
            "retry_source_task_id": task_id,
        },
    )
    if queued.get("status") == "rejected":
        raise HTTPException(status_code=400, detail=str(queued.get("reason") or "无法处理这个文件。"))
    retry_task_id = str(queued["task_id"])
    started_job = get_job(retry_task_id, client_id=client_id) or {
        "task_id": retry_task_id,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "source_filename": queued.get("filename"),
    }
    return {
        "ok": True,
        "source_task_id": task_id,
        "task_id": retry_task_id,
        "job": {**started_job, "task_snapshot": build_task_snapshot(started_job)},
    }


@router.post("/jobs/{task_id}/retry")
async def retry_job_from_stored_source(request: Request, task_id: str) -> dict:
    """Re-run a task from its stored source file, on the local hub."""
    client_id = _local_client_scope(request)
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Cancel the active job before retrying it")
    in_place = _in_place_origin(job)
    if in_place is not None:
        return await _retry_in_place(
            request, task_id=task_id, client_id=client_id, job=job, origin=in_place
        )
    source = find_source_file(task_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source file not found")

    filename = Path(job.get("source_filename") or source.name).name
    suffix = source.suffix or Path(filename).suffix.lower() or ".mp4"
    retry_task_id = claim_task_id(None, client_id=client_id)
    try:
        target_path = copy_source_file(retry_task_id, suffix, source)
    except Exception as exc:
        upsert_job(
            task_id=retry_task_id,
            status="failed",
            client_id=client_id,
            stage="import",
            progress=100,
            source_type=source_type_for_suffix(suffix),
            source_filename=filename,
            error_reason=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail="Could not prepare the stored source for retry.",
        ) from exc
    source_file_size_mb = path_size_mb(target_path)
    source_type = source_type_for_suffix(suffix)
    try:
        media_preflight = preflight_media_file(target_path)
    except MediaPreflightError as exc:
        raise _preflight_rejection(
            task_id=retry_task_id,
            source_path=target_path,
            source_type=source_type,
            source_filename=filename,
            source_file_size_mb=source_file_size_mb,
            client_id=client_id,
            error=exc,
            route="/jobs/{task_id}/retry",
            retry_source_task_id=task_id,
        ) from exc

    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    stored = metadata.get("queue_options")
    options = _queue_options_from_mapping(stored if isinstance(stored, dict) else metadata)
    raw_title_value = str(
        options.get("title") or metadata.get("raw_title") or Path(filename).stem
    ).strip()
    options.setdefault("title", raw_title_value)
    display_title_value = str(
        metadata.get("display_title") or display_title_for_user(raw_title_value, filename)
    ).strip()
    effective_duration_limit = _effective_duration_limit(
        options.get("duration_limit_seconds")
    )
    if effective_duration_limit is not None:
        options["duration_limit_seconds"] = str(effective_duration_limit)

    job_metadata = event_metadata(
        route="/jobs/{task_id}/retry",
        retry_source_task_id=task_id,
        raw_title=raw_title_value,
        display_title=display_title_value,
        queue_options=options,
        media_preflight={"status": "passed", **media_preflight.as_metadata()},
    )
    log_event(
        task_id=retry_task_id,
        event_name="task_retried",
        source_type=source_type,
        source_filename=filename,
        source_file_size_mb=source_file_size_mb,
        stage="import",
        success=True,
        metadata=job_metadata,
    )
    upsert_job(
        task_id=retry_task_id,
        status="running",
        client_id=client_id,
        stage="import",
        progress=0,
        source_type=source_type,
        source_filename=filename,
        source_file_size_mb=source_file_size_mb,
        metadata=job_metadata,
    )

    ctx = _local_media_job_context(
        task_id=retry_task_id,
        client_id=client_id,
        source_type=source_type,
        source_filename=filename,
        raw_title=raw_title_value,
        display_title=display_title_value,
        suffix=suffix,
        td=tempfile.mkdtemp(),
        in_path=target_path,
        content=b"",
        source_file_size_mb=source_file_size_mb,
        duration_preflight_sec=media_preflight.duration_seconds,
        options=options,
        duration_limit_seconds=effective_duration_limit,
    )
    await JOB_EVENTS.start(
        retry_task_id,
        functools.partial(
            run_worker_with_terminal_state,
            task_id=retry_task_id,
            client_id=client_id,
            hub=JOB_EVENTS,
            route="/jobs/{task_id}/retry",
            stage="processing",
            worker=functools.partial(execute_media_job, ctx),
        ),
    )

    started_job = get_job(retry_task_id, client_id=client_id) or {
        "task_id": retry_task_id,
        "status": "running",
        "stage": "import",
        "progress": 0,
        "source_type": source_type,
        "source_filename": filename,
        "source_file_size_mb": source_file_size_mb,
        "metadata": job_metadata,
    }
    return {
        "ok": True,
        "source_task_id": task_id,
        "task_id": retry_task_id,
        "job": {**started_job, "task_snapshot": build_task_snapshot(started_job)},
    }
