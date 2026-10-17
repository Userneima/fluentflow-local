"""Speaker separation runs in a child process the service can stop.

Requirements (2026-10-17): one separation run held the service for about 16
hours, so the page and agents got no answer. A run past its limit, or whose
task is cancelled, is stopped for real; the service keeps answering while it
runs; whatever goes wrong inside comes back as a reason, never a hang.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

import diarization_workers as workers
from backend.core.diarization_process import diarize_in_child


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pid(audio: Path) -> int:
    marker = Path(str(audio) + ".pid")
    for _ in range(100):
        if marker.is_file() and marker.read_text():
            return int(marker.read_text())
        time.sleep(0.05)
    raise AssertionError("the child never started")


def test_the_turns_come_back_from_the_child(tmp_path):
    turns = asyncio.run(diarize_in_child(tmp_path / "a.wav", budget_seconds=30, worker=workers.answers))
    assert [(t.start, t.end, t.speaker) for t in turns] == [(0.0, 1.5, "SPEAKER_00"), (1.5, 3.0, "SPEAKER_01")]


def test_a_run_past_its_limit_is_stopped_not_left_running(tmp_path):
    audio = tmp_path / "a.wav"

    async def scenario():
        with pytest.raises(asyncio.TimeoutError):
            await diarize_in_child(audio, budget_seconds=1.5, worker=workers.hangs)

    asyncio.run(scenario())
    assert not _alive(_pid(audio))


def test_a_cancelled_task_stops_the_child(tmp_path):
    audio = tmp_path / "a.wav"

    async def scenario():
        run = asyncio.create_task(diarize_in_child(audio, budget_seconds=60, worker=workers.hangs))
        await asyncio.sleep(0.2)
        pid = await asyncio.get_running_loop().run_in_executor(None, _pid, audio)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        return pid

    pid = asyncio.run(scenario())
    for _ in range(40):
        if not _alive(pid):
            break
        time.sleep(0.05)
    assert not _alive(pid)


def test_the_service_keeps_answering_while_the_child_works(tmp_path):
    audio = tmp_path / "a.wav"

    async def scenario():
        run = asyncio.create_task(diarize_in_child(audio, budget_seconds=1.5, worker=workers.hangs))
        ticks = 0
        while not run.done():
            await asyncio.sleep(0.05)
            ticks += 1
        with pytest.raises(asyncio.TimeoutError):
            await run
        return ticks

    assert asyncio.run(scenario()) >= 15


def test_a_failure_inside_comes_back_as_its_reason(tmp_path):
    with pytest.raises(RuntimeError, match="模型文件缺了"):
        asyncio.run(diarize_in_child(tmp_path / "a.wav", budget_seconds=30, worker=workers.fails))


def test_a_child_that_dies_says_so(tmp_path):
    with pytest.raises(RuntimeError, match="意外退出"):
        asyncio.run(diarize_in_child(tmp_path / "a.wav", budget_seconds=30, worker=workers.dies))
