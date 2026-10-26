"""Stopping the step a task is on.

Cancelling a task's coroutine stops what runs on the event loop, but a step's
real work runs elsewhere: ffmpeg encodes, the transcription process, a Claude
command line, all started from worker threads. Each run of a task gets a stop
event here (``begin``); work started for the task watches it (a ``TaskWatch``
built on it kills the ffmpeg it runs), and the Claude command lines a thread
starts while bound to the task (``bound``) are registered and killed when the
task is stopped (``request``).

A request already sent to a model over HTTP cannot be called back: its answer
is dropped, and the step waits for nothing.

When the service itself stops (``shutdown``), every task's work is stopped the
same way, but the steps are not recorded as stopped: from then on
``task_steps`` leaves step records as they are, so the step a task was on is
still open after the restart and is carried on from there (decision 6.42).
Without this, the encodes of a cut kept running after the service had gone and
the restarted service rendered the same cut again beside them (measured
2026-10-25).
"""

from __future__ import annotations

import subprocess
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from backend.core import process_tree

_lock = threading.Lock()
_events: dict[str, threading.Event] = {}
_processes: dict[str, set[subprocess.Popen]] = {}
_bound = threading.local()
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


def requested(task_id: str) -> bool:
    with _lock:
        found = _events.get(task_id)
    return bool(found and found.is_set())


@contextmanager
def bound(task_id: str) -> Iterator[None]:
    """Run the block (on a worker thread) as work of this task."""
    previous = getattr(_bound, "task_id", None)
    _bound.task_id = task_id
    try:
        yield
    finally:
        _bound.task_id = previous


def current() -> str | None:
    """The task the calling thread works for, if it is bound to one."""
    return getattr(_bound, "task_id", None)


def bound_runner() -> Any:
    """A command runner that stops with the bound task (``TaskWatch``), or
    None on a thread not bound to one."""
    task_id = current()
    if not task_id:
        return None
    from backend.core.task_watch import TaskWatch

    return TaskWatch(cancelled=event(task_id)).runner


def track(process: subprocess.Popen) -> None:
    task_id = getattr(_bound, "task_id", None)
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


def service_started() -> None:
    """A new start of the service: step records are written again."""
    _shutting_down.clear()


def shutting_down() -> bool:
    return _shutting_down.is_set()


__all__ = ["begin", "bound", "bound_runner", "current", "event", "request", "requested", "service_started", "shutdown", "shutting_down", "track", "untrack"]
