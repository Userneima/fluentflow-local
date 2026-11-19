"""Pinning a record: it stays on top of the list until unpinned, and pinning
or unpinning never changes when the task itself was last updated (unpinning
must put the record back where it was)."""

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.job_store import get_job, upsert_job
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.routers.local_job_mutation import router

_HEADERS = {"x-fluentflow-client-id": "desktop-a"}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _task() -> str:
    task_id = f"pin-{uuid.uuid4().hex}"
    upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, metadata={"display_title": "长课"})
    return task_id


def test_pin_then_unpin_keeps_updated_at():
    task_id = _task()
    before = get_job(task_id)["updated_at"]

    pinned = _client().post(f"/jobs/{task_id}/pin", json={"pinned": True}, headers=_HEADERS)
    assert pinned.status_code == 200
    body = pinned.json()
    assert body["metadata"]["pinned_at"]
    assert body["metadata"]["display_title"] == "长课"
    assert body["updated_at"] == before
    assert "task_snapshot" in body and "activity" in body

    unpinned = _client().post(f"/jobs/{task_id}/pin", json={"pinned": False}, headers=_HEADERS)
    assert unpinned.status_code == 200
    assert not unpinned.json()["metadata"].get("pinned_at")
    assert get_job(task_id)["updated_at"] == before


def test_pinning_twice_keeps_the_first_pin_time():
    task_id = _task()
    first = _client().post(f"/jobs/{task_id}/pin", json={"pinned": True}, headers=_HEADERS).json()
    again = _client().post(f"/jobs/{task_id}/pin", json={"pinned": True}, headers=_HEADERS).json()
    assert again["metadata"]["pinned_at"] == first["metadata"]["pinned_at"]


def test_pin_missing_task_is_404():
    assert _client().post("/jobs/nope/pin", json={"pinned": True}, headers=_HEADERS).status_code == 404


def test_pin_rejects_a_non_boolean():
    task_id = _task()
    response = _client().post(f"/jobs/{task_id}/pin", json={"pinned": "yes"}, headers=_HEADERS)
    assert response.status_code == 422
    assert not get_job(task_id)["metadata"].get("pinned_at")
