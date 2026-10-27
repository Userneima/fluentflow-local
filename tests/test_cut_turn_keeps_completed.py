"""A finished task waiting its turn to render its cut stays completed.

Requirements (owner, 2026-10-27):
- Once a task's transcript is stored it reads completed. When the agent has
  judged its cut and another job is ahead in the line, it waits for that job
  to render the cut; while it waits and after, the task still reads completed,
  so its note can be written and its runner does not file it as failed.
- A new submission waiting in the line still reads queued.

Measured 2026-10-27: two meetings submitted together were cut, then filed as
failed ("Worker exited without a terminal state") and refused note material.
"""

from __future__ import annotations

import asyncio

from backend.core import task_queue as tq


def _wire(monkeypatch):
    writes: list[str] = []
    ahead = asyncio.Event()
    monkeypatch.setattr(tq, "upsert_job", lambda **kw: writes.append(kw["status"]))
    monkeypatch.setattr(tq, "_queue_tail_barrier", lambda: ("ahead", ahead))
    monkeypatch.setattr(tq, "_queue_tail_record", lambda *_a: None)
    monkeypatch.setattr(tq, "_QUEUE_HEARTBEAT_SECONDS", 0.01)

    async def publish(*_a, **_k):
        return None

    async def running(_task_id):
        return True

    monkeypatch.setattr(tq.JOB_EVENTS, "publish", publish)
    monkeypatch.setattr(tq.JOB_EVENTS, "has_running_task", running)
    return writes, ahead


def test_a_finished_task_waiting_for_its_cut_turn_stays_completed(monkeypatch):
    writes, ahead = _wire(monkeypatch)

    async def scenario():
        turn = asyncio.create_task(tq._take_queue_turn("t", None))
        await asyncio.sleep(0.05)
        assert not turn.done()
        ahead.set()
        await asyncio.wait_for(turn, 1)

    asyncio.run(scenario())
    assert writes, "the wait was not recorded on the job"
    assert set(writes) == {"completed"}


def test_a_new_submission_waiting_in_line_reads_queued(monkeypatch):
    writes, ahead = _wire(monkeypatch)

    async def scenario():
        wait = asyncio.create_task(tq._await_predecessor(("ahead", ahead), "t", None))
        await asyncio.sleep(0.05)
        ahead.set()
        await asyncio.wait_for(wait, 1)

    asyncio.run(scenario())
    assert writes and set(writes) == {"queued"}
