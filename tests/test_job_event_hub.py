"""The live event hub forgets what nobody can use any more.

A job's progress history exists so a page that reconnects mid-job can catch
up. Once the job is over, only how it ended matters — and the process runs for
weeks with hundreds of jobs through it, so keeping every progress event of
every finished job is a leak with a slow fuse.
"""

from __future__ import annotations

import asyncio

from backend.core.job_event_hub import JobEventHub


def _sse_events(chunks: list[str]) -> list[dict]:
    import json

    return [json.loads(chunk[6:].strip()) for chunk in chunks]


def test_a_finished_job_keeps_only_how_it_ended_and_a_late_page_still_learns_it():
    hub = JobEventHub()

    async def scenario():
        for i in range(30):
            await hub.publish("t", {"stage": "stt", "progress": i})
        await hub.publish("t", {"stage": "done", "progress": 100, "result": {"ok": True}})
        cached = await hub.cached_events("t")
        late = [chunk async for chunk in hub.subscribe("t", since=0)]
        mid_way = [chunk async for chunk in hub.subscribe("t", since=12)]
        return cached, late, mid_way

    cached, late, mid_way = asyncio.run(scenario())

    assert len(cached) == 1
    assert cached[0]["stage"] == "done" and cached[0]["result"] == {"ok": True}
    assert cached[0]["event_index"] == 30, "the terminal event keeps its place in the sequence"
    assert [e["stage"] for e in _sse_events(late)] == ["done"]
    assert [e["stage"] for e in _sse_events(mid_way)] == ["done"], (
        "a page that had seen the first dozen events must still be told the ending"
    )


def test_a_failure_trims_the_same_way():
    hub = JobEventHub()

    async def scenario():
        for i in range(5):
            await hub.publish("t", {"stage": "render", "progress": i})
        await hub.publish("t", {"stage": "error", "progress": 100, "error": "boom"})
        return await hub.cached_events("t")

    cached = asyncio.run(scenario())

    assert [e["stage"] for e in cached] == ["error"]


def test_progress_history_is_kept_in_full_while_the_job_runs():
    hub = JobEventHub()

    async def scenario():
        for i in range(10):
            await hub.publish("t", {"stage": "stt", "progress": i})
        return await hub.cached_events("t")

    assert [e["event_index"] for e in asyncio.run(scenario())] == list(range(10))


def test_a_finished_runner_is_dropped_from_the_table_and_a_live_one_is_not():
    hub = JobEventHub()

    async def scenario():
        gate = asyncio.Event()

        async def waits():
            await gate.wait()

        async def ends():
            return None

        await hub.start("live", waits)
        await hub.start("over", ends)
        await asyncio.sleep(0.01)
        while_live = (hub.is_running("live"), await hub.has_running_task("live"), hub.is_running("over"), "over" in hub._tasks)
        gate.set()
        await asyncio.sleep(0.01)
        return while_live, hub.is_running("live"), "live" in hub._tasks

    while_live, live_after, kept_after = asyncio.run(scenario())

    assert while_live == (True, True, False, False)
    assert live_after is False and kept_after is False
