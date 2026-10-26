"""A service started on a copy of the owner's task database leaves his real
work alone.

Requirements (review of decision 8.11, 2026-10-26), in the user's terms:

- Starting a service on the copy carries nothing on and queues nothing: the
  copied tasks may still be running in the real service, and carrying them on
  (or re-running them from the original as a new task) would spend model
  calls and write beside the real recordings.
- The copied tasks are read-only there: any request that would change one
  (cut again, rewrite the note, restore a version, delete it, a review note
  beside it) is refused with 「这是测试副本里的旧任务，只能看。」, for the page
  and agents alike. Tasks made in the copy are worked on as usual.
- Nothing on the copy writes into a copied task's output folder, pushes to or
  pulls from Feishu, or exports to Feishu.
- The page and ``/health`` say the service runs on a test copy.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.core import feishu_sync, job_store, output_folder, review_note, task_actions, task_flow, task_steps
from backend.core import test_copy, workflow_status


@pytest.fixture
def copy_of(monkeypatch, tmp_path):
    """Turns the switch on with these tasks listed as copied."""

    def turn_on(*task_ids: str) -> None:
        monkeypatch.setenv(test_copy.ENV, "1")
        monkeypatch.setenv("FLUENTFLOW_DATA_DIR", str(tmp_path))
        (tmp_path / test_copy.TASKS_FILE).write_text(json.dumps(list(task_ids)), encoding="utf-8")
        test_copy.reset()

    yield turn_on
    test_copy.reset()


def _task(status: str = "running", *, folder: Path | None = None) -> str:
    task_id = f"copy-{uuid.uuid4().hex[:8]}"
    metadata = {"folder_intake": {"original_path": str(folder / "lecture.mp4")}} if folder else {}
    job_store.upsert_job(task_id=task_id, status=status, stage="transcribing", progress=40,
                         result={"task_id": task_id, "transcript_text": "大家好", "summary_markdown": "# 笔记"},
                         metadata=metadata)
    return task_id


# ── startup carries nothing on ─────────────────────────────────────────────

def _start_service(monkeypatch, cut_off: list[str]) -> dict[str, list]:
    from backend import local_main

    asked: dict[str, list] = {"carried_on": [], "rerun_as_new": [], "agent_cuts": []}

    async def carry_on(task_ids):
        asked["carried_on"].extend(task_ids)
        return 0

    async def rerun_as_new(task_ids):
        asked["rerun_as_new"].extend(task_ids)
        return 0

    async def agent_cuts():
        asked["agent_cuts"].append(True)
        return 0

    monkeypatch.setattr(task_steps, "interrupted", lambda: list(cut_off))
    monkeypatch.setattr(task_actions, "resume_after_restart", carry_on)
    monkeypatch.setattr(local_main, "resume_interrupted_jobs", rerun_as_new)
    monkeypatch.setattr(task_flow, "resume_agent_cuts", agent_cuts)
    with TestClient(local_main.create_local_app()):
        time.sleep(0.2)
    return asked


def test_a_service_on_the_copy_carries_on_and_queues_nothing(monkeypatch, copy_of):
    stepped = _task()
    task_steps.plan(stepped)
    task_steps.start(stepped, task_steps.TRANSCRIBE)
    unstepped = _task()
    copy_of(stepped, unstepped)
    before = len(job_store.list_jobs(limit=None, include_result=False))

    asked = _start_service(monkeypatch, [stepped])

    assert asked == {"carried_on": [], "rerun_as_new": [], "agent_cuts": []}
    assert len(job_store.list_jobs(limit=None, include_result=False)) == before
    # Closed in the copy, so it does not read as running there forever.
    assert task_steps.status_of(stepped, task_steps.TRANSCRIBE) == task_steps.FAILED


def test_without_the_switch_the_same_start_carries_them_on(monkeypatch):
    """The check above is not vacuous: the normal start asks to carry on."""
    stepped = _task()
    task_steps.plan(stepped)
    task_steps.start(stepped, task_steps.TRANSCRIBE)
    monkeypatch.delenv(test_copy.ENV, raising=False)

    asked = _start_service(monkeypatch, [stepped])

    assert stepped in asked["carried_on"] and asked["agent_cuts"]


# ── copied tasks are read-only ─────────────────────────────────────────────

def _app() -> TestClient:
    app = FastAPI()
    app.middleware("http")(test_copy.middleware)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def anything(path: str, request: Request):
        return {"reached": path}

    return TestClient(app)


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/jobs/{old}/debreath", None),
    ("POST", "/agent/v1/tasks/{old}/visual-note", None),
    ("POST", "/agent/v1/tasks/{old}/note/versions/v1/restore", None),
    ("PUT", "/jobs/{old}/summary", {"summary_markdown": "x"}),
    ("DELETE", "/jobs/{old}", None),
    ("POST", "/regenerate-summary", {"task_id": "{old}"}),
])
def test_a_change_to_a_copied_task_is_refused(copy_of, method, path, body):
    old = _task("completed")
    copy_of(old)
    payload = json.loads(json.dumps(body).replace("{old}", old)) if body else None
    response = _app().request(method, path.replace("{old}", old), json=payload)
    assert response.status_code == 409
    assert response.json()["detail"] == test_copy.READ_ONLY


def test_a_task_made_in_the_copy_and_reading_a_copied_one_go_through(copy_of):
    old = _task("completed")
    copy_of(old)
    client = _app()
    assert client.post(f"/jobs/{_task('completed')}/debreath").json()["reached"]
    assert client.get(f"/jobs/{old}").json()["reached"]
    assert client.post("/agent/v1/tasks", json={"path": "/tmp/clip.mp4"}).json()["reached"]


def test_exporting_to_feishu_is_refused_on_the_copy(copy_of):
    copy_of()
    response = _app().post("/export-lark", json={"task_id": _task("completed")})
    assert (response.status_code, response.json()["detail"]) == (409, test_copy.NO_FEISHU)


def test_the_service_refuses_through_this_check():
    from backend import local_main

    app = local_main.create_local_app()
    assert any(getattr(entry, "kwargs", {}).get("dispatch") is test_copy.middleware for entry in app.user_middleware)


def test_without_the_switch_nothing_is_refused(monkeypatch):
    monkeypatch.delenv(test_copy.ENV, raising=False)
    assert _app().delete(f"/jobs/{_task('completed')}").json()["reached"]


# ── nothing of a copied task is written outside the copy ───────────────────

def test_a_copied_tasks_output_folder_is_never_written(copy_of, tmp_path):
    folder = tmp_path / "recordings"
    folder.mkdir()
    (folder / "lecture.mp4").write_bytes(b"original")
    old = _task("completed", folder=folder)
    copy_of(old)
    assert output_folder.sync(old) is None
    assert sorted(path.name for path in folder.iterdir()) == ["lecture.mp4"]


def test_nothing_is_pushed_to_or_pulled_from_feishu(copy_of, monkeypatch):
    old = _task("completed")
    copy_of(old)
    ran: list = []
    pushes: list = []
    monkeypatch.setattr(feishu_sync, "schedule_push_when_quiet", lambda task_id, *a, **k: pushes.append(task_id))
    runner = lambda *a, **k: ran.append(a)  # noqa: E731
    assert feishu_sync.push(old, previous={"doc_id": "d"}, runner=runner) is None
    assert feishu_sync.pull(old, runner=runner) is None
    feishu_sync._on_result_saved(old, {"summary_markdown": "a"}, {"summary_markdown": "b", "lark_sync": {"doc_id": "d"}})
    assert ran == [] and pushes == []


def test_a_review_note_beside_a_copied_task_is_refused(copy_of, monkeypatch):
    old = _task("completed")
    copy_of(old)
    monkeypatch.setattr(review_note, "locate", lambda *_a: (Path("/tmp/x_笔记.md"), job_store.get_job(old)))
    with pytest.raises(review_note.ReviewNoteError, match="只能看"):
        review_note.generate(old)


def test_the_page_says_it_runs_on_a_test_copy(copy_of):
    copy_of()
    shown = {item["id"]: item for item in workflow_status.workflow_status()["attention"]}
    assert "测试副本" in shown["test-copy"]["text"]["zh"] and shown["test-copy"]["action"] == "none"
