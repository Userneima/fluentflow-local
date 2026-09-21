"""How many jobs the local queue lets run at once.

The queue is a chain: each job waits on the event of one already handed out.
Which one it waits on is the whole setting — the predecessor makes it serial,
the job two places back lets two run together.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.routers import local_processing as lp


@pytest.fixture(autouse=True)
def _clean_queue():
    lp._QUEUE_RECENT.clear()
    lp._QUEUE_TAIL["task_id"] = None
    lp._QUEUE_TAIL["event"] = None
    yield
    lp._QUEUE_RECENT.clear()


def _hand_out(task_id: str):
    """What a submission does: read the barrier, then record itself."""
    barrier = lp._queue_tail_barrier()
    done = asyncio.Event()
    lp._queue_tail_record(task_id, done)
    return barrier, done


def test_the_first_jobs_up_to_the_limit_start_without_waiting():
    """A render is four ffmpeg processes and the transcription after it is one
    model on one core, so holding the second job back leaves most of the machine
    idle for half of every job."""
    barriers = [_hand_out(f"t{i}")[0] for i in range(lp.QUEUE_CONCURRENCY)]

    assert barriers == [None] * lp.QUEUE_CONCURRENCY


def test_the_next_job_waits_on_the_one_that_will_free_a_slot():
    for i in range(lp.QUEUE_CONCURRENCY):
        _hand_out(f"t{i}")

    barrier, _ = _hand_out("overflow")

    assert barrier is not None, "a job beyond the limit has to wait for a slot"
    waiting_for, _event = barrier
    assert waiting_for == "t0", (
        "it waits on the oldest job still in flight, not on the newest"
    )


def test_a_finished_job_stops_holding_anyone_back():
    """An event already set means that slot is free — waiting on it would be a
    queue that never drains after the first completion."""
    events = []
    for i in range(lp.QUEUE_CONCURRENCY):
        _, done = _hand_out(f"t{i}")
        events.append(done)
    events[0].set()

    barrier, _ = _hand_out("next")

    assert barrier is None


def test_the_chain_does_not_grow_with_the_archive():
    """This list is process-local and an archive run puts hundreds of jobs
    through it; only the ones somebody can still wait on are worth keeping."""
    for i in range(200):
        _hand_out(f"t{i}")

    assert len(lp._QUEUE_RECENT) <= lp.QUEUE_CONCURRENCY + 1


def test_concurrency_of_one_is_still_a_serial_queue(monkeypatch):
    """The setting has to be able to go back: one job at a time is what this was
    before, and what a smaller machine may still need."""
    monkeypatch.setattr(lp, "QUEUE_CONCURRENCY", 1)

    first, _ = _hand_out("a")
    second, _ = _hand_out("b")

    assert first is None
    assert second is not None and second[0] == "a"


def test_the_concurrency_setting_is_what_this_machine_was_measured_at():
    """The tests above all read QUEUE_CONCURRENCY, so they pass against any
    value — they check the mechanism, not the setting. This one pins the setting,
    because raising it is a memory decision and not a free one: measured at 2 on
    a 16GB machine, load went from ~50 to ~102 in fifteen minutes with swap
    nearly exhausted, which is the shape that precedes a tenfold slowdown."""
    assert lp.QUEUE_CONCURRENCY == 1
