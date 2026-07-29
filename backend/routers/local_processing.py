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
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.job_store import delete_jobs, get_job, list_jobs_for_retention, update_job_result, upsert_job
from backend.core.history_retention import enforce_history_retention
from backend.core.local_retention_config import artifact_retention_days
from backend.core.local_entry_guards import (
    claim_task_id,
    friendly_error,
    local_ai_kwargs,
    run_worker_with_terminal_state,
)
from backend.core.local_config import resolve_secret
from backend.core.local_job_runtime import JOB_EVENTS
from backend.core.local_limits_config import (
    max_media_duration_seconds,
    max_queue_files,
    max_upload_mb,
)
from backend.core.local_request_scope import request_client_id
from backend.core.local_retention_config import source_retention_days
from backend.core.local_stt_policy import LOCAL_STT_PROVIDER
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
_QUEUE_TAIL: dict[str, Optional[asyncio.Event]] = {"event": None}


async def _run_serially(
    previous: Optional[asyncio.Event],
    done: asyncio.Event,
    ctx: MediaJobContext,
) -> None:
    try:
        if previous is not None:
            try:
                await previous.wait()
            except asyncio.CancelledError:
                # This runner may be cancelled while it is only a queue
                # placeholder. Keep its successor behind the same predecessor
                # barrier; releasing ``done`` immediately would let the next
                # worker overlap the still-running previous worker.
                await previous.wait()
                raise
        await execute_media_job(ctx)
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
        model_size=(options.get("stt_model") or "").strip() or "medium",
        speed_profile=(options.get("stt_speed") or "").strip() or "balanced",
        language="auto",
        stt_provider_value=LOCAL_STT_PROVIDER,
        diarization_requested=_truthy(options.get("speaker_diarization")),
        do_lark=_truthy(options.get("export_to_lark")),
        summary_disabled=_truthy(options.get("skip_summary")),
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
            worker=functools.partial(execute_media_job, ctx),
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
        previous = _QUEUE_TAIL["event"]
        done = asyncio.Event()
        _QUEUE_TAIL["event"] = done
        await JOB_EVENTS.start(
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


@router.post("/jobs/{task_id}/retry")
async def retry_job_from_stored_source(request: Request, task_id: str) -> dict:
    """Re-run a task from its stored source file, on the local hub."""
    client_id = _local_client_scope(request)
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Cancel the active job before retrying it")
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
