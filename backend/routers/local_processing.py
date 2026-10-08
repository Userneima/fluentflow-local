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
import threading
import functools
from concurrent.futures import ThreadPoolExecutor
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
from backend.core.local_entry_guards import (
    claim_task_id,
    friendly_error,
    local_ai_kwargs,
    run_worker_with_terminal_state,
)
from backend.core import (
    claude_code_note,
    local_file_chooser,
    local_folder_intake,
    local_intake_flow,
    visual_note_channel,
    visual_note_job,
)
from backend.core import speaker_diarization as speaker_diarization_core
from backend.core.local_config import resolve_secret
from backend.core.local_job_runtime import JOB_EVENTS
from backend.core.local_limits_config import (
    max_media_duration_seconds,
    max_queue_files,
    max_upload_mb,
)
from backend.core.local_request_scope import request_is_localhost
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
from backend.core.lark_cli_exporter import export_markdown_via_lark_cli, lark_cli_ready
from backend.core.lark_exporter import export_markdown_to_lark
from backend.core.media_job import MediaJobContext, execute_media_job
from backend.core.media_preflight import MediaPreflightError, preflight_media_file
from backend.core.result_retention import finalize_completed_result_storage
from backend.core.queue_options import _queue_options_from_mapping
from backend.core.request_scope import local_client_scope
from backend.core.runtime_env import truthy
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
    if route in {"local_cli", "lark_cli"} or truthy(values.get("lark_via_cli")):
        target = "lark_cli"
    elif route == "auto":
        # New users' default: their own identity when lark-cli is signed in.
        target = "lark_cli" if lark_cli_ready() else "lark_openapi"
    else:
        target = "lark_openapi"
    title = resolve_lark_doc_title(str(values["summary_markdown"]), filename_stem=str(values["filename_stem"]), form_title=str(values.get("form_title") or ""))
    if target == "lark_cli":
        response = export_markdown_via_lark_cli(title, str(values["summary_markdown"]), task_id=str(values["task_id"]), artifact_root=_artifact_storage_dir())
    else:
        kwargs = {}
        if app_id := resolve_secret(values.get("lark_app_id"), "lark_app_id"): kwargs["app_id"] = app_id
        if app_secret := resolve_secret(values.get("lark_app_secret"), "lark_app_secret"): kwargs["app_secret"] = app_secret
        if values.get("folder_token"): kwargs["folder_token"] = values["folder_token"]
        response = export_markdown_to_lark(title, str(values["summary_markdown"]), task_id=str(values["task_id"]), artifact_root=_artifact_storage_dir(), **kwargs)
    return {"doc_title": title, "export_target": target, "response": response}

def _enforce_local_history_retention(client_id: str | None) -> dict:
    return enforce_history_retention(client_id, source_days=source_retention_days(), list_jobs=list_jobs_for_retention, update_result=update_job_result, load_job=get_job)


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
# Each link carries the predecessor's task id as well as its event, because the
# event alone cannot answer the question that matters: an unset event means
# "still working" and "gone without releasing" equally well. Waiting on it
# forever is what left every later upload sitting at "queued 0%" on an idle
# machine, with restarting the service as the only exit. The id makes the
# difference checkable — the hub knows whether that task is still running.
#
# Every entry that runs the pipeline goes through ``_start_behind_queue``: the
# single upload, the batch, the folder, the retry, and the video link. One of
# them starting its worker directly is how two transcriptions ended up running
# side by side on a machine measured to collapse under exactly that.

# How many jobs may be in flight at once. The chain above is what makes the
# queue serial: each job waits on its predecessor's event. Waiting on the job
# two places back instead lets two run together, which is worth doing because
# the two expensive phases do not compete for the same resource — the render is
# four ffmpeg processes on as many cores as it is given, and the transcription
# that follows it is one model on one. Measured on this archive with the queue
# strictly serial: a three-hour lecture spent about half its wall clock in each
# phase with the other half of the machine idle.
#
# Two rather than more because the render is also the memory peak, and this is
# a 16GB machine: a single 1440p job has been observed pushing the system into
# swap on its own.
# Set to 2 and measured on this 16GB machine: both jobs did progress, but system
# load went from ~50 to ~102 within fifteen minutes with swap free down to a few
# hundred MB. The failure mode that follows is not a stall but a collapse — the
# same machine under that pressure rendered at a tenth of its healthy rate and
# did not recover until the load came off. One job at a time is slower and does
# not do that.
QUEUE_CONCURRENCY = 1

# The last few jobs handed out, newest last. A job waits on the one
# QUEUE_CONCURRENCY places behind it, which is nothing at all until that many
# have been queued.
_QUEUE_RECENT: list[dict[str, Any]] = []

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
    previous: tuple[Optional[str], asyncio.Event], task_id: str, client_id: Optional[str]
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
    announced = False
    try:
        while not previous_event.is_set():
            if not announced:
                _record_queue_wait(task_id, client_id, previous_id)
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
            _record_queue_wait(task_id, client_id, None)


def _queue_tail_barrier() -> Optional[tuple[Optional[str], asyncio.Event]]:
    """The job this one has to wait for, or None when it can start now.

    With QUEUE_CONCURRENCY at 1 this is the previous job and the queue is
    strictly serial, which is what it used to be unconditionally.
    """
    if len(_QUEUE_RECENT) < QUEUE_CONCURRENCY:
        return None
    ahead = _QUEUE_RECENT[-QUEUE_CONCURRENCY]
    event = ahead["event"]
    if event is None or event.is_set():
        return None
    return (ahead["task_id"], event)


def _queue_tail_record(task_id: Optional[str], done: asyncio.Event) -> None:
    """Remember this job as the newest, and forget the ones nobody can wait on.

    Trimming matters: the list is process-local and an archive run puts hundreds
    of jobs through it.
    """
    link = {"task_id": task_id, "event": done}
    _QUEUE_RECENT.append(link)
    keep = max(QUEUE_CONCURRENCY, 1) + 1
    if len(_QUEUE_RECENT) > keep:
        del _QUEUE_RECENT[:-keep]
    _QUEUE_LINE[:] = [item for item in _QUEUE_LINE if not item["event"].is_set()]
    _QUEUE_LINE.append(link)


def _queue_tail_forget(done: asyncio.Event) -> None:
    """Take back a link whose runner never started."""
    _QUEUE_RECENT[:] = [link for link in _QUEUE_RECENT if link["event"] is not done]
    _QUEUE_LINE[:] = [link for link in _QUEUE_LINE if link["event"] is not done]


# Every job still holding a place in line, oldest first: the one working and
# the ones waiting behind it. ``_QUEUE_RECENT`` is trimmed to what a waiter needs
# and cannot say how long the line is; this can, and is pruned as jobs release.
_QUEUE_LINE: list[dict[str, Any]] = []


def queue_place(task_id: str) -> tuple[int, int]:
    """Where this job stands in the live line: (its place, how many are in line).

    Place 1 is the job working now. A job not in line (already finished, or
    never queued) answers (0, line length).
    """
    _QUEUE_LINE[:] = [
        link for link in _QUEUE_LINE
        if not link["event"].is_set() and JOB_EVENTS.is_running(str(link["task_id"]))
    ]
    for place, link in enumerate(_QUEUE_LINE, start=1):
        if link["task_id"] == task_id:
            return place, len(_QUEUE_LINE)
    return 0, len(_QUEUE_LINE)


def _with_queue_place(item: dict[str, Any]) -> dict[str, Any]:
    """The submit answer's position, read from the live line, not the batch."""
    task_id = item.get("task_id")
    if not task_id:
        return item
    place, line = queue_place(str(task_id))
    if place:
        item["queue_position"] = place
        item["queue_total"] = line
    return item


def queue_is_busy() -> bool:
    """Whether a pipeline job is running or waiting its turn right now.

    For the entries that cannot join the chain — a manual de-breath or a note
    run on a finished task — and so refuse instead of starting beside it. Reads
    the same two facts the chain itself reads: a link whose event is unset and
    whose runner the hub still has. A link whose runner is gone without
    releasing is the stale-chain case the waiters heal, and it is not busy.
    """
    return any(
        link["event"] is not None
        and not link["event"].is_set()
        and JOB_EVENTS.is_running(str(link["task_id"]))
        for link in _QUEUE_RECENT
    )


def _log_visual_note_unavailable(task_id: str, *, source_type: str, source_filename: str) -> None:
    """The automatic frame note is on but cannot run, so this task gets the text
    note instead. Recorded because it is invisible otherwise: the task looks
    like any other text-note task."""
    channel = visual_note_channel.resolve_channel(resolve_secret(None, "anthropic_api_key"))
    login_expired = (
        channel.name == visual_note_channel.CHANNEL_SUBSCRIPTION
        and claude_code_note.login_state() is False
    )
    log_event(
        task_id=task_id,
        event_name="visual_note_unavailable",
        source_type=source_type,
        source_filename=source_filename,
        stage="summary",
        success=False,
        error_reason=channel.unavailable_reason,
        metadata=event_metadata(
            channel=channel.name,
            reason="login_expired" if login_expired else "channel_unavailable",
            fallback="text_note",
        ),
    )


async def wait_for_queue_turn(
    previous: Optional[tuple[Optional[str], asyncio.Event]],
    task_id: str,
    client_id: Optional[str],
) -> None:
    """Hold this job's place in the chain until the job ahead is done.

    A runner cancelled while it is only a queue placeholder keeps its successor
    behind the same barrier: it waits the predecessor out before re-raising,
    because releasing ``done`` at once would let the next worker overlap the
    still-running previous one.
    """
    if previous is None:
        return
    started = time.perf_counter()
    try:
        await _await_predecessor(previous, task_id, client_id)
    except asyncio.CancelledError:
        await _await_predecessor(previous, task_id, client_id)
        raise
    # A task's total time starts when it is submitted, so without this a long
    # wait behind other recordings reads as slow processing.
    log_event(
        task_id=task_id,
        event_name="queue_wait_completed",
        stage="queue",
        duration_seconds=round(time.perf_counter() - started, 3),
        success=True,
        metadata=event_metadata(waited_behind=previous[0]),
    )


# How many notes may wait on the remote model at once. That wait does not hold
# the queue (see `_run_serially`); this only stops a long batch from opening a
# Claude session per finished task. A thread semaphore because the wait happens
# in the note's worker thread, not on the event loop.
NOTE_CONCURRENCY = 2
_NOTE_REMOTE_SLOTS = threading.BoundedSemaphore(NOTE_CONCURRENCY)

# Notes run on their own threads, never in the event loop's default executor.
# A note thread spends minutes blocked (on a slot above, then on Claude), and
# the default executor is the one every upload, preflight and store write goes
# through: a batch of short recordings used to park a note thread there per
# finished task until uploads and the pipeline itself waited for a free thread.
# The few spare threads past NOTE_CONCURRENCY let that many finished recordings
# pick their frames and wait for a slot while the queue moves on; past that the
# next note waits for a thread, which holds the queue (its frame work is queue
# work) rather than piling up more blocked threads.
NOTE_WAITING_AHEAD = 4
_NOTE_EXECUTOR = ThreadPoolExecutor(
    max_workers=NOTE_CONCURRENCY + NOTE_WAITING_AHEAD, thread_name_prefix="fluentflow-note",
)
# How often a thread waiting for a slot checks whether its task was cancelled.
_NOTE_SLOT_POLL_SECONDS = 0.25


class NoteAbandoned(visual_note_job.VisualNoteError):
    """The task was cancelled before its note got to Claude."""


def _write_note_holding_slot(
    task_id: str,
    client_id: Optional[str],
    release_queue: Any,
    abandoned: threading.Event,
) -> None:
    """Write the note on this thread, holding a remote slot from the hand-off on.

    The thread owns the slot from acquire to release. The coroutine that
    started it can be cancelled at any point, but the thread runs on: releasing
    from the coroutine freed a slot that a running Claude request still used,
    and a coroutine cancelled while the thread was still waiting never saw the
    slot it would later take, so it was never given back.
    """
    slots = _NOTE_REMOTE_SLOTS
    holding = False

    def hand_off() -> None:
        nonlocal holding
        if release_queue is not None:
            release_queue()
        while not slots.acquire(timeout=_NOTE_SLOT_POLL_SECONDS):
            if abandoned.is_set():
                raise NoteAbandoned("任务已取消，笔记没有写。")
        holding = True
        if abandoned.is_set():
            raise NoteAbandoned("任务已取消，笔记没有写。")

    try:
        local_intake_flow.write_note(task_id, client_id, on_local_work_done=hand_off)
    except NoteAbandoned as exc:
        # The frame note records this itself; the text-note path lets it through.
        local_intake_flow._patch_result(task_id, client_id, {
            "summary_status": "failed",
            "summary_error": f"{exc}",
            "summary_skipped": False,
        })
    finally:
        if holding:
            slots.release()


async def _write_note_after_transcript(
    task_id: str,
    client_id: Optional[str],
    *,
    on_local_work_done: Any = None,
) -> None:
    """The last step of the flow: the note, from the file the transcript came from.

    Runs here rather than inside the pipeline because the note job persists its own
    progress and the pipeline owns the result until it completes. The seam is
    visible to the user as a task that is finished and readable while its note is
    still being written, which is why the result is marked accordingly first.

    ``on_local_work_done`` is called from the note thread once the note no longer
    uses this machine; the note then waits for one of NOTE_CONCURRENCY slots.

    Never raises: the transcript and the cut file are already the user's, and a
    note failure must not take them away. `write_note` records the reason where the
    note belongs.
    """
    if not local_intake_flow.note_is_wanted(task_id, client_id):
        return
    local_intake_flow.mark_note_running(task_id, client_id)
    abandoned = threading.Event()
    future = asyncio.get_running_loop().run_in_executor(
        _NOTE_EXECUTOR,
        functools.partial(_write_note_holding_slot, task_id, client_id, on_local_work_done, abandoned),
    )
    try:
        await future
    except asyncio.CancelledError:
        # A note not yet started is dropped with the future; one that has
        # started gives up at the slot instead of calling Claude for a task
        # nobody wants any more, and releases what it holds itself.
        abandoned.set()
        raise


async def _run_pipeline(ctx: MediaJobContext) -> None:
    """The part of a task that uses this machine: cut, transcribe, store."""
    await execute_media_job(ctx)


async def _run_serially(
    previous: Optional[tuple[Optional[str], asyncio.Event]],
    done: asyncio.Event,
    ctx: MediaJobContext,
) -> None:
    """Wait for the task ahead, then the pipeline and its note."""
    try:
        await wait_for_queue_turn(previous, ctx.task_id_value, ctx.client_id)
    except BaseException:
        done.set()
        raise
    await run_pipeline_then_note(done, ctx)


async def run_pipeline_then_note(done: asyncio.Event, ctx: MediaJobContext) -> None:
    """Run the pipeline and the note's local part, then hand the queue on while
    the note waits on the remote model. Every entry that transcribes goes
    through here once its turn has come, so none of them can end without a note
    step: the link entry used to stop after the pipeline, and since the
    pipeline leaves the note to this step, link tasks got no note at all.

    Holding the queue through the whole note left this machine idle for minutes
    per task (note median about three minutes, a quarter over six). Releasing it
    as soon as the transcript was stored would have been wrong the other way: the
    note first picks its pictures by decoding the whole video, measured at about
    two and a half cores for half a minute per twelve minutes of video, and that
    belongs in the queue with every other local job. Running local jobs side by
    side on this machine was measured slower than running them in turn.
    """
    loop = asyncio.get_running_loop()

    def hand_off() -> None:
        # Called from the note's worker thread, once its frames are on disk.
        loop.call_soon_threadsafe(done.set)

    try:
        await _run_pipeline(ctx)
        await _write_note_after_transcript(
            ctx.task_id_value, ctx.client_id, on_local_work_done=hand_off,
        )
        await _export_note_written_after_pipeline(ctx)
    finally:
        # Always release the chain — including when this job is cancelled, and
        # when the note ended before reaching its hand-off. The remote slot is
        # not released here: the note thread owns it (`_write_note_holding_slot`).
        done.set()


async def _export_note_written_after_pipeline(ctx: MediaJobContext) -> None:
    """Send the note written after the pipeline to Feishu, when asked to.

    The pipeline exports the note it writes itself. When that note was left to
    the frame note that follows, the pipeline's export never ran, and a user
    with "export to Feishu automatically" on got no document for any task.
    Never raises: the note is already the user's.
    """
    if not getattr(ctx, "do_lark", False) or not getattr(ctx, "note_deferred_to_visual_note", False):
        return
    job = get_job(ctx.task_id_value, client_id=ctx.client_id) or {}
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    markdown = str(result.get("summary_markdown") or "").strip()
    if not markdown or result.get("lark_response"):
        return
    started = time.perf_counter()
    common = {
        "task_id": ctx.task_id_value,
        "source_type": ctx.source_type,
        "source_filename": ctx.source_filename,
        "summary_length": len(markdown),
        "stage": "export",
    }
    log_event(event_name="lark_export_started", metadata=event_metadata(trigger="auto_after_note"), **common)
    try:
        export = await asyncio.to_thread(
            _auto_export_local_lark,
            task_id=ctx.task_id_value,
            summary_markdown=markdown,
            filename_stem=ctx.display_title_value or Path(ctx.source_filename or "media").stem,
            form_title=ctx.title or ctx.display_title_value,
            lark_export_route=ctx.lark_export_route,
            lark_via_cli=ctx.lark_via_cli,
            lark_app_id=ctx.lark_app_id,
            lark_app_secret=ctx.lark_app_secret,
            folder_token=ctx.folder_token,
        )
    except Exception as exc:  # noqa: BLE001 - a failed export keeps the note
        message = friendly_error(exc)
        latest = (get_job(ctx.task_id_value, client_id=ctx.client_id) or {}).get("result") or {}
        update_job_result(ctx.task_id_value, {**latest, "lark_error": message}, client_id=ctx.client_id)
        log_event(
            event_name="lark_export_completed",
            duration_seconds=round(time.perf_counter() - started, 3),
            success=False,
            error_reason=message,
            metadata=event_metadata(trigger="auto_after_note", raw_error=str(exc)),
            **common,
        )
        return
    response = export["response"]
    latest = (get_job(ctx.task_id_value, client_id=ctx.client_id) or {}).get("result") or {}
    update_job_result(ctx.task_id_value, {
        **latest,
        "lark_doc_title": export["doc_title"],
        "lark_response": response,
        "lark_error": None,
    }, client_id=ctx.client_id)
    log_event(
        event_name="lark_export_completed",
        duration_seconds=round(time.perf_counter() - started, 3),
        success=True,
        export_target=export["export_target"],
        feishu_doc_url=response.get("url") if isinstance(response, dict) else None,
        metadata=event_metadata(trigger="auto_after_note", doc_title=export["doc_title"]),
        **common,
    )


async def _start_behind_queue(
    *,
    task_id: str,
    client_id: Optional[str],
    route: str,
    stage: str,
    chained_worker: Any,
    gate: Any = None,
) -> bool:
    """Put a job on the chain and start its runner; say whether one started.

    ``chained_worker(previous, done)`` is the job: it must wait its turn with
    ``wait_for_queue_turn(previous, ...)`` before the expensive part and set
    ``done`` when it is over, however it ends.
    """
    # Barrier and record happen together, before the first await, so two
    # submissions arriving at once cannot both take the same job as the one
    # they wait for.
    previous = _queue_tail_barrier()
    done = asyncio.Event()
    _queue_tail_record(task_id, done)
    started = await JOB_EVENTS.start(
        task_id,
        functools.partial(
            run_worker_with_terminal_state,
            task_id=task_id,
            client_id=client_id,
            hub=JOB_EVENTS,
            route=route,
            stage=stage,
            gate=gate,
            worker=functools.partial(chained_worker, previous, done),
        ),
    )
    if not started:
        # Nothing will run that worker (this task id is already running), so
        # nothing will run its ``finally``. The link comes off the chain, so the
        # next submission waits behind the job that really is working. A
        # submission that already took this link as its barrier while the start
        # was pending is released only when the job ahead of this one is: setting
        # ``done`` now told it the line was empty while that job still ran.
        _queue_tail_forget(done)
        if previous is None:
            done.set()
        else:
            _hold_link_until_released(previous, done)
    return started


_LINK_RELAYS: set[asyncio.Task[None]] = set()


def _hold_link_until_released(
    previous: tuple[Optional[str], asyncio.Event], done: asyncio.Event
) -> None:
    """Set ``done`` once the job ahead releases the chain or is gone."""

    async def relay() -> None:
        previous_id, previous_event = previous
        try:
            while not previous_event.is_set():
                try:
                    await asyncio.wait_for(previous_event.wait(), _QUEUE_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    if previous_event.is_set():
                        break
                    if previous_id and await JOB_EVENTS.has_running_task(previous_id):
                        continue
                    break
        finally:
            done.set()

    task = asyncio.get_running_loop().create_task(relay())
    _LINK_RELAYS.add(task)
    task.add_done_callback(_LINK_RELAYS.discard)


async def start_media_job_behind_queue(ctx: MediaJobContext, *, route: str) -> bool:
    """The pipeline and then its note, behind every job already queued."""
    ctx.event_route = route
    return await _start_behind_queue(
        task_id=ctx.task_id_value,
        client_id=ctx.client_id,
        route=route,
        stage="processing",
        chained_worker=lambda previous, done: _run_serially(previous, done, ctx),
    )


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
    STT and the local event hub. ``options`` uses the persisted queue-options vocabulary so the
    upload and retry entries cannot drift apart."""
    skip_summary = truthy(options.get("skip_summary"))
    visual_note_will_run = local_intake_flow.auto_note_will_run()
    if not skip_summary and local_intake_flow.auto_note_enabled() and not visual_note_will_run:
        _log_visual_note_unavailable(task_id, source_type=source_type, source_filename=source_filename)
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
        task_started_at=time.perf_counter(),
        loop=asyncio.get_event_loop(),
        model_size=(options.get("stt_model") or "").strip() or DEFAULT_LOCAL_STT_MODEL,
        speed_profile=(options.get("stt_speed") or "").strip() or "balanced",
        language="auto",
        stt_provider_value=LOCAL_STT_PROVIDER,
        diarization_requested=truthy(options.get("speaker_diarization")),
        voice_enhance_requested=truthy(options.get("voice_enhance")),
        do_lark=truthy(options.get("export_to_lark")),
        # The pipeline's own note stage is switched off whenever this edition is
        # going to write the note itself from the cut media. That is the "remove
        # the old flow" half of the owner's decision: without this the user gets a
        # note from the text model first and the real one a minute later, which is
        # the two-notes shape the whole change exists to end.
        #
        # `will_run` rather than `enabled`: a machine that cannot reach Claude
        # never writes that note, and switching the text one off for it left a
        # fresh install with no note at all.
        summary_disabled=skip_summary or visual_note_will_run,
        note_deferred_to_visual_note=visual_note_will_run and not skip_summary,
        generate_visuals=truthy(options.get("generate_visuals")),
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
        preflight = await asyncio.to_thread(preflight_media_file, source_path)
    except MediaPreflightError as exc:
        # Refused before a task row exists, so an unreadable file does not leave a
        # queued task that can never run.
        return {"filename": source_path.name, "status": "rejected", "reason": str(exc)}
    task_id_value = claim_task_id(None, client_id=client_id)
    suffix = source_path.suffix.lower() or ".mp4"
    raw_title_value = (options.get("title") or source_path.stem).strip()
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
    await start_media_job_behind_queue(ctx, route=route)
    return _with_queue_place({
        "task_id": task_id_value,
        "filename": source_path.name,
        "status": "queued",
        "queue_position": index,
        "queue_total": total,
    })


def _option_default_on(payload: dict, key: str) -> str:
    """The caller's value for a switch that is on unless they turned it off."""
    if key not in (payload or {}):
        return "true"
    raw = (payload or {}).get(key)
    if raw is None:
        return "true"
    return "true" if truthy(str(raw)) else ""


def _payload_text(payload: dict, key: str) -> Optional[str]:
    """A JSON payload's value spelled the way a form field would carry it.

    The upload routes receive every option as form text ("true", "1800.0");
    a JSON caller may send ``true`` or ``1800``. Spelling both the same way is
    what keeps a task's stored ``queue_options`` identical whichever door it
    came in through, so a retry or a later reader cannot tell them apart.
    """
    value = (payload or {}).get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def local_path_options(
    payload: dict,
    *,
    file_count: int = 1,
    speaker_diarization_default_on: bool = True,
) -> tuple[dict, float | None]:
    """The run options of a by-path submission, in the upload routes' vocabulary.

    Takes every option ``/queue/process`` takes, so a file chosen with the system
    dialog or found in a folder runs with the same settings as the same file
    dragged in: export to Feishu, provider and model, prompt, visuals. A key
    missing here is dropped without an error and the run reports success with
    the feature never having run, so ``tests/test_local_option_vocabulary_parity.py``
    pins the two key sets equal.

    ``title`` applies only to a single file, as on the upload route: a batch
    sharing one typed title would make every task the same name.

    Speaker separation defaults ON for callers with no page — the Agent API, the
    MCP tool, a curl — when pyannote is installed (otherwise the request would
    always be skipped, so it is not made). The settings page's default never reaches them, and a
    2026-09-03 report of an 8-person meeting transcribed with no speakers was
    exactly that gap. The page's own routes pass
    ``speaker_diarization_default_on=False``: the page always says what the user
    chose, and an absent switch means off there, exactly as on the upload route.
    An explicit false always wins.
    """
    limit = _effective_duration_limit(payload.get("duration_limit_seconds"))
    if speaker_diarization_default_on:
        asked = (payload or {}).get("speaker_diarization") is not None
        diarization = (
            _option_default_on(payload, "speaker_diarization")
            if asked or speaker_diarization_core.default_on_for_agents()
            else None
        )
    else:
        diarization = _payload_text(payload, "speaker_diarization")
    return _collect_options(
        export_to_lark=_payload_text(payload, "export_to_lark"),
        lark_export_route=_payload_text(payload, "lark_export_route"),
        lark_via_cli=_payload_text(payload, "lark_via_cli"),
        title=_payload_text(payload, "title") if file_count == 1 else None,
        folder_token=_payload_text(payload, "folder_token"),
        ai_provider=_payload_text(payload, "ai_provider"),
        ai_model=_payload_text(payload, "ai_model"),
        note_mode=_payload_text(payload, "note_mode"),
        skip_summary=_payload_text(payload, "skip_summary"),
        generate_visuals=_payload_text(payload, "generate_visuals"),
        stt_model=_payload_text(payload, "stt_model"),
        stt_speed=_payload_text(payload, "stt_speed"),
        speaker_diarization=diarization,
        voice_enhance=_payload_text(payload, "voice_enhance"),
        system_prompt=_payload_text(payload, "system_prompt"),
        prompt_preset=_payload_text(payload, "prompt_preset"),
        prompt_preset_label=_payload_text(payload, "prompt_preset_label"),
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
    return {"ok": True, "queued": [_with_queue_place(item) for item in queued], "count": len(queued)}


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
    *,
    local_caller: bool,
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
    if not local_caller:
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
    return await retry_task(
        task_id,
        client_id=local_client_scope(request),
        local_caller=request_is_localhost(request),
    )


async def _retry_link_task(
    *,
    task_id: str,
    job: dict,
    client_id: Optional[str],
    carry: Optional[dict],
) -> Optional[dict]:
    """Re-submit a link task that never got its video: fetch the link again.

    A link that failed while downloading has no recording to re-run, and the
    retry used to answer that the recording "was cleaned up" although there
    never was one. The full link is kept as ``video_source_url``; older tasks
    only have the share-text preview, which is searched for a link instead.
    Returns None when the task is not a link task or no link can be found.
    """
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    if job.get("source_type") != "video_link" and not metadata.get("video_source_url"):
        return None
    from backend.core.video_source import extract_first_url
    from backend.routers.local_video_sources import submit_video_source_job

    link = str(metadata.get("video_source_url") or "").strip() or extract_first_url(
        str(metadata.get("video_source_input_preview") or "")
    )
    if not link:
        return None
    stored = metadata.get("queue_options")
    raw_options: dict = dict(stored) if isinstance(stored, dict) else {}
    # How the link is fetched follows the user's settings now, not the ones in
    # force when it was first submitted: someone who turned the third-party
    # Douyin resolver off, or changed the browser whose login is read, must not
    # have a retry (theirs, the MCP tool's, or startup recovery's) quietly use
    # the old choice. Leaving both out lets submit_video_source_job read the
    # current preferences (`allow_miuistore`, `video_cookies_browser`).
    raw_options.pop("cookies_from_browser", None)
    raw_options.pop("allow_miuistore", None)
    new_job = await submit_video_source_job(
        input_text=link,
        title=str(metadata.get("video_source_title") or "").strip(),
        raw_options=raw_options,
        client_id=client_id,
        extra_metadata={"retry_source_task_id": task_id, **(carry or {})},
        # Retrying this task is the confirmation: a completed copy of the same
        # link elsewhere does not block it. One already queued or running is
        # still answered instead of being fetched a second time.
        allow_duplicate=True,
    )
    retry_task_id = new_job["task_id"]
    started_job = get_job(retry_task_id, client_id=client_id) or new_job
    return {
        "ok": True,
        "source_task_id": task_id,
        "task_id": retry_task_id,
        "job": {**started_job, "task_snapshot": build_task_snapshot(started_job)},
    }


async def retry_task(
    task_id: str,
    *,
    client_id: Optional[str],
    local_caller: bool,
    carry: Optional[dict] = None,
) -> dict:
    """Queue a fresh task from this one's recording; the old row stays as it was.

    The route and startup recovery both come here, so a restart-interrupted task
    re-runs through exactly the path the user's own retry button takes.
    ``local_caller`` says the request came from this machine, which is what
    allows reading a file by its path. ``carry`` is written onto the new task
    when it is created, before anything can run it.
    """
    job = get_job(task_id, client_id=client_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Cancel the active job before retrying it")
    in_place = _in_place_origin(job)
    if in_place is not None:
        return await _retry_in_place(
            local_caller=local_caller,
            task_id=task_id,
            client_id=client_id,
            job=job,
            origin={**in_place, **(carry or {})},
        )
    source = find_source_file(task_id)
    if not source:
        link_retry = await _retry_link_task(task_id=task_id, job=job, client_id=client_id, carry=carry)
        if link_retry is not None:
            return link_retry
        raise HTTPException(
            status_code=404,
            detail="这个任务的原始录音已按保留策略清理，无法直接重新运行。请重新添加文件后再处理。",
        )

    filename = Path(job.get("source_filename") or source.name).name
    suffix = source.suffix or Path(filename).suffix.lower() or ".mp4"
    retry_task_id = claim_task_id(None, client_id=client_id)
    try:
        target_path = await asyncio.to_thread(copy_source_file, retry_task_id, suffix, source)
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
        media_preflight = await asyncio.to_thread(preflight_media_file, target_path)
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
        **(carry or {}),
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
    # Behind the same chain as every other upload, and with the note after it:
    # a retry that started at once ran beside whatever was already transcribing,
    # which is the memory collapse QUEUE_CONCURRENCY exists to prevent, and it
    # ended at the transcript without ever writing the note.
    await start_media_job_behind_queue(ctx, route="/jobs/{task_id}/retry")

    started_job = get_job(retry_task_id, client_id=client_id) or {
        "task_id": retry_task_id,
        "status": "queued",
        "stage": "queued",
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
