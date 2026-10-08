"""The next recording starts while the previous note waits on the remote model.

Requirements, in the user's terms:

- In a batch, the minutes a note spends waiting on Claude use nothing on this
  machine, so the next recording's transcription starts during them.
- The note's own local work (picking frames out of the video) still happens in
  turn with other local work: running local jobs side by side on this machine
  was measured slower than running them one after another.
- A long batch does not have more than a few notes waiting on Claude at once.
- A note that fails before it gets to Claude does not leave the queue stuck.
- Notes waiting for their turn with Claude do not tie up the threads uploads
  and the pipeline use, however many short recordings finish at once.
- Cancelling a task whose note is waiting for a turn gives that turn up; a note
  already talking to Claude keeps its turn until it really stops, so the
  number of notes with Claude never goes past the limit.
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
    monkeypatch.setattr(lp, "_NOTE_SLOT_POLL_SECONDS", 0.02)
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

    def write_note(self, task_id, client_id, *, on_local_work_done=None):
        """Stands in for ``local_intake_flow.write_note``: blocking, on the note thread."""
        self.log.append(f"frames:{task_id}")
        self.local_gate[task_id].wait(5)
        if task_id in self.fail_before_handoff:
            self.log.append(f"note-failed:{task_id}")
            return
        try:
            on_local_work_done()
        except lp.NoteAbandoned:
            self.log.append(f"note-abandoned:{task_id}")
            raise
        with self._lock:
            self.remote_now += 1
            self.remote_peak = max(self.remote_peak, self.remote_now)
        self.log.append(f"remote:{task_id}")
        self.remote_gate[task_id].wait(5)
        with self._lock:
            self.remote_now -= 1
        self.log.append(f"note-done:{task_id}")


def _start(flow: Flow, ids, monkeypatch):
    monkeypatch.setattr(lp, "_run_pipeline", flow.pipeline)
    monkeypatch.setattr(lp.local_intake_flow, "note_is_wanted", lambda *_a: True)
    monkeypatch.setattr(lp.local_intake_flow, "mark_note_running", lambda *_a: None)
    monkeypatch.setattr(lp.local_intake_flow, "_patch_result", lambda *_a, **_k: None)
    monkeypatch.setattr(lp.local_intake_flow, "write_note", flow.write_note)
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


def _slots_free():
    # BoundedSemaphore keeps its count here; a leak shows as a count below the limit.
    return lp._NOTE_REMOTE_SLOTS._value


def test_waiting_notes_do_not_starve_the_threads_uploads_use(monkeypatch):
    """Requirement: with every Claude turn taken and more notes waiting, other
    work that runs on a background thread (an upload being saved, a preflight)
    still starts at once."""
    ids = _ids("a", "b", "c", "d")
    flow = Flow(ids)

    async def scenario():
        from concurrent.futures import ThreadPoolExecutor
        # A small default pool makes starvation show quickly: four notes parked
        # in it would leave nothing for anyone else.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=2))
        runners = _start(flow, ids, monkeypatch)
        for gate in flow.local_gate.values():
            gate.set()
        assert await _until(lambda: all(f"frames:{i}" in flow.log for i in ids))
        assert await _until(lambda: flow.remote_now == lp.NOTE_CONCURRENCY)
        answer = await asyncio.wait_for(asyncio.to_thread(lambda: "saved"), 1)
        for gate in flow.remote_gate.values():
            gate.set()
        await asyncio.gather(*runners)
        return answer

    assert asyncio.run(scenario()) == "saved"


def test_cancelling_a_note_waiting_for_its_turn_gives_the_turn_back(monkeypatch):
    """Requirement: cancel a task while its note waits for Claude, and the
    notes after it still get both turns."""
    a, b, c, d = _ids("a", "b", "c", "d")
    flow = Flow([a, b, c, d])

    async def scenario():
        runners = _start(flow, [a, b, c, d], monkeypatch)
        for gate in flow.local_gate.values():
            gate.set()
        # a and b are with Claude; c and d wait for a turn.
        assert await _until(lambda: flow.remote_now == 2 and f"frames:{d}" in flow.log)
        runners[2].cancel()
        assert await _until(lambda: f"note-abandoned:{c}" in flow.log)
        for gate in flow.remote_gate.values():
            gate.set()
        await asyncio.gather(*runners, return_exceptions=True)

    asyncio.run(scenario())
    assert f"remote:{c}" not in flow.log
    assert f"note-done:{d}" in flow.log
    assert _slots_free() == lp.NOTE_CONCURRENCY


def test_cancelling_a_note_already_with_claude_keeps_its_turn_until_it_stops(monkeypatch):
    """Requirement: a cancelled task whose note is already talking to Claude
    does not let another note start beside it; the turn is given back when
    that note's thread really ends."""
    a, b, c = _ids("a", "b", "c")
    flow = Flow([a, b, c])

    async def scenario():
        runners = _start(flow, [a, b, c], monkeypatch)
        for gate in flow.local_gate.values():
            gate.set()
        assert await _until(lambda: flow.remote_now == 2 and f"frames:{c}" in flow.log)
        runners[0].cancel()
        await asyncio.sleep(0.3)
        assert f"remote:{c}" not in flow.log, "c started while a still held its turn"
        flow.remote_gate[a].set()
        assert await _until(lambda: f"remote:{c}" in flow.log)
        for gate in flow.remote_gate.values():
            gate.set()
        await asyncio.gather(*runners, return_exceptions=True)
        assert await _until(lambda: _slots_free() == lp.NOTE_CONCURRENCY)

    asyncio.run(scenario())
    assert flow.remote_peak == lp.NOTE_CONCURRENCY
