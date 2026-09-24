"""The Mac stays awake from the first active task to the last, and no longer."""

from __future__ import annotations

import asyncio
import sys

from backend.core.job_event_hub import JobEventHub
from backend.core.local_keep_awake import KeepAwake


def _guard() -> KeepAwake:
    # A stand-in that lives until terminated, like caffeinate does.
    return KeepAwake(command=[sys.executable, "-c", "import time; time.sleep(60)"])


def test_one_hold_covers_overlapping_tasks_and_ends_with_the_last():
    guard = _guard()

    guard.acquire()
    first = guard._process
    guard.acquire()
    assert guard._process is first, "one process for the whole queue, not one per task"
    guard.release()
    assert guard.holding
    guard.release()

    assert not guard.holding
    assert first.poll() is not None


def test_a_machine_without_the_command_just_runs_the_tasks():
    guard = KeepAwake(command=[])

    guard.acquire()
    assert not guard.holding
    guard.release()
    assert guard.active == 0


def test_the_hub_holds_it_while_a_task_waits_or_runs_and_lets_go_after():
    guard = _guard()
    hub = JobEventHub(keep_awake=guard)
    seen: list[bool] = []

    async def scenario():
        gate = asyncio.Event()

        async def runner():
            seen.append(guard.holding)
            await gate.wait()

        async def failing():
            raise RuntimeError("the pipeline broke")

        await hub.start("a", runner)
        await hub.start("b", failing)
        await asyncio.sleep(0.05)
        still = guard.holding
        gate.set()
        await asyncio.gather(*hub._tasks.values(), return_exceptions=True)
        return still

    assert asyncio.run(scenario()) is True
    assert seen == [True]
    assert not guard.holding
    assert guard.active == 0
