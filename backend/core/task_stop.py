"""Stopping the step a task is on.

Cancelling a task's coroutine stops what runs on the event loop, but a step's
real work runs elsewhere: ffmpeg encodes, the transcription process, a Claude
command line, all started from worker threads. Each run of a task gets a stop
event here (``begin``); work started for the task watches it (a ``TaskWatch``
built on it kills the ffmpeg it runs), and the Claude command lines a thread
starts while bound to the task (``bound``) are registered and killed when the
task is stopped (``request``).

A request already sent to a model over HTTP cannot be called back: the thread
waiting for it stops waiting (``model_usage.unless_stopping``) and raises
``StepStopped``, nothing more is sent for the step, and an answer that still
comes back is kept for the run that carries the step on (``model_answers``).
The binding is a context variable, so work handed to another thread through
``model_usage.carry`` (a note's chapters, its coverage check, the request
itself) is the task's work too.

When the service itself stops (``shutdown``), every task's work is stopped the
same way, but the steps are not recorded as stopped: from then on
``task_steps`` leaves step records as they are, so the step a task was on is
still open after the restart and is carried on from there (decision 6.42).
Without this, the encodes of a cut kept running after the service had gone and
the restarted service rendered the same cut again beside them (measured
2026-10-25).
"""

from __future__ import annotations

import asyncio
import contextvars
import subprocess
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from backend.core import process_tree

_lock = threading.Lock()
_events: dict[str, threading.Event] = {}
_processes: dict[str, set[subprocess.Popen]] = {}
_bound: contextvars.ContextVar[str | None] = contextvars.ContextVar("task_stop_bound", default=None)
_shutting_down = threading.Event()


def begin(task_id: str) -> threading.Event:
    """A fresh stop event for a new run of this task. Work from an earlier run
    keeps the event it was given, so a stop it saw stays seen."""
    event = threading.Event()
    with _lock:
        _events[task_id] = event
    return event


def event(task_id: str) -> threading.Event:
    with _lock:
        found = _events.get(task_id)
        if found is None:
            found = _events[task_id] = threading.Event()
        return found


class Either:
    """Set when any of its events is: a step's own stop (its coroutine was
    cancelled) or the task's (the user stopped the step, the service is
    stopping). Read like a ``threading.Event``."""

    def __init__(self, *events: threading.Event) -> None:
        self._events = events

    def is_set(self) -> bool:
        return any(found.is_set() for found in self._events)

    def set(self) -> None:
        self._events[0].set()

    def wait(self, timeout: float | None = None) -> bool:
        import time

        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True


def either(task_id: str, own: threading.Event) -> Either:
    """``own``, or this task's stop event: what a step running on worker
    threads watches so that stopping the task stops it too."""
    return Either(own, event(task_id))


def requested(task_id: str) -> bool:
    with _lock:
        found = _events.get(task_id)
    return bool(found and found.is_set())


class StepStopped(asyncio.CancelledError):
    """The user stopped this task's step (``request``), and the code that
    raised it stopped: a request not sent, an answer not waited for, a part
    of the note not written. A cancellation, not a failure, so the
    ``except Exception`` that turns a failed request into a failed note lets
    it through; unlike ``model_usage.ServiceStopping`` the step is recorded
    as stopped (已停下) and waits for 接着做."""


@contextmanager
def bound(task_id: str) -> Iterator[None]:
    """Run the block (on a worker thread) as work of this task."""
    token = _bound.set(task_id)
    try:
        yield
    finally:
        _bound.reset(token)


def current() -> str | None:
    """The task the calling thread works for, if it is bound to one."""
    return _bound.get()


def stopped_here() -> bool:
    """Whether the user stopped the step of the task the calling thread works
    for. Only bound work asks: a rewrite asked for on request is not bound,
    and the stop of an earlier run must not reach it."""
    task_id = current()
    return bool(task_id) and requested(task_id)


def bound_runner() -> Any:
    """A command runner that stops with the bound task (``TaskWatch``), or
    None on a thread not bound to one."""
    task_id = current()
    if not task_id:
        return None
    from backend.core.task_watch import TaskWatch

    return TaskWatch(cancelled=event(task_id)).runner


def track(process: subprocess.Popen) -> None:
    task_id = current()
    if not task_id:
        return
    with _lock:
        _processes.setdefault(task_id, set()).add(process)
        stop = _events.get(task_id)
    if stop is not None and stop.is_set():
        process_tree.kill_tree(process)


def untrack(process: subprocess.Popen) -> None:
    with _lock:
        for running in _processes.values():
            running.discard(process)


def request(task_id: str) -> int:
    """Stop this task's current step: set its event and kill the command lines
    its threads started. Returns how many were killed."""
    with _lock:
        stop = _events.get(task_id)
        if stop is None:
            stop = _events[task_id] = threading.Event()
        stop.set()
        processes = list(_processes.get(task_id, ()))
    killed = 0
    for process in processes:
        if process.poll() is None:
            process_tree.kill_tree(process)
            killed += 1
    return killed


def shutdown() -> int:
    """The service is stopping: stop every task's work and leave its steps
    as they are. Returns how many command lines were killed."""
    _shutting_down.set()
    with _lock:
        task_ids = list(_events)
    return sum(request(task_id) for task_id in task_ids)


def stop_on_signals(then: Any = None) -> None:
    """Count the service as stopping the moment it is told to stop.

    The server waits for running requests (an agent's note written in a
    request's background task, a rewrite awaited in the request) before it
    runs the app's shutdown, and skips that shutdown on a second Ctrl+C; a
    model request waiting on ``shutting_down`` would hold the exit for the
    whole note (found in a review, 2026-11-25). So the signal handlers the
    server installed are wrapped to raise the flag first. Called from the
    app's startup, on the main thread, after the server installed them; the
    server puts back its own originals when it ends.

    ``then`` (stopping the running work: command lines, note writers) runs
    right after, on a thread of its own: a note written by the Claude command
    line is waited for by the server too, and is stopped only by killing it
    (found in the second review, 2026-11-25). Not in the handler itself: the
    locks it takes may be held by the code the signal interrupted.
    """
    import signal

    if threading.current_thread() is not threading.main_thread():
        return
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        previous = signal.getsignal(sig) if sig is not None else None
        if not callable(previous):
            continue

        def handler(signum: int, frame: Any, previous: Any = previous) -> None:
            if not _shutting_down.is_set():
                _shutting_down.set()
                if then is not None:
                    threading.Thread(target=then, name="stop-on-signal", daemon=True).start()
            previous(signum, frame)

        signal.signal(sig, handler)


def service_started() -> None:
    """A new start of the service: step records are written again."""
    _shutting_down.clear()


def shutting_down() -> bool:
    return _shutting_down.is_set()


__all__ = ["Either", "StepStopped", "either", "begin", "bound", "bound_runner", "current", "event", "request", "requested", "service_started", "shutdown", "shutting_down", "stop_on_signals", "stopped_here", "track", "untrack"]
