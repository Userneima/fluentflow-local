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


def test_on_windows_a_holder_asks_the_system_to_stay_awake(monkeypatch):
    """10-18 review, item 8: Windows used to get nothing."""
    from backend.core import local_keep_awake as lka

    monkeypatch.setattr(lka.sys, "platform", "win32")
    command = KeepAwake()._resolved_command()
    assert command[-1] == lka.WINDOWS_HOLDER
    assert "SetThreadExecutionState(0x80000001)" in command[-1]


def test_the_windows_holder_ends_when_the_server_lets_go_of_its_pipe():
    """Its stdin is the server's: closed by a release, or by the server dying."""
    from backend.core import local_keep_awake as lka

    stand_in = lka.WINDOWS_HOLDER.replace(
        "ctypes.windll.kernel32.SetThreadExecutionState(0x80000001); ", "")
    guard = KeepAwake(command=[sys.executable, "-c", stand_in])
    guard.acquire()
    process = guard._process
    assert process.stdin is not None and guard.holding
    process.stdin.close()
    assert process.wait(timeout=5) == 0
