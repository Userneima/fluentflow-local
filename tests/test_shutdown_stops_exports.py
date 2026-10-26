"""Stopping the service stops the commands its tasks started, and the steps
they were on are carried on after the restart.

Requirements (to-do「停服务时剪辑的 FFmpeg 不跟着停」, decision 6.42, owner
2026-10-26):

- When the service stops (Ctrl+C in its terminal, or the app quitting it),
  every encode a task's step started stops with it. Measured 2026-10-25: three
  ffmpeg processes of an interrupted cut kept running after the service had
  stopped, and the restarted service rendered the same cut again beside them.
- The step that was cut off is not recorded as stopped or failed: after the
  restart the task is carried on from that step, as it is when the service is
  killed outright.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid

import pytest

from backend.core import job_store, task_actions, task_steps, task_stop
from backend.core.task_watch import TaskCancelled


@pytest.fixture(autouse=True)
def _service_running(monkeypatch):
    # Each test starts with a running service; shutting it down here must not
    # leak into the rest of the suite.
    monkeypatch.setattr(task_stop, "_shutting_down", threading.Event())
    monkeypatch.setattr(task_stop, "_events", {})
    monkeypatch.setattr(task_stop, "_processes", {})


def _task() -> str:
    return f"shutdown-{uuid.uuid4().hex[:8]}"


_SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]


def _run_bound(task_id: str, outcome: dict) -> threading.Thread:
    """An encode a task's step runs on a worker thread, through the runner
    that stops with the task (what the cut render uses)."""
    def work() -> None:
        with task_stop.bound(task_id):
            runner = task_stop.bound_runner()
            try:
                runner(_SLEEP)
                outcome[task_id] = "finished"
            except TaskCancelled:
                outcome[task_id] = "stopped"

    thread = threading.Thread(target=work)
    thread.start()
    return thread


def test_stopping_the_service_stops_every_tasks_encodes():
    first, second = _task(), _task()
    task_stop.begin(first)
    task_stop.begin(second)
    outcome: dict[str, str] = {}
    threads = [_run_bound(first, outcome), _run_bound(second, outcome)]
    time.sleep(0.5)

    started = time.monotonic()
    task_stop.shutdown()
    for thread in threads:
        thread.join(timeout=5)

    assert outcome == {first: "stopped", second: "stopped"}
    assert time.monotonic() - started < 3


def test_stopping_the_service_kills_the_command_lines_a_task_started():
    task_id = _task()
    task_stop.begin(task_id)
    process = subprocess.Popen(_SLEEP)
    with task_stop.bound(task_id):
        task_stop.track(process)

    killed = task_stop.shutdown()

    assert killed == 1
    assert process.wait(timeout=5) is not None


def test_the_step_cut_off_by_the_shutdown_is_carried_on_after_the_restart():
    task_id = _task()
    job_store.upsert_job(task_id=task_id, status="completed", result={"task_id": task_id, "transcript_text": "x"})
    task_steps.plan(task_id)
    for name in task_steps.BEFORE_TRANSCRIPT:
        task_steps.finish(task_id, name, task_steps.DONE)
    task_steps.start(task_id, task_steps.CUT)

    task_stop.shutdown()
    # What the cut's flow does once its encode is stopped under it.
    task_steps.finish(task_id, task_steps.CUT, task_steps.FAILED, "任务已取消")
    task_steps.close_open(task_id, stopped=True)

    statuses = task_steps.statuses(task_steps.steps_of([task_id]).get(task_id, []))
    assert statuses[task_steps.CUT] == task_steps.RUNNING
    assert statuses[task_steps.NOTE] == task_steps.PENDING
    assert task_id in task_actions.stepped_tasks_cut_off()


def test_while_the_service_runs_a_stop_is_recorded_as_before():
    task_id = _task()
    job_store.upsert_job(task_id=task_id, status="completed", result={"task_id": task_id})
    task_steps.plan(task_id)
    task_steps.start(task_id, task_steps.CUT)

    task_steps.close_open(task_id, stopped=True)

    statuses = task_steps.statuses(task_steps.steps_of([task_id]).get(task_id, []))
    assert statuses[task_steps.CUT] == task_steps.STOPPED


def test_the_service_stops_its_tasks_encodes_when_it_shuts_down(monkeypatch):
    from fastapi.testclient import TestClient

    from backend import local_main

    calls: list[str] = []
    real = task_stop.shutdown
    monkeypatch.setattr(task_stop, "shutdown", lambda: calls.append("shutdown") or real())
    monkeypatch.setattr(task_steps, "interrupted", lambda: [])

    async def nothing_to_rerun(_task_ids):
        return 0

    monkeypatch.setattr(local_main, "resume_interrupted_jobs", nothing_to_rerun)

    with TestClient(local_main.create_local_app()):
        assert calls == []
    assert calls == ["shutdown"]


def test_a_stopped_task_takes_no_more_pictures():
    """Picking a note's pictures runs ffmpeg once per frame; once the task is
    stopped (by the user, or by the service stopping) no further frame is
    taken, so the step ends at once instead of after every frame."""
    from backend.core import frame_extractor

    task_id = _task()
    task_stop.begin(task_id)
    ran: list[list[str]] = []

    with task_stop.bound(task_id):
        frame_extractor._run([sys.executable, "-c", "pass"], check=True)
        ran.append(["first"])
        task_stop.shutdown()
        with pytest.raises(TaskCancelled):
            frame_extractor._run([sys.executable, "-c", "pass"], check=True)
    # Work not bound to a task is not affected.
    frame_extractor._run([sys.executable, "-c", "pass"], check=True)
    assert ran == [["first"]]
