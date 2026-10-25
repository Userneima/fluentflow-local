"""A task still being worked on is not deleted.

Requirement (review, 2026-10-16): a task reads completed once its transcript is
stored, while its cut and note are still to come. Deleting it then let the
note step write it back afterwards, with no source and a failed note. The
delete is refused, in words that say what it is waiting for; a task that is
really finished is deleted as before.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import job_store, task_steps
from backend.routers import local_job_mutation


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(local_job_mutation, "_local_client_scope", lambda request: None)
    monkeypatch.setattr(local_job_mutation, "cleanup_task_all_files", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(local_job_mutation.router)
    return TestClient(app)


def _task(result):
    task_id = f"del-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status="completed", result=result)
    return task_id


@pytest.mark.parametrize("result, words", [
    ({"summary_status": "pending"}, "笔记还在写"),
    ({"debreath": {"status": "running"}}, "剪辑或导出"),
])
def test_a_task_still_being_worked_on_is_not_deleted(client, result, words):
    task = _task(result)

    response = client.delete(f"/jobs/{task}")

    assert response.status_code == 409 and words in response.json()["detail"]
    assert job_store.get_job(task) is not None
    job_store.delete_jobs([task])


def test_a_task_whose_cut_and_note_are_still_to_come_is_not_deleted(client):
    # The transcript is stored and the later steps have not started yet: the
    # refusal names the first of them.
    task = _task({"summary_status": "skipped", "note_deferred_to_visual_note": True, "transcript_text": "x"})
    task_steps.plan(task)
    for done in task_steps.BEFORE_TRANSCRIPT:
        task_steps.finish(task, done, task_steps.DONE)

    response = client.delete(f"/jobs/{task}")

    assert response.status_code == 409 and "剪辑或导出" in response.json()["detail"]
    job_store.delete_jobs([task])


def test_a_finished_task_is_deleted(client):
    task = _task({"summary_status": "completed", "summary_markdown": "# 笔记"})

    assert client.delete(f"/jobs/{task}").status_code == 200
    assert job_store.get_job(task) is None
