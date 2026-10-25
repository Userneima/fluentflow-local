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
import logging
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.job_store import delete_jobs, get_job, recent_local_folders, upsert_job
from backend.core.local_entry_guards import (
    claim_task_id,
)
from backend.core import (
    local_file_chooser,
    local_folder_intake,
    open_folder,
)
from backend.core.local_job_runtime import JOB_EVENTS
from backend.core.local_limits_config import (
    max_queue_files,
    max_upload_mb,
)
from backend.core.local_request_scope import request_is_localhost
from backend.core.media_intake import (
    ALLOWED_SUFFIXES,
    MediaIntakeSizeError,
    file_size_mb,
    persist_source_stream,
    source_type_for_suffix,
)
from backend.core.media_preflight import MediaPreflightError, preflight_media_file
from backend.core.request_scope import local_client_scope
from backend.core.storage_cleanup import remove_tree
from backend.core.title_display import display_title_for_user
from backend.core.task_queue import _with_queue_place
from backend.core.task_submission import (
    _collect_options,
    _effective_duration_limit,
    _local_media_job_context,
    _preflight_rejection,
    local_path_options,
    queue_local_media_file,
    retry_for_request,
    start_media_job_behind_queue,
)

logger = logging.getLogger(__name__)

router = APIRouter()

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
    voice_enhance: Optional[str] = Form(None),
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

    client_id = local_client_scope(request)
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
        media_preflight = await asyncio.to_thread(preflight_media_file, in_path)
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
        voice_enhance=voice_enhance,
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

    # Same chain and same tail as the queued entry, so the two upload paths
    # cannot end in different products — one with a note, one without — and a
    # single upload cannot run beside a batch that is already transcribing.
    await start_media_job_behind_queue(ctx, route="/process")

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
    voice_enhance: Optional[str] = Form(None),
    system_prompt: Optional[str] = Form(None),
    prompt_preset: Optional[str] = Form(None),
    prompt_preset_label: Optional[str] = Form(None),
    duration_limit_seconds: Optional[float] = Form(None),
) -> dict:
    """Queue several media files for serial local processing (one live worker
    at a time), all on the local event hub."""
    client_id = local_client_scope(request)
    limit_files = max_queue_files()
    if limit_files > 0 and len(files) > limit_files:
        raise HTTPException(
            status_code=413,
            detail=f"一次最多提交 {limit_files} 个文件，这次选了 {len(files)} 个。请分成几批提交。",
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
        voice_enhance=voice_enhance,
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
            preflight = await asyncio.to_thread(preflight_media_file, source_path)
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
        await start_media_job_behind_queue(ctx, route="/queue/process")
        queued.append({
            "task_id": task_id_value,
            "filename": filename,
            "source_type": source_type,
            "source_file_size_mb": source_file_size_mb,
            "status": "queued",
            "queue_position": index,
            "queue_total": total,
        })
    return {"ok": True, "queued": [_with_queue_place(item) for item in queued], "count": len(queued)}

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


@router.post("/jobs/{task_id}/output-folder/open")
def open_task_output_folder(task_id: str, request: Request) -> dict:
    """Show the task's output folder in Explorer or Finder.

    It opens a window on somebody's screen, so only for a request from this
    machine; and only the folder the task recorded, never a path from the request.
    """
    if not request_is_localhost(request):
        raise HTTPException(status_code=403, detail="只有本机能打开文件夹。")
    job = get_job(task_id, client_id=local_client_scope(request))
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    folder = open_folder.task_output_folder(job.get("result"))
    if folder is None:
        raise HTTPException(status_code=409, detail="这个任务没有放进你文件夹里的产出。")
    try:
        open_folder.open_folder(folder)
    except open_folder.OpenFolderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "path": str(folder)}


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
    client_id = local_client_scope(request)
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

    options, duration_limit = local_path_options(
        payload, file_count=len(resolved), speaker_diarization_default_on=False
    )
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
    # Paths past the limit are not queued; said here so a caller that sent more
    # (an agent, an older page) learns which ones, instead of losing them quietly.
    skipped = [str(raw) for raw in raw_paths[local_folder_intake.MAX_FILES_PER_FOLDER:]]
    return {
        "ok": True,
        "queued": [_with_queue_place(item) for item in queued],
        "count": len(queued),
        "skipped_paths": skipped,
        "limit": local_folder_intake.MAX_FILES_PER_FOLDER,
    }


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
    client_id = local_client_scope(request)
    try:
        folder = local_folder_intake.resolve_folder(payload.get("path"))
        listing = local_folder_intake.list_media(folder)
    except local_folder_intake.FolderIntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    described = local_folder_intake.describe(listing)
    if bool(payload.get("preview")):
        return {"ok": True, "preview": True, **described}

    options, duration_limit = local_path_options(
        payload, file_count=len(listing.files), speaker_diarization_default_on=False
    )
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
    return {"ok": True, **described, "queued": [_with_queue_place(item) for item in queued], "count": len(queued)}

@router.post("/jobs/{task_id}/retry")
async def retry_job_from_stored_source(request: Request, task_id: str) -> dict:
    """Re-run a task from its stored source file, on the local hub."""
    return await retry_for_request(request, task_id)
