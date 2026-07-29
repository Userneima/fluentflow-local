"""In-memory fan-out hub for live job progress events (SSE)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def event_from_sse_chunk(chunk: str) -> dict[str, Any] | None:
    """Read one structured job event from a pipeline SSE chunk.

    The event hub is the owner of the wire format, so pipeline runners do not
    need the hosted helper barrel just to replay their own progress output.
    """
    for line in chunk.splitlines():
        if line.startswith("data: "):
            try:
                payload = json.loads(line[6:])
            except json.JSONDecodeError:
                return None
            return payload if isinstance(payload, dict) else None
    return None


class JobEventHub:
    """In-memory fan-out for live job progress events.

    Durable completion state lives in the SQLite job store. This hub only keeps
    recent live events so clients can disconnect and resubscribe without
    cancelling the underlying processing task.
    """

    def __init__(self, max_events_per_job: int = 500) -> None:
        self.max_events_per_job = max_events_per_job
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, task_id: str, event: dict[str, Any]) -> None:
        if not task_id:
            return
        async with self._lock:
            history = self._events.setdefault(task_id, [])
            payload = dict(event)
            payload["event_index"] = len(history)
            history.append(payload)
            if len(history) > self.max_events_per_job:
                del history[: len(history) - self.max_events_per_job]
                for index, item in enumerate(history):
                    item["event_index"] = index
            subscribers = list(self._subscribers.get(task_id, set()))
        for queue in subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    async def start(self, task_id: str, runner: Any) -> None:
        async with self._lock:
            existing = self._tasks.get(task_id)
            if existing and not existing.done():
                return
            self._tasks[task_id] = asyncio.create_task(runner())

    async def subscribe(self, task_id: str, since: int = 0) -> AsyncGenerator[str, None]:
        from backend.core.job_store import get_job

        cached = await self.cached_events(task_id)
        start = max(0, int(since or 0))
        for event in cached[start:]:
            yield _sse(event)
            if self.is_terminal(event):
                return

        job = get_job(task_id)
        if job and job.get("status") in {"completed", "failed", "cancelled"}:
            terminal = self.event_from_job(job)
            await self.publish(task_id, terminal)
            yield _sse(terminal)
            return

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.setdefault(task_id, set()).add(queue)
        try:
            while True:
                event = await queue.get()
                yield _sse(event)
                if self.is_terminal(event):
                    return
        finally:
            async with self._lock:
                subscribers = self._subscribers.get(task_id)
                if subscribers:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(task_id, None)

    async def cached_events(self, task_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            return list(self._events.get(task_id, []))

    async def has_running_task(self, task_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            return bool(task and not task.done())

    async def cancel(self, task_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
        if not task or task.done():
            return False
        task.cancel()
        return True

    @staticmethod
    def is_terminal(event: dict[str, Any]) -> bool:
        return event.get("stage") in {"done", "error"}

    @staticmethod
    def event_from_job(job: dict[str, Any]) -> dict[str, Any]:
        status = job.get("status")
        result = job.get("result")
        if status == "completed" or result:
            return {"stage": "done", "progress": 100, "result": result or {}}
        return {
            "stage": "error",
            "progress": job.get("progress") or 0,
            "error": job.get("error_reason") or f"Job {status or 'failed'}",
        }
