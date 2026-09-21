"""Local-edition video-source intake: resolve a user-submitted share text or
video link on this machine, download it, and run the local pipeline.

Functionally local: no accounts, quota, or rate limits; the download worker
runs on ``local_job_runtime.JOB_EVENTS`` — the same hub the local read (SSE)
and cancel routes use — so resolving, downloading, processing, and cancelling
share one hub. The Douyin ``miuistore`` fallback is ON by default and can be switched off
per request or by a remembered choice (``options.allow_miuistore``). YouTube caption
downloads reuse the local transcript summarize core in-process instead of the
hosted HTTP self-call.

Classification note: this router is ``local_ready``. It composes local source
resolution and local policies into the shared media worker without importing
hosted account, quota, cloud-STT, or desktop-sync modules.
"""

from __future__ import annotations

import asyncio
import functools
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Request

from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.job_store import list_jobs, upsert_job
from backend.core.local_entry_guards import (
    CancellationGate,
    claim_task_id,
    run_worker_with_terminal_state,
)
from backend.core.local_config import get_preference
from backend.core.local_error_diagnostics import diagnose_error
from backend.core.local_job_runtime import JOB_EVENTS
from backend.core.local_limits_config import max_upload_mb
from backend.core.local_request_scope import request_client_id
from backend.core.media_intake import (
    copy_source_file,
    file_size_mb,
    path_size_mb,
)
from backend.core.media_job import execute_media_job
from backend.core.media_preflight import MediaPreflightError, preflight_media_file
from backend.core.queue_options import _queue_options_from_mapping
from backend.core.storage_paths import _video_source_storage_dir
from backend.core.title_display import display_title_for_user
from backend.core.video_source import (
    SavedVideoSource,
    VideoSourceProgress,
    check_browser_cookies,
    display_title_for_source_input,
    download_video_source,
)
from backend.routers.local_note_regen import summarize_transcript_source
from backend.routers.local_processing import (
    _effective_duration_limit,
    _local_media_job_context,
)

router = APIRouter()

_ROUTE = "/video-sources/jobs"
_ALLOWED_COOKIE_BROWSERS = {
    "chrome", "edge", "firefox", "safari", "brave", "chromium", "opera", "vivaldi",
}


def _local_client_scope(request: Request) -> Optional[str]:
    return request_client_id(request) or "anonymous"


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _friendly_error(error: Any) -> str:
    return str(diagnose_error(error).get("detail") or "").strip() or str(error)


def _video_cookies_browser(options: dict[str, Any]) -> str | None:
    """Only a known browser name is passed to yt-dlp --cookies-from-browser."""
    value = str((options or {}).get("cookies_from_browser") or "").strip().lower()
    return value if value in _ALLOWED_COOKIE_BROWSERS else None


def _video_source_progress_value(progress: VideoSourceProgress) -> float:
    if progress.stage == "resolving":
        return float(progress.percent or 8)
    if progress.stage == "downloading":
        pct = progress.percent if progress.percent is not None else 0
        return 10 + max(0, min(100, float(pct))) * 0.45
    if progress.stage == "saving":
        return 58
    return 0


def _public_video_source_metadata(saved: SavedVideoSource) -> dict[str, Any]:
    return {
        "provider": saved.provider,
        "media_type": saved.media_type or "video",
        "source_url": saved.source_url,
        "duration_seconds": saved.duration_seconds,
        "estimated_size_bytes": saved.estimated_size_bytes,
        "video_id": saved.video_id,
        "raw_title": saved.raw_title or saved.title,
        "display_title": saved.display_title or display_title_for_user(saved.title, saved.filename),
        "title": saved.title,
        "filename": saved.filename,
        "file_path": saved.file_path,
        "file_url": saved.file_url,
        "metadata_path": saved.metadata_path,
        "size_bytes": saved.size_bytes,
        "downloaded_at": saved.downloaded_at,
        "asset_strategy": saved.asset_strategy,
        "resolution_trace": saved.resolution_trace,
    }


@router.post("/video-sources/cookie-check")
async def video_source_cookie_check(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    browser = str(payload.get("browser") or "").strip().lower()
    if browser not in _ALLOWED_COOKIE_BROWSERS:
        raise HTTPException(status_code=400, detail="不支持的浏览器")
    return check_browser_cookies(browser)


async def _run_local_video_source_job(
    *,
    task_id: str,
    input_text: str,
    title: str | None,
    options: dict[str, str],
    allow_miuistore: bool,
    client_id: Optional[str],
    gate: CancellationGate,
    route: str = _ROUTE,
) -> None:
    """Download the source, then run the local pipeline — all on the local hub.

    Runs under ``run_worker_with_terminal_state``: this body handles the
    failures it can describe well (resolution, size, preflight); anything that
    escapes — including cancellation — still ends in a terminal state.
    """
    loop = asyncio.get_running_loop()

    def on_progress(progress: VideoSourceProgress) -> None:
        # Runs in the executor thread: persist, then publish thread-safely.
        # After cancellation the download thread cannot be killed and keeps
        # calling this; the gate keeps it from resurrecting the job state.
        if gate.closed:
            return
        progress_value = _video_source_progress_value(progress)
        upsert_job(
            task_id=task_id,
            status="running",
            client_id=client_id,
            stage=progress.stage,
            progress=progress_value,
            summary_status=progress.message,
            metadata=event_metadata(
                route=route,
                queue_options=options,
                video_source_progress={
                    "message": progress.message,
                    "loaded_bytes": progress.loaded_bytes,
                    "total_bytes": progress.total_bytes,
                },
            ),
        )
        asyncio.run_coroutine_threadsafe(
            JOB_EVENTS.publish(
                task_id,
                {
                    "stage": progress.stage,
                    "progress": progress_value,
                    "message": progress.message,
                    "loaded_bytes": progress.loaded_bytes,
                    "total_bytes": progress.total_bytes,
                },
            ),
            loop,
        )

    async def fail(stage: str, error: Exception | str, **extra_metadata: Any) -> None:
        friendly_error = _friendly_error(error)
        log_event(
            task_id=task_id,
            event_name="task_failed",
            source_type="video",
            source_filename=title or (input_text[:80] if input_text else "video link"),
            stage=stage,
            success=False,
            error_reason=friendly_error,
            metadata=event_metadata(
                route=route,
                queue_options=options,
                raw_error=str(error),
                **extra_metadata,
            ),
        )
        upsert_job(
            task_id=task_id,
            status="failed",
            client_id=client_id,
            stage=stage,
            progress=100,
            error_reason=friendly_error,
        )
        await JOB_EVENTS.publish(
            task_id, {"stage": "error", "progress": 100, "error": friendly_error}
        )

    try:
        saved = await loop.run_in_executor(
            None,
            lambda: download_video_source(
                input_text,
                title=title,
                video_dir=_video_source_storage_dir(),
                on_progress=on_progress,
                cookies_from_browser=_video_cookies_browser(options),
                # Local edition: the Douyin miuistore fallback stays off unless
                # this request explicitly consented (design contract).
                allow_miuistore=allow_miuistore,
                cancellation_event=gate.cancellation_event,
            ),
        )
    except Exception as exc:
        resolution_trace = getattr(exc, "resolution_trace", None)
        await fail(
            "video_source",
            exc,
            video_source={"resolution_trace": resolution_trace} if resolution_trace else None,
        )
        return

    saved_size_mb = file_size_mb(saved.size_bytes)
    limit_mb = max_upload_mb()
    if saved_size_mb is not None and saved_size_mb > limit_mb:
        await fail(
            "video_source",
            f"Downloaded video is too large: {saved_size_mb} MB. Limit is {limit_mb:g} MB.",
        )
        return

    source_path = Path(saved.file_path)
    suffix = source_path.suffix or Path(saved.filename).suffix or ".mp4"
    target_path = copy_source_file(task_id, suffix, source_path)
    source_file_size_mb = path_size_mb(target_path)
    media_type = saved.media_type or "video"
    source_type = "transcript_file" if media_type == "transcript" else "video"
    raw_title = saved.raw_title or saved.title
    display_title = saved.display_title or display_title_for_user(saved.title, saved.filename)
    metadata = event_metadata(
        route=route,
        queue_options=options,
        source_path=str(target_path),
        raw_title=raw_title,
        display_title=display_title,
        video_source=_public_video_source_metadata(saved),
        asset_strategy=saved.asset_strategy,
    )
    log_event(
        task_id=task_id,
        event_name="video_source_downloaded",
        source_type=source_type,
        source_filename=saved.filename,
        source_file_size_mb=source_file_size_mb,
        stage="queued",
        success=True,
        metadata=metadata,
    )
    upsert_job(
        task_id=task_id,
        status="queued",
        client_id=client_id,
        stage="queued",
        progress=0,
        source_type=source_type,
        source_filename=saved.filename,
        source_file_size_mb=source_file_size_mb,
        metadata=metadata,
    )
    await JOB_EVENTS.publish(task_id, {"stage": "queued", "progress": 0})

    if media_type == "transcript":
        try:
            raw = await asyncio.to_thread(target_path.read_bytes)
            result = await summarize_transcript_source(
                raw=raw,
                filename=saved.filename,
                client_id=client_id,
                task_id=task_id,
                ai_provider=options.get("ai_provider"),
                ai_model=options.get("ai_model"),
                note_mode=options.get("note_mode"),
                skip_summary=options.get("skip_summary"),
                system_prompt=options.get("system_prompt"),
                prompt_preset=options.get("prompt_preset"),
                prompt_preset_label=options.get("prompt_preset_label"),
                route=route,
            )
        except HTTPException as exc:
            await fail("transcript_parse", str(exc.detail))
            return
        except Exception as exc:
            await fail("summary", exc)
            return
        await JOB_EVENTS.publish(task_id, {"stage": "done", "progress": 100, "result": result})
        return

    try:
        media_preflight = preflight_media_file(target_path)
    except MediaPreflightError as exc:
        await fail(
            "import",
            exc,
            media_preflight={"status": "rejected", "code": exc.code, **exc.metadata},
        )
        return

    effective_duration_limit = _effective_duration_limit(options.get("duration_limit_seconds"))
    if effective_duration_limit is not None:
        options["duration_limit_seconds"] = str(effective_duration_limit)
    options.setdefault("title", raw_title)
    ctx = _local_media_job_context(
        task_id=task_id,
        client_id=client_id,
        source_type=source_type,
        source_filename=saved.filename,
        raw_title=raw_title,
        display_title=display_title,
        suffix=suffix,
        td=tempfile.mkdtemp(),
        in_path=target_path,
        content=b"",
        source_file_size_mb=source_file_size_mb,
        duration_preflight_sec=media_preflight.duration_seconds,
        options=options,
        duration_limit_seconds=effective_duration_limit,
    )
    await execute_media_job(ctx)


async def submit_video_source_job(
    *,
    input_text: str,
    title: str,
    raw_options: dict[str, Any],
    client_id: Optional[str],
    route: str = _ROUTE,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate, claim, persist, and start a video-source download job on the
    local hub. Shared by the video-source route and the local Agent API."""
    if not input_text:
        raise HTTPException(status_code=400, detail="缺少视频分享文本或视频链接")
    if len(input_text) > 4000:
        raise HTTPException(status_code=400, detail="分享文本过长")

    options = _queue_options_from_mapping(raw_options)
    # Per-request choice wins; otherwise the remembered settings choice
    # applies. Default is ON: yt-dlp needs a fresh Douyin login and fails
    # without one, so with the fallback off a Douyin link had no working route
    # at all.
    if "allow_miuistore" in (raw_options or {}):
        allow_miuistore = _truthy(raw_options.get("allow_miuistore"))
    else:
        remembered = get_preference("allow_miuistore")
        allow_miuistore = True if remembered is None else bool(remembered)
    task_id_value = claim_task_id(None, client_id=client_id)
    raw_title = title or display_title_for_source_input(input_text, input_text[:80])
    display_name = display_title_for_user(raw_title, raw_title)
    metadata = event_metadata(
        route=route,
        queue_options=options,
        raw_title=raw_title,
        display_title=display_name,
        video_source_input_preview=input_text[:200],
        video_source_allow_miuistore=allow_miuistore,
        **(extra_metadata or {}),
    )
    log_event(
        task_id=task_id_value,
        event_name="video_source_submitted",
        source_type="video_link",
        source_filename=display_name,
        stage="queued",
        success=True,
        metadata=metadata,
    )
    upsert_job(
        task_id=task_id_value,
        status="queued",
        client_id=client_id,
        stage="queued",
        progress=0,
        source_type="video_link",
        source_filename=display_name,
        metadata=metadata,
    )
    gate = CancellationGate()
    await JOB_EVENTS.start(
        task_id_value,
        functools.partial(
            run_worker_with_terminal_state,
            task_id=task_id_value,
            client_id=client_id,
            hub=JOB_EVENTS,
            route=route,
            stage="video_source",
            gate=gate,
            worker=functools.partial(
                _run_local_video_source_job,
                task_id=task_id_value,
                input_text=input_text,
                title=title or None,
                options=options,
                allow_miuistore=allow_miuistore,
                client_id=client_id,
                gate=gate,
                route=route,
            ),
        ),
    )
    return {
        "task_id": task_id_value,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "source_type": "video_link",
        "source_filename": display_name,
        "metadata": metadata,
    }


@router.post("/video-sources/jobs")
async def create_video_source_job(
    request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    job = await submit_video_source_job(
        input_text=str(payload.get("input") or "").strip(),
        title=str(payload.get("title") or "").strip(),
        raw_options=payload.get("options") if isinstance(payload.get("options"), dict) else {},
        client_id=_local_client_scope(request),
    )
    return {"ok": True, "job": job}


@router.get("/video-sources/jobs")
def list_video_source_jobs(request: Request, limit: int = 50) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 50), 200))
    jobs = [
        job
        for job in list_jobs(limit=200, client_id=_local_client_scope(request))
        if (job.get("metadata") or {}).get("route") == _ROUTE
    ][:safe_limit]
    return {"jobs": jobs}
