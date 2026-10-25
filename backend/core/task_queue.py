"""The processing queue: which task runs now and which wait behind it.

Every entry that runs the pipeline puts its task on this chain
(``start_behind_queue``), and the entries that cannot join it (a manual
de-breath, a note rewritten on a finished task) ask ``queue_is_busy`` and
refuse while it works. The state is process-local, like the event hub.
"""

from __future__ import annotations

import asyncio
import functools
import time
from datetime import datetime, timezone
from typing import Any, Optional

from backend.core import device_profile, task_steps
from backend.core.event_context import event_metadata
from backend.core.event_logger import log_event
from backend.core.job_store import upsert_job
from backend.core.local_entry_guards import run_worker_with_terminal_state
from backend.core.local_job_runtime import JOB_EVENTS


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
QUEUE_CONCURRENCY = device_profile.queue_concurrency()

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


async def _take_queue_turn(task_id: str, client_id: Optional[str]) -> asyncio.Event:
    """A new place at the end of the line for a task already running (its
    runner is alive under this task id, so the jobs behind it wait for it);
    returns once it is this task's turn. Set the event to move the line on."""
    previous = _queue_tail_barrier()
    done = asyncio.Event()
    _queue_tail_record(task_id, done)
    if previous is not None:
        await _await_predecessor(previous, task_id, client_id)
    return done


async def _start_behind_queue(
    *,
    task_id: str,
    client_id: Optional[str],
    route: str,
    stage: str,
    chained_worker: Any,
    gate: Any = None,
    plan_steps: bool = True,
) -> bool:
    """Put a job on the chain and start its runner; say whether one started.

    ``chained_worker(previous, done)`` is the job: it must wait its turn with
    ``wait_for_queue_turn(previous, ...)`` before the expensive part and set
    ``done`` when it is over, however it ends.
    """
    # Barrier and record happen together, before the first await, so two
    # submissions arriving at once cannot both take the same job as the one
    # they wait for.
    if plan_steps and not task_steps.steps_of([task_id]).get(task_id):
        # Recorded from the moment the task is queued, so a restart before its
        # turn carries it on in place (``task_actions.resume_after_restart``).
        task_steps.plan(task_id)
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
