"""A re-render is taken once per task until it has finished.

Requirement (review, 2026-10-16): two quick clicks, or the same report open
in two windows, start one re-render; the second is told to wait. The task is
held until the render, the report and the note are done, and is free again
afterwards, also when the work failed.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import BackgroundTasks

from backend.core import debreath_job, job_store
from backend.core import task_actions as route


@pytest.fixture
def task(monkeypatch):
    task_id = f"rr-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status="completed", result={"note_declined": True})
    monkeypatch.setattr(route.original_locator, "locate", lambda *a, **k: None)
    monkeypatch.setattr(route, "queue_is_busy", lambda: False)
    yield task_id
    debreath_job.release(task_id)
    job_store.delete_jobs([task_id])


def _run(tasks: BackgroundTasks) -> None:
    for job in tasks.tasks:
        job.func(*job.args, **job.kwargs)


def test_a_second_click_before_the_first_ends_is_refused(task):
    seen = []
    first = BackgroundTasks()
    route._accept_rerender(task, None, first.add_task, lambda claimed: seen.append(claimed))

    with pytest.raises(route.Refused) as refused:
        route._accept_rerender(task, None, BackgroundTasks().add_task, lambda claimed: seen.append(claimed))
    assert refused.value.status == 409

    _run(first)
    assert seen == [True], "the work runs once, holding the render slot"
    again = BackgroundTasks()
    route._accept_rerender(task, None, again.add_task, lambda claimed: None)
    _run(again)


def test_a_failed_rerender_frees_the_task(task):
    def broken(claimed):
        raise RuntimeError("disk full")

    tasks = BackgroundTasks()
    route._accept_rerender(task, None, tasks.add_task, broken)
    _run(tasks)

    later = BackgroundTasks()
    route._accept_rerender(task, None, later.add_task, lambda claimed: None)
    _run(later)
    assert task not in debreath_job.running_task_ids()
