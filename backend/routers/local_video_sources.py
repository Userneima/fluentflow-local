"""Local-edition video-source intake: resolve a user-submitted share text or
video link on this machine, download it, and run the local pipeline.

Functionally local: no accounts, quota, or rate limits; the download worker
runs on ``local_job_runtime.JOB_EVENTS`` — the same hub the local read (SSE)
and cancel routes use — so resolving, downloading, processing, and cancelling
share one hub. The Douyin ``miuistore`` fallback is ON by default and can be switched off
per request or by a remembered choice (``options.allow_miuistore``); it runs only
when yt-dlp cannot get the video. The browser login (``cookies_from_browser``)
comes from the request, else the remembered ``video_cookies_browser``
preference, else ``YT_DLP_COOKIES_FROM_BROWSER``. YouTube caption
downloads reuse the local transcript summarize core in-process instead of the
hosted HTTP self-call.

Classification note: this router is ``local_ready``. It composes local source
resolution and local policies into the shared media worker without importing
hosted account, quota, cloud-STT, or desktop-sync modules.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Request

from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.job_store import list_jobs, progress_message_metadata, upsert_job
from backend.core.local_entry_guards import CancellationGate, claim_task_id
from backend.core.local_config import ALLOWED_COOKIE_BROWSERS, get_preference, normalize_cookie_browser
from backend.core.local_error_diagnostics import diagnose_error
from backend.core.local_job_runtime import JOB_EVENTS
from backend.core.local_limits_config import max_upload_mb
from backend.core.media_intake import (
    copy_source_file,
    file_size_mb,
    path_size_mb,
)
from backend.core.media_preflight import MediaPreflightError, preflight_media_file
from backend.core.queue_options import _queue_options_from_mapping
from backend.core.request_scope import local_client_scope
from backend.core.runtime_env import truthy
from backend.core.storage_paths import _video_source_storage_dir
from backend.core.title_display import display_title_for_user
from backend.core.video_source import (
    SavedVideoSource,
    VideoSourceProgress,
    check_browser_cookies,
    display_title_for_source_input,
    download_video_source,
    extra_urls_ignored,
    extract_first_url,
    link_too_large_message,
    normalize_source_link,
)
from backend.routers.local_note_regen import summarize_transcript_source
from backend.routers.local_processing import (
    _effective_duration_limit,
    _local_media_job_context,
    _start_behind_queue,
    run_pipeline_then_note,
    wait_for_queue_turn,
)

router = APIRouter()

_ROUTE = "/video-sources/jobs"
_ALLOWED_COOKIE_BROWSERS = ALLOWED_COOKIE_BROWSERS
EXTRA_URLS_WARNING = "分享文本里有多个链接，只处理了第一个支持的链接；其余链接请分别提交。"

# Link downloads skip the transcription queue (they compete with nothing it
# protects), so without a cap a batch of links all downloaded at once, each
# holding a thread of the default pool the rest of the server shares. They get
# a small pool of their own and at most this many run together.
_MAX_CONCURRENT_LINK_DOWNLOADS = 2
_DOWNLOAD_EXECUTOR = ThreadPoolExecutor(
    max_workers=_MAX_CONCURRENT_LINK_DOWNLOADS, thread_name_prefix="fluentflow-link-download"
)
_download_slots: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Semaphore]] = {}


def _download_slot() -> asyncio.Semaphore:
    """The link-download semaphore of the running event loop (tests run
    several loops; a semaphore must not be shared between them)."""
    loop = asyncio.get_running_loop()
    entry = _download_slots.get(id(loop))
    if entry is None or entry[0] is not loop:
        entry = (loop, asyncio.Semaphore(_MAX_CONCURRENT_LINK_DOWNLOADS))
        _download_slots[id(loop)] = entry
    return entry[1]


# A download reports every megabyte. Writing each report to the database and
# fanning it out to every subscriber is more work than the download itself on
# a fast connection, so reports go out at most this often — unless the
# percentage moved by this much, or the stage changed, or it is the last one.
_PROGRESS_PUBLISH_INTERVAL_SECONDS = 2.0
_PROGRESS_PUBLISH_PERCENT_STEP = 5.0


def _friendly_error(error: Any) -> str:
    return str(diagnose_error(error).get("detail") or "").strip() or str(error)


def _video_cookies_browser(options: dict[str, Any]) -> str | None:
    """The browser whose login yt-dlp reads: the request's choice, else the
    remembered preference. None leaves yt-dlp to the environment variable.
    Only a known browser name is ever passed to --cookies-from-browser."""
    requested = normalize_cookie_browser((options or {}).get("cookies_from_browser"))
    if requested:
        return requested
    remembered = normalize_cookie_browser(get_preference("video_cookies_browser"))
    return remembered or None


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
    return await asyncio.to_thread(check_browser_cookies, browser)


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
    previous: Any = None,
    done: asyncio.Event | None = None,
) -> None:
    """Download the source, then run the local pipeline — all on the local hub.

    Runs under ``run_worker_with_terminal_state``: this body handles the
    failures it can describe well (resolution, size, preflight); anything that
    escapes — including cancellation — still ends in a terminal state.

    ``previous`` and ``done`` are this job's link in the serial chain. The
    download goes ahead at once — it is network, and competes with nothing the
    chain protects — and the pipeline waits its turn behind whatever is already
    transcribing. ``done`` is set however this ends, so the job behind it is
    never left waiting.
    """
    link_done = done if done is not None else asyncio.Event()
    try:
        await _download_then_process(
            task_id=task_id,
            input_text=input_text,
            title=title,
            options=options,
            allow_miuistore=allow_miuistore,
            client_id=client_id,
            gate=gate,
            route=route,
            previous=previous,
            done=link_done,
        )
    finally:
        link_done.set()


def _progress_throttle() -> Any:
    """Decide which download reports are worth persisting and publishing."""
    last = {"at": 0.0, "percent": None, "stage": None}

    def should_publish(progress: VideoSourceProgress) -> bool:
        percent = progress.percent
        finished = (
            (percent is not None and percent >= 100)
            or (progress.total_bytes and progress.loaded_bytes == progress.total_bytes)
        )
        now = time.monotonic()
        due = (
            finished
            or progress.stage != last["stage"]
            or now - last["at"] >= _PROGRESS_PUBLISH_INTERVAL_SECONDS
            or (
                percent is not None
                and (last["percent"] is None or percent - last["percent"] >= _PROGRESS_PUBLISH_PERCENT_STEP)
            )
        )
        if due:
            last.update(at=now, percent=percent, stage=progress.stage)
        return due

    return should_publish


async def _download_then_process(
    *,
    task_id: str,
    input_text: str,
    title: str | None,
    options: dict[str, str],
    allow_miuistore: bool,
    client_id: Optional[str],
    gate: CancellationGate,
    route: str,
    previous: Any,
    done: asyncio.Event,
) -> None:
    loop = asyncio.get_running_loop()
    should_publish = _progress_throttle()

    def on_progress(progress: VideoSourceProgress) -> None:
        # Runs in the executor thread: persist, then publish thread-safely.
        # After cancellation the download thread cannot be killed and keeps
        # calling this; the gate keeps it from resurrecting the job state.
        if gate.closed:
            return
        if not should_publish(progress):
            return
        progress_value = _video_source_progress_value(progress)
        upsert_job(
            task_id=task_id,
            status="running",
            client_id=client_id,
            stage=progress.stage,
            progress=progress_value,
            # Download text is progress, not the note's state: summary_status
            # carries only note statuses, and readers get this line as the
            # job's top-level progress_message while this stage lasts.
            metadata=event_metadata(
                route=route,
                queue_options=options,
                **progress_message_metadata(progress.message, progress.stage),
                video_source_progress={
                    "message": progress.message,
                    "percent": progress.percent,
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
                    "percent": progress.percent,
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

    cookies_browser = _video_cookies_browser(options)
    duration_limit = _effective_duration_limit(options.get("duration_limit_seconds"))
    slot = _download_slot()
    try:
        if slot.locked():
            on_progress(VideoSourceProgress(
                stage="resolving", message="等待前面的链接下载完成", percent=2,
            ))
        async with slot:
            saved = await loop.run_in_executor(
                _DOWNLOAD_EXECUTOR,
                lambda: download_video_source(
                    input_text,
                    title=title,
                    video_dir=_video_source_storage_dir(),
                    on_progress=on_progress,
                    cookies_from_browser=cookies_browser,
                    # The Douyin third-party fallback is on unless this request
                    # or the remembered setting switched it off.
                    allow_miuistore=allow_miuistore,
                    duration_limit_seconds=duration_limit,
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
            link_too_large_message(saved.size_bytes, int(limit_mb * 1024 * 1024)),
        )
        return

    source_path = Path(saved.file_path)
    suffix = source_path.suffix or Path(saved.filename).suffix or ".mp4"
    target_path = await asyncio.to_thread(copy_source_file, task_id, suffix, source_path)
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
        media_preflight = await asyncio.to_thread(preflight_media_file, target_path)
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
    ctx.event_route = route
    # The expensive part waits its turn; the download above did not have to.
    await wait_for_queue_turn(previous, task_id, client_id)
    await run_pipeline_then_note(done, ctx)


_ACTIVE_STATUSES = frozenset({"queued", "running"})
DUPLICATE_LINK_MESSAGE = "这个链接已经处理过了。要再处理一次，请确认后重新提交。"


def _same_link_task(link: str | None, client_id: Optional[str]) -> dict[str, Any] | None:
    """The task this link already has: an active one first, else a completed one.

    Failed and cancelled tasks never count, so a link that went wrong can always
    be submitted again. Compared on ``normalize_source_link``, so the same video
    shared twice with different tracking parameters is still the same link.
    """
    if not link:
        return None
    completed: dict[str, Any] | None = None
    for job in list_jobs(limit=None, client_id=client_id, include_result=False):
        metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
        stored = str(metadata.get("video_source_url") or "").strip()
        if not stored or normalize_source_link(stored) != link:
            continue
        status = str(job.get("status") or "")
        if status in _ACTIVE_STATUSES:
            return job
        if status == "completed" and completed is None:
            completed = job
    return completed


def _refuse_or_reuse_duplicate(
    input_text: str, client_id: Optional[str], *, allow_duplicate: bool
) -> dict[str, Any] | None:
    """Answer a link FluentFlow already has, before anything is downloaded.

    Submitting one link twice used to download, transcribe and write the note
    twice and leave two identical records (measured 2026-10-08). A link still
    queued or running answers with that task (``duplicate_of_active``) — the
    caller wanted it processed and it is being processed. A link already done
    is refused with 409 ``duplicate_link`` unless the caller confirms with
    ``allow_duplicate``.
    """
    existing = _same_link_task(normalize_source_link(input_text), client_id)
    if existing is None:
        return None
    status = str(existing.get("status") or "")
    if status in _ACTIVE_STATUSES:
        return {**existing, "duplicate_of_active": True}
    if allow_duplicate:
        return None
    raise HTTPException(
        status_code=409,
        detail={
            "code": "duplicate_link",
            "existing_task_id": existing.get("task_id"),
            "existing_status": status,
            "message": DUPLICATE_LINK_MESSAGE,
        },
    )


def allow_duplicate_requested(payload: dict[str, Any] | None) -> bool:
    """``allow_duplicate`` at the top of the request or inside its options."""
    body = payload or {}
    options = body.get("options") if isinstance(body.get("options"), dict) else {}
    value = body.get("allow_duplicate", options.get("allow_duplicate"))
    if value is None:
        return False
    return value is True or truthy(str(value))


async def submit_video_source_job(
    *,
    input_text: str,
    title: str,
    raw_options: dict[str, Any],
    client_id: Optional[str],
    route: str = _ROUTE,
    extra_metadata: dict[str, Any] | None = None,
    allow_duplicate: bool = False,
) -> dict[str, Any]:
    """Validate, claim, persist, and start a video-source download job on the
    local hub. Shared by the video-source route and the local Agent API.

    A link already queued or running returns that task with
    ``duplicate_of_active: true`` instead of starting a second one; a link
    already completed is refused (409 ``duplicate_link``) unless
    ``allow_duplicate`` is true.
    """
    if not input_text:
        raise HTTPException(status_code=400, detail="缺少视频分享文本或视频链接")
    if len(input_text) > 4000:
        raise HTTPException(status_code=400, detail="分享文本过长")
    duplicate = _refuse_or_reuse_duplicate(input_text, client_id, allow_duplicate=allow_duplicate)
    if duplicate is not None:
        return duplicate

    options = _queue_options_from_mapping(raw_options)
    # Per-request choice wins; otherwise the remembered settings choice
    # applies. Default is ON: yt-dlp needs a fresh Douyin login and fails
    # without one, so with the fallback off a Douyin link had no working route
    # at all.
    if "allow_miuistore" in (raw_options or {}):
        allow_miuistore = truthy(raw_options.get("allow_miuistore"))
    else:
        remembered = get_preference("allow_miuistore")
        allow_miuistore = True if remembered is None else bool(remembered)
    task_id_value = claim_task_id(None, client_id=client_id)
    raw_title = title or display_title_for_source_input(input_text, input_text[:80])
    display_name = display_title_for_user(raw_title, raw_title)
    # The link itself, whole, so a retry fetches exactly this link; the preview
    # below is cut at 200 characters and can lose a link at the end of long
    # share text.
    source_url = extract_first_url(input_text)
    ignored_extra = extra_urls_ignored(input_text)
    link_metadata: dict[str, Any] = {}
    if source_url:
        link_metadata["video_source_url"] = source_url
    if title:
        link_metadata["video_source_title"] = title
    if ignored_extra:
        link_metadata["video_source_extra_urls_ignored"] = True
    metadata = event_metadata(
        route=route,
        queue_options=options,
        raw_title=raw_title,
        display_title=display_name,
        video_source_input_preview=input_text[:200],
        video_source_allow_miuistore=allow_miuistore,
        **link_metadata,
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
    await _start_behind_queue(
        task_id=task_id_value,
        client_id=client_id,
        route=route,
        stage="video_source",
        gate=gate,
        chained_worker=lambda previous, done: _run_local_video_source_job(
            task_id=task_id_value,
            input_text=input_text,
            title=title or None,
            options=options,
            allow_miuistore=allow_miuistore,
            client_id=client_id,
            gate=gate,
            route=route,
            previous=previous,
            done=done,
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
        "extra_urls_ignored": ignored_extra,
        **({"warning": EXTRA_URLS_WARNING} if ignored_extra else {}),
    }


@router.post("/video-sources/jobs")
async def create_video_source_job(
    request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    job = await submit_video_source_job(
        input_text=str(payload.get("input") or "").strip(),
        title=str(payload.get("title") or "").strip(),
        raw_options=payload.get("options") if isinstance(payload.get("options"), dict) else {},
        client_id=local_client_scope(request),
        allow_duplicate=allow_duplicate_requested(payload),
    )
    response: dict[str, Any] = {
        "ok": True,
        "job": job,
        "extra_urls_ignored": job.get("extra_urls_ignored", False),
        "duplicate_of_active": bool(job.get("duplicate_of_active")),
    }
    if job.get("warning"):
        response["warning"] = job["warning"]
    return response


@router.get("/video-sources/jobs")
def list_video_source_jobs(request: Request, limit: int = 50) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 50), 200))
    jobs = [
        job
        for job in list_jobs(limit=200, client_id=local_client_scope(request))
        if (job.get("metadata") or {}).get("route") == _ROUTE
    ][:safe_limit]
    return {"jobs": jobs}
