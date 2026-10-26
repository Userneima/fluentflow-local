"""What runs once a task's turn in the queue has come: the pipeline (fetch,
transcribe, correct), then the cut, the note, the compressed copy, the
Feishu export and the output folder, each recorded as a step
(``task_steps``). Every entry that transcribes ends up here.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import shutil
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from backend.core import (
    agent_cut,
    local_intake_flow,
    output_folder,
    survey_cut,
    tail_trim,
    task_steps,
    task_stop,
    visual_note_channel,
    visual_note_job,
)
from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.job_store import get_job, list_jobs_by_statuses, update_job_result
from backend.core.lark_cli_exporter import export_markdown_via_lark_cli, lark_cli_ready
from backend.core.lark_exporter import export_markdown_to_lark
from backend.core.local_config import resolve_secret
from backend.core.local_entry_guards import friendly_error
from backend.core.local_job_runtime import JOB_EVENTS
from backend.core.media_job import RESUME_DIRNAME, MediaJobContext, execute_media_job
from backend.core.note_title import resolve_lark_doc_title
from backend.core.runtime_env import truthy
from backend.core.storage_paths import _artifact_storage_dir
from backend.core.task_queue import _take_queue_turn, wait_for_queue_turn
from backend.core.task_watch import TaskCancelled, TaskWatch

logger = logging.getLogger(__name__)


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
    planned_ranges: Any = None,
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
        with task_stop.bound(task_id):
            local_intake_flow.write_note(
                task_id, client_id, on_local_work_done=hand_off,
                **({"planned_ranges": planned_ranges} if planned_ranges else {}),
            )
        if task_stop.requested(task_id):
            # Its Claude was stopped with the step: the note says so, not
            # what the stopped command line printed.
            local_intake_flow._patch_result(task_id, client_id, {
                "summary_status": "failed", "summary_error": task_steps.STOPPED_NOTE, "summary_skipped": False,
            })
    except TaskCancelled:
        # Picking its pictures stopped with the step (``frame_extractor``).
        if not task_stop.requested(task_id):
            raise
        local_intake_flow._patch_result(task_id, client_id, {
            "summary_status": "failed", "summary_error": task_steps.STOPPED_NOTE, "summary_skipped": False,
        })
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
    planned_ranges: Any = None,
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
        # No note to write here: the pipeline wrote it, or none was asked for.
        task_steps.settle(task_id, task_steps.NOTE, task_steps.note_outcome(get_job(task_id, client_id=client_id)))
        return
    task_steps.start(task_id, task_steps.NOTE)
    local_intake_flow.mark_note_running(task_id, client_id)
    abandoned = threading.Event()
    future = asyncio.get_running_loop().run_in_executor(
        _NOTE_EXECUTOR,
        functools.partial(
            _write_note_holding_slot, task_id, client_id, on_local_work_done, abandoned, planned_ranges,
        ),
    )
    try:
        await future
    except asyncio.CancelledError:
        # A note not yet started is dropped with the future; one that has
        # started gives up at the slot instead of calling Claude for a task
        # nobody wants any more, and releases what it holds itself.
        abandoned.set()
        raise
    except Exception as exc:
        task_steps.finish(task_id, task_steps.NOTE, task_steps.FAILED, friendly_error(exc))
        raise
    task_steps.settle(task_id, task_steps.NOTE, task_steps.note_outcome(get_job(task_id, client_id=client_id)))


async def _run_pipeline(ctx: MediaJobContext) -> None:
    """The part of a task that uses this machine: cut, transcribe, store."""
    await execute_media_job(ctx)


async def _run_serially(
    previous: Optional[tuple[Optional[str], asyncio.Event]],
    done: asyncio.Event,
    ctx: MediaJobContext,
    start_at: str = task_steps.FETCH,
) -> None:
    """Wait for the task ahead, then the pipeline and its note (from
    ``start_at`` when the task is carried on)."""
    try:
        await wait_for_queue_turn(previous, ctx.task_id_value, ctx.client_id)
    except BaseException:
        done.set()
        raise
    await run_pipeline_then_note(done, ctx, start_at=start_at)


class StepStopped(asyncio.CancelledError):
    """The user stopped this task's step (``task_stop``)."""


def _raise_if_stopped(task_id: str) -> None:
    if task_stop.requested(task_id):
        raise StepStopped()


async def run_pipeline_then_note(
    done: asyncio.Event, ctx: MediaJobContext, *, start_at: str = task_steps.FETCH,
) -> None:
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

    ``start_at`` carries a task on from that step (after a restart, or when the
    user carries on a stopped task): the steps it already did are not done again
    (decision 6.42).
    """
    loop = asyncio.get_running_loop()
    compressing: list[asyncio.Future] = []
    task_id = ctx.task_id_value
    recorded = task_steps.steps_of([task_id]).get(task_id, [])
    if recorded:
        task_steps.reopen_from(task_id, start_at, recorded)
    else:
        task_steps.plan(task_id)
    done_before = {name for name, status in task_steps.statuses(recorded).items() if status == task_steps.DONE}
    task_stop.begin(task_id)

    def hand_off() -> None:
        # Called once the note's frames are on disk (from its worker thread) or
        # the cut is rendered. The compressed copy of a high-bitrate original
        # is local work too, so it runs first and the queue moves on after it.
        def start() -> None:
            if compressing:
                return
            if task_steps.COMPRESS in done_before:
                compressing.append(asyncio.ensure_future(_release(done)))
            else:
                compressing.append(asyncio.ensure_future(_compress_then_release(ctx, done)))
        loop.call_soon_threadsafe(start)

    stopped = False
    try:
        if start_at in task_steps.BEFORE_TRANSCRIPT:
            if not await _steps_before_transcript(ctx, start_at):
                return
        named = task_steps.statuses(task_steps.steps_of([task_id]).get(task_id, []))
        if named.get(task_steps.CUT) != task_steps.DONE:
            if agent_cut_unfinished(get_job(task_id, client_id=ctx.client_id)) or await _agent_judges_cut(ctx):
                # The queue moves on while the user's agent judges; this task
                # takes a new place in line once it has a decision.
                done.set()
                await continue_after_agent(task_id, ctx.client_id, ctx)
                return
            if named.get(task_steps.NOTE) == task_steps.DONE:
                await _trim_recorded_tail(ctx)
            else:
                await _cut_then_note(ctx, hand_off)
        elif named.get(task_steps.NOTE) != task_steps.DONE:
            await _write_note_after_transcript(task_id, ctx.client_id, on_local_work_done=hand_off)
        _raise_if_stopped(task_id)
        if not compressing:
            # No note took the hand-off (none wanted, or it stopped early).
            hand_off()
            await asyncio.sleep(0)
        if compressing:
            # Delivered after the copy exists, so the folder lists it.
            await compressing[0]
        _raise_if_stopped(task_id)
        if named.get(task_steps.FEISHU) != task_steps.DONE:
            await _feishu_step(ctx)
        await _deliver_to_output_folder(ctx)
    except asyncio.CancelledError:
        stopped = True
        for step in compressing:
            step.cancel()
        raise
    finally:
        # Always release the chain — including when this job is cancelled, and
        # when the note ended before reaching its hand-off. The remote slot is
        # not released here: the note thread owns it (`_write_note_holding_slot`).
        done.set()
        # Nothing runs this task's steps once this returns.
        task_steps.close_open(task_id, stopped=stopped or task_stop.requested(task_id))


def _bound_to(task_id: str, work: Any) -> Any:
    """``work`` run on a worker thread as work of this task (``task_stop``)."""
    def run() -> Any:
        with task_stop.bound(task_id):
            return work()
    return run


async def _release(done: asyncio.Event) -> None:
    done.set()


# ── the steps before the transcript is stored ───────────────────────────────
#
# The pipeline (``media_job``) fetches nothing itself: a recording is uploaded
# or read in place before the task is queued, and a link is downloaded by
# ``link_submission`` (which records the fetch step). It transcribes, then
# corrects; the moment the transcription is done it hands over what it
# produced, which is kept so that a task cut off during the corrections
# carries on from them (``transcribe_checkpoint``).

CHECKPOINT_FILE = "transcribed.json"


def _checkpoint_path(task_id: str) -> Path:
    return _artifact_storage_dir() / task_id / RESUME_DIRNAME / CHECKPOINT_FILE


def _save_checkpoint(task_id: str, checkpoint: dict[str, Any]) -> None:
    try:
        path = _checkpoint_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(checkpoint, ensure_ascii=False, default=str), encoding="utf-8")
        temporary.replace(path)
    except Exception:  # noqa: BLE001 - the task goes on; carrying on would transcribe again
        logger.warning("could not keep the transcription of %s for carrying on", task_id, exc_info=True)


def load_checkpoint(task_id: str) -> dict[str, Any] | None:
    try:
        return json.loads(_checkpoint_path(task_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _drop_checkpoint(task_id: str) -> None:
    shutil.rmtree(_checkpoint_path(task_id).parent, ignore_errors=True)


async def _steps_before_transcript(ctx: Any, start_at: str) -> bool:
    """Fetch (a file already on this machine), transcribe and correct, as
    steps. Raises ``StepStopped`` when the user stopped one."""
    task_id = ctx.task_id_value
    if start_at == task_steps.FETCH:
        task_steps.start(task_id, task_steps.FETCH)
        task_steps.finish(task_id, task_steps.FETCH, task_steps.DONE)
    checkpoint = load_checkpoint(task_id) if start_at == task_steps.CORRECT else None
    if checkpoint is None:
        if start_at == task_steps.CORRECT:
            # Nothing kept from the transcription: it is done again.
            task_steps.reopen_from(task_id, task_steps.TRANSCRIBE)
        task_steps.start(task_id, task_steps.TRANSCRIBE)
    else:
        task_steps.start(task_id, task_steps.CORRECT)

    def transcribed(record: dict[str, Any]) -> None:
        _save_checkpoint(task_id, record)
        task_steps.finish(task_id, task_steps.TRANSCRIBE, task_steps.DONE)
        task_steps.start(task_id, task_steps.CORRECT)

    ctx.transcribe_checkpoint = checkpoint
    ctx.on_transcribed = transcribed
    ctx.save_checkpoint = functools.partial(_save_checkpoint, task_id)
    await _run_pipeline(ctx)
    outcome = task_steps.transcribe_outcome(get_job(task_id, client_id=ctx.client_id))
    if task_stop.requested(task_id) and outcome[0] != task_steps.DONE:
        outcome = (task_steps.STOPPED, None)
    current = task_steps.statuses(task_steps.steps_of([task_id]).get(task_id, []))
    for name in (task_steps.TRANSCRIBE, task_steps.CORRECT):
        if current.get(name) in task_steps.OPEN:
            task_steps.settle(task_id, name, outcome)
    if outcome[0] == task_steps.STOPPED:
        raise StepStopped()
    if outcome[0] == task_steps.DONE:
        _drop_checkpoint(task_id)
    _raise_if_stopped(task_id)
    # A failed transcription goes on as before: each later step finds
    # nothing to work on and says so.
    return True


async def _cut_then_note(ctx: Any, hand_off: Any, **cut: Any) -> None:
    """Cut the transcribed recording and write its note: side by side when
    the note can start from the plan, else one after the other. ``cut`` goes
    to ``survey_cut.survey_and_cut`` (a decision made by the user's agent)."""
    if _note_can_start_before_the_cut(ctx):
        await _cut_and_note_side_by_side(ctx, hand_off, **cut)
    else:
        await _trim_recorded_tail(ctx, **cut)
        await _write_note_after_transcript(
            ctx.task_id_value, ctx.client_id, on_local_work_done=hand_off,
        )


async def _agent_judges_cut(ctx: MediaJobContext) -> bool:
    """Whether this task waits for the user's agent to judge the cut
    (``agent_cut``); if so, its material is measured now, in the queue.
    Never raises: a failure here cuts the task as usual."""
    loop = asyncio.get_running_loop()
    try:
        if not await loop.run_in_executor(None, agent_cut.should_wait, ctx.task_id_value, ctx.client_id):
            return False
        return await loop.run_in_executor(None, agent_cut.prepare, ctx.task_id_value, ctx.client_id)
    except Exception as exc:  # noqa: BLE001 - FluentFlow judges instead
        logger.warning("could not prepare %s for the agent's judgement: %s", ctx.task_id_value, exc)
        return False


AGENT_CUT_POLL_SECONDS = 3.0


async def continue_after_agent(task_id: str, client_id: Optional[str], ctx: Any = None) -> None:
    """Wait for the agent's decision (or for the agent to be given up on),
    then cut, write the note and deliver, in the queue. ``ctx`` is the
    pipeline's context; without it (resumed after a restart) the note is not
    exported to Feishu automatically. The cut step is open from the start:
    the task is not finished while it waits for the agent."""
    task_steps.start(task_id, task_steps.CUT)
    try:
        await _cut_note_and_export_after_agent(task_id, client_id, ctx)
    except asyncio.CancelledError:
        task_steps.close_open(task_id, stopped=True)
        raise
    finally:
        task_steps.close_open(task_id)


async def _cut_note_and_export_after_agent(task_id: str, client_id: Optional[str], ctx: Any) -> None:
    loop = asyncio.get_running_loop()
    while True:
        state = await loop.run_in_executor(None, agent_cut.check, task_id, client_id)
        if state is not None:
            break
        await asyncio.sleep(AGENT_CUT_POLL_SECONDS)
    if state.get("status") == agent_cut.DECIDED:
        cut = {"decide": agent_cut.decider(state), "judged_by": state.get("judged_by")}
    else:
        cut = {"extra": {"agent_fallback": state.get("fallback_reason") or "agent 没有交回决定"}}
    turn = await _take_queue_turn(task_id, client_id)
    try:
        step = ctx if ctx is not None else types.SimpleNamespace(task_id_value=task_id, client_id=client_id)
        await _cut_then_note(step, lambda: loop.call_soon_threadsafe(turn.set), **cut)
        await loop.run_in_executor(None, agent_cut.finished, task_id, client_id)
        if ctx is not None:
            await _feishu_step(ctx)
        try:
            output_folder.schedule_sync(task_id)
        except Exception as exc:  # noqa: BLE001 - the task's own results stand
            logger.warning("output folder not scheduled for %s: %s", task_id, exc)
    finally:
        turn.set()


def agent_cut_unfinished(job: Any) -> bool:
    """Whether a task's agent-judged cut was left between waiting and done;
    ``resume_agent_cuts`` carries such a task on after a restart."""
    result = (job or {}).get("result") if isinstance((job or {}).get("result"), dict) else {}
    state = agent_cut.state_of(result)
    return state.get("status") in (agent_cut.WAITING, agent_cut.DECIDED, agent_cut.FALLBACK) and not state.get("finished_at")


def agent_cut_goes_on(job: Any) -> bool:
    """Whether a restart carries this task's agent-judged cut on: the cut is
    unfinished and its step is still open (a task from before steps were
    recorded has none and goes on as before). A cut the user stopped stays
    stopped until they carry it on."""
    if not agent_cut_unfinished(job):
        return False
    task_id = str((job or {}).get("task_id") or "")
    named = task_steps.statuses(task_steps.steps_of([task_id]).get(task_id, []))
    return not named or named.get(task_steps.CUT) in task_steps.OPEN


async def resume_agent_cuts() -> int:
    """At startup: every task a restart left waiting for its agent, or
    between the agent's decision and its cut, goes on waiting or is cut."""
    resumed = 0
    for job in list_jobs_by_statuses(("completed",), include_result=True):
        if not agent_cut_goes_on(job):
            continue
        task_id, client_id = str(job.get("task_id")), job.get("client_id")
        if await JOB_EVENTS.start(task_id, functools.partial(continue_after_agent, task_id, client_id)):
            resumed += 1
    return resumed


async def _compress_then_release(ctx: MediaJobContext, done: asyncio.Event) -> None:
    """Make the uncut compressed copy (``local_intake_flow.compress_original``),
    then hand the queue on. Cancelled with its task, it stops the encode."""
    watch = TaskWatch()
    task_steps.start(ctx.task_id_value, task_steps.COMPRESS)
    state: Any = None
    try:
        state = await asyncio.get_running_loop().run_in_executor(
            None, functools.partial(local_intake_flow.compress_original, ctx.task_id_value, ctx.client_id, watch),
        )
    except asyncio.CancelledError:
        watch.cancel()
        raise
    except Exception as exc:  # noqa: BLE001 - the task's own results stand
        logger.warning("compressed copy skipped for %s: %s", ctx.task_id_value, exc)
        state = {"compressed": False, "compress_error": str(exc)}
    finally:
        done.set()
    task_steps.settle(ctx.task_id_value, task_steps.COMPRESS, task_steps.compress_outcome(state))


def _note_can_start_before_the_cut(ctx: MediaJobContext) -> bool:
    """Whether the note may be written while the cut renders: the cut is
    decided from the transcript (``survey_cut``), and the note will be the
    picture note, which can read the original as the cut (``planned_cut_media``).
    The text note that stands in without Claude reads the transcript as stored,
    so it waits for the cut."""
    if not local_intake_flow.survey_first_enabled():
        return False
    try:
        if not local_intake_flow.note_is_wanted(ctx.task_id_value, ctx.client_id):
            return False
        return visual_note_channel.resolve_channel(resolve_secret(None, "anthropic_api_key")).available
    except Exception as exc:  # noqa: BLE001 - in turn is always safe
        logger.warning("could not tell whether the note can start early for %s: %s", ctx.task_id_value, exc)
        return False


async def _cut_and_note_side_by_side(ctx: MediaJobContext, hand_off: Any, **decision: Any) -> None:
    """Cut and note at the same time (to-do batch three, 2026-10-16).

    The model decides what to cut from the transcript; from that moment the
    note is written from the original read as the cut, while the cut renders.
    The note's few minutes of local work (finding its pictures) overlap the
    render; its long wait on Claude costs this machine nothing. The queue moves
    on once the render is done and the note's pictures are on disk.

    When nothing is to be cut, or the plan never came (the cut failed before
    it), the note waits for the cut and reads what it left, as before."""
    loop = asyncio.get_running_loop()
    planned: dict[str, Any] = {}
    plan_known = asyncio.Event()
    pictures_taken = asyncio.Event()

    def on_plan(ranges: Any) -> None:
        planned["ranges"] = ranges
        loop.call_soon_threadsafe(plan_known.set)

    def note_local_work_done() -> None:
        loop.call_soon_threadsafe(pictures_taken.set)

    cut = asyncio.ensure_future(_trim_recorded_tail(ctx, on_plan=on_plan, **decision))
    note: Optional[asyncio.Future] = None
    try:
        plan_wait = asyncio.ensure_future(plan_known.wait())
        await asyncio.wait({cut, plan_wait}, return_when=asyncio.FIRST_COMPLETED)
        plan_wait.cancel()
        if not planned.get("ranges"):
            await cut
            await _write_note_after_transcript(
                ctx.task_id_value, ctx.client_id, on_local_work_done=hand_off,
            )
            return
        note = asyncio.ensure_future(_write_note_after_transcript(
            ctx.task_id_value, ctx.client_id,
            on_local_work_done=note_local_work_done, planned_ranges=planned["ranges"],
        ))
        await cut
        pictures_wait = asyncio.ensure_future(pictures_taken.wait())
        await asyncio.wait({note, pictures_wait}, return_when=asyncio.FIRST_COMPLETED)
        pictures_wait.cancel()
        hand_off()
        await note
        # The cut did not come out after all: the task stays on the original,
        # and so does the note written for the cut.
        await loop.run_in_executor(None, functools.partial(
            _note_back_onto_original_if_uncut, ctx.task_id_value, ctx.client_id, planned["ranges"],
        ))
    except asyncio.CancelledError:
        for step in (cut, note):
            if step is not None and not step.done():
                step.cancel()
        raise


def _note_back_onto_original_if_uncut(task_id: str, client_id: Optional[str], ranges: Any) -> None:
    try:
        visual_note_job.put_back_onto_original(task_id, ranges, client_id=client_id)
    except Exception as exc:  # noqa: BLE001 - the note itself is the user's
        logger.warning("could not move the note of %s back onto the original: %s", task_id, exc)


async def _trim_recorded_tail(ctx: MediaJobContext, *, on_plan: Any = None, **cut: Any) -> None:
    """Cut the recording now that its transcript exists (``survey_cut``: a
    model reads the whole timeline, then one render), or, when it was cut
    before transcription, cut off what was recorded after the content ended
    (``tail_trim``). Never raises.

    ``on_plan`` (survey path only) gets the stretches the cut keeps as soon as
    they are decided, so the note can start while the cut renders."""
    if local_intake_flow.survey_first_enabled():
        work = functools.partial(survey_cut.survey_and_cut, ctx.task_id_value, ctx.client_id, on_plan=on_plan, **cut)
    else:
        work = functools.partial(tail_trim.trim_tail, ctx.task_id_value, ctx.client_id)
    task_steps.start(ctx.task_id_value, task_steps.CUT)
    try:
        await asyncio.get_running_loop().run_in_executor(None, _bound_to(ctx.task_id_value, work))
    except Exception as exc:  # noqa: BLE001 - the task's own results stand
        logger.warning("tail trim skipped for %s: %s", ctx.task_id_value, exc)
        task_steps.finish(ctx.task_id_value, task_steps.CUT, task_steps.FAILED, friendly_error(exc))
        return
    # A cut made before transcription (not the survey flow) recorded itself in
    # the pipeline; either way the result says what came of it.
    task_steps.settle(ctx.task_id_value, task_steps.CUT, task_steps.cut_outcome(
        get_job(ctx.task_id_value, client_id=ctx.client_id),
    ))


async def _deliver_to_output_folder(ctx: MediaJobContext) -> None:
    """Hand the task to ``output_folder`` to put the transcript, subtitles, cut
    list and note (md and PDF) of a recording from the user's own folder into
    its folder beside it. The cut video is already there.

    Queued, not awaited: exporting the PDF starts a browser, and the next task
    in line must not wait for it (on CI a second queued task timed out behind
    it, 2026-10-15). Never raises."""
    try:
        output_folder.schedule_sync(ctx.task_id_value)
    except Exception as exc:  # noqa: BLE001 - the task's own results stand
        logger.warning("output folder not scheduled for %s: %s", ctx.task_id_value, exc)


async def _feishu_step(ctx: Any) -> None:
    """The Feishu export as a step of the task: skipped unless asked for;
    otherwise what came of it, whether the pipeline exported the note it wrote
    or ``_export_note_written_after_pipeline`` exports the one written after."""
    task_id = ctx.task_id_value
    if not getattr(ctx, "do_lark", False):
        task_steps.finish(task_id, task_steps.FEISHU, task_steps.SKIPPED)
        return
    task_steps.start(task_id, task_steps.FEISHU)
    await _export_note_written_after_pipeline(ctx)
    task_steps.settle(task_id, task_steps.FEISHU, task_steps.feishu_outcome(
        get_job(task_id, client_id=ctx.client_id),
    ))


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
