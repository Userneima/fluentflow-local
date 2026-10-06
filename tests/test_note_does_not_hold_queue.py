"""The next recording starts while the previous note waits on the remote model.

Requirements, in the user's terms:

- In a batch, the minutes a note spends waiting on Claude use nothing on this
  machine, so the next recording's transcription starts during them.
- The note's own local work (picking frames out of the video) still happens in
  turn with other local work: running local jobs side by side on this machine
  was measured slower than running them one after another.
- A long batch does not have more than a few notes waiting on Claude at once.
- A note that fails before it gets to Claude does not leave the queue stuck.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from types import SimpleNamespace

import pytest

from backend.core import job_store
from backend.core.job_event_hub import JobEventHub
from backend.routers import local_processing as lp

_CREATED: list[str] = []


@pytest.fixture(autouse=True)
def _fresh_chain(monkeypatch):
    hub = JobEventHub()
    monkeypatch.setattr(lp, "JOB_EVENTS", hub)
    monkeypatch.setattr(lp, "_NOTE_REMOTE_SLOTS", threading.BoundedSemaphore(lp.NOTE_CONCURRENCY))
    lp._QUEUE_RECENT.clear()
    yield hub
    lp._QUEUE_RECENT.clear()
    # Waiting in the chain records a row; leave none behind looking like a task a
    # restart cut off.
    job_store.delete_jobs(_CREATED)
    _CREATED.clear()


def _ids(*names: str) -> list[str]:
    tag = uuid.uuid4().hex[:8]
    ids = [f"note-queue-{tag}-{n}" for n in names]
    _CREATED.extend(ids)
    return ids


class Flow:
    """Fake pipeline and note. Each note has two gates: its local frame work,
    then its remote wait. The log shows what overlapped."""

    def __init__(self, ids, *, fail_before_handoff=()):
        self.log: list[str] = []
        self.local_gate = {i: threading.Event() for i in ids}
        self.remote_gate = {i: threading.Event() for i in ids}
        self.fail_before_handoff = set(fail_before_handoff)
        self.remote_now = 0
        self.remote_peak = 0
        self._lock = threading.Lock()

    async def pipeline(self, ctx):
        self.log.append(f"pipeline:{ctx.task_id_value}")
        await asyncio.sleep(0.01)

    async def note(self, task_id, client_id, *, on_local_work_done=None):
        def run():
            self.log.append(f"frames:{task_id}")
            self.local_gate[task_id].wait(5)
            if task_id in self.fail_before_handoff:
                raise RuntimeError("no frames")
            on_local_work_done()
            with self._lock:
                self.remote_now += 1
                self.remote_peak = max(self.remote_peak, self.remote_now)
            self.log.append(f"remote:{task_id}")
            self.remote_gate[task_id].wait(5)
            with self._lock:
                self.remote_now -= 1
            self.log.append(f"note-done:{task_id}")

        try:
            await asyncio.to_thread(run)
        except RuntimeError:
            self.log.append(f"note-failed:{task_id}")


def _start(flow: Flow, ids, monkeypatch):
    monkeypatch.setattr(lp, "_run_pipeline", flow.pipeline)
    monkeypatch.setattr(lp, "_write_note_after_transcript", flow.note)
    runners = []
    for task_id in ids:
        previous = lp._queue_tail_barrier()
        done = asyncio.Event()
        lp._queue_tail_record(task_id, done)
        runners.append(asyncio.create_task(
            lp._run_serially(previous, done, SimpleNamespace(task_id_value=task_id, client_id=None))
        ))
    return runners


async def _until(predicate, tries=100):
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


def test_the_next_recording_starts_while_the_previous_note_waits_on_claude(monkeypatch):
    a, b = _ids("a", "b")
    flow = Flow([a, b])

    async def scenario():
        runners = _start(flow, [a, b], monkeypatch)
        flow.local_gate[a].set()
        assert await _until(lambda: f"pipeline:{b}" in flow.log), "B waited for A's note"
        assert f"note-done:{a}" not in flow.log
        for gate in [*flow.local_gate.values(), *flow.remote_gate.values()]:
            gate.set()
        await asyncio.gather(*runners)

    asyncio.run(scenario())


def test_the_next_recording_waits_while_the_previous_note_picks_frames(monkeypatch):
    a, b = _ids("a", "b")
    flow = Flow([a, b])

    async def scenario():
        runners = _start(flow, [a, b], monkeypatch)
        assert await _until(lambda: f"frames:{a}" in flow.log)
        await asyncio.sleep(0.2)
        assert f"pipeline:{b}" not in flow.log, "B ran beside A's local frame work"
        for gate in [*flow.local_gate.values(), *flow.remote_gate.values()]:
            gate.set()
        await asyncio.gather(*runners)

    asyncio.run(scenario())


def test_a_long_batch_has_at_most_a_few_notes_waiting_on_claude(monkeypatch):
    ids = _ids("a", "b", "c", "d", "e")
    flow = Flow(ids)

    async def scenario():
        runners = _start(flow, ids, monkeypatch)
        for gate in flow.local_gate.values():
            gate.set()
        await asyncio.sleep(0.5)
        waiting = flow.remote_now
        for gate in flow.remote_gate.values():
            gate.set()
        await asyncio.gather(*runners)
        return waiting

    assert asyncio.run(scenario()) == lp.NOTE_CONCURRENCY
    assert flow.remote_peak == lp.NOTE_CONCURRENCY
    assert all(f"note-done:{i}" in flow.log for i in ids)


def test_a_note_that_fails_before_claude_does_not_stick_the_queue(monkeypatch):
    a, b = _ids("a", "b")
    flow = Flow([a, b], fail_before_handoff=[a])

    async def scenario():
        runners = _start(flow, [a, b], monkeypatch)
        for gate in [*flow.local_gate.values(), *flow.remote_gate.values()]:
            gate.set()
        await asyncio.wait_for(asyncio.gather(*runners), 5)

    asyncio.run(scenario())
    assert f"note-failed:{a}" in flow.log
    assert f"note-done:{b}" in flow.log


def test_pipelines_still_run_one_at_a_time(monkeypatch):
    ids = _ids("a", "b", "c")
    flow = Flow(ids)
    running = {"now": 0, "peak": 0}

    async def counting(ctx):
        running["now"] += 1
        running["peak"] = max(running["peak"], running["now"])
        await asyncio.sleep(0.02)
        running["now"] -= 1

    async def scenario():
        runners = _start(flow, ids, monkeypatch)
        monkeypatch.setattr(lp, "_run_pipeline", counting)
        for gate in [*flow.local_gate.values(), *flow.remote_gate.values()]:
            gate.set()
        await asyncio.gather(*runners)

    asyncio.run(scenario())
    assert running["peak"] <= 1
