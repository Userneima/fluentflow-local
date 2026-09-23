from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routers.local_job_mutation as local_job_mutation
from backend.core.local_job_runtime import JOB_EVENTS
from backend.routers.local_job_mutation import router

_HEADERS = {"x-fluentflow-client-id": "desktop-a"}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_cancel_missing_job_returns_404(monkeypatch):
    scoped: list[tuple[str, str | None]] = []

    def missing(task_id, client_id=None):
        scoped.append((task_id, client_id))
        return None

    monkeypatch.setattr(local_job_mutation, "get_job", missing)

    response = _client().post("/jobs/nope/cancel", headers=_HEADERS)

    assert response.status_code == 404
    assert scoped == [("nope", "desktop-a")]


def test_cancel_terminal_job_returns_409(monkeypatch):
    monkeypatch.setattr(
        local_job_mutation, "get_job",
        lambda task_id, client_id=None: {"task_id": task_id, "status": "completed"},
    )

    response = _client().post("/jobs/t1/cancel", headers=_HEADERS)

    assert response.status_code == 409
    assert "already completed" in response.json()["detail"]


def test_cancel_running_job_marks_cancelled_and_reconciles_hub(monkeypatch):
    events: dict[str, object] = {}

    async def fake_cancel(task_id):
        events["cancelled"] = task_id
        return True

    published: list[tuple[str, dict]] = []

    async def fake_publish(task_id, event):
        published.append((task_id, event))

    upserts: list[dict] = []
    logged: list[dict] = []

    monkeypatch.setattr(
        local_job_mutation, "get_job",
        lambda task_id, client_id=None: {
            "task_id": task_id, "status": "running", "stage": "stt",
            "progress": 40, "client_id": "desktop-a",
        },
    )
    monkeypatch.setattr(local_job_mutation, "cancel_job_steps", lambda task_id: 2)
    monkeypatch.setattr(local_job_mutation, "upsert_job", lambda **kwargs: upserts.append(kwargs))
    monkeypatch.setattr(local_job_mutation, "log_event", lambda **kwargs: logged.append(kwargs))
    monkeypatch.setattr(JOB_EVENTS, "cancel", fake_cancel)
    monkeypatch.setattr(JOB_EVENTS, "publish", fake_publish)

    response = _client().post("/jobs/t1/cancel", headers=_HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "task_id": "t1",
        "status": "cancelled",
        "cancelled_running_task": True,
        "cancelled_steps": 2,
        "queued_before_cancel": False,
    }
    assert events["cancelled"] == "t1"
    assert upserts[0]["status"] == "cancelled"
    assert upserts[0]["error_reason"] == "user_cancelled"
    assert published[0][1]["error"] == "Task cancelled"
    assert logged[0]["event_name"] == "task_cancelled"


def test_delete_missing_job_returns_404(monkeypatch):
    scoped: list[tuple[str, str | None]] = []

    def missing(task_id, client_id=None):
        scoped.append((task_id, client_id))
        return None

    monkeypatch.setattr(local_job_mutation, "get_job", missing)

    response = _client().delete("/jobs/nope", headers=_HEADERS)

    assert response.status_code == 404
    assert scoped == [("nope", "desktop-a")]


def test_delete_active_job_returns_409(monkeypatch):
    monkeypatch.setattr(
        local_job_mutation, "get_job",
        lambda task_id, client_id=None: {"task_id": task_id, "status": "running"},
    )

    response = _client().delete("/jobs/t1", headers=_HEADERS)

    assert response.status_code == 409
    assert "Cancel running jobs" in response.json()["detail"]


def test_delete_completed_job_cleans_files_and_deletes_scoped(monkeypatch):
    cleaned: list[tuple[str, dict | None]] = []
    deleted: list[tuple[list[str], str | None]] = []

    monkeypatch.setattr(
        local_job_mutation, "get_job",
        lambda task_id, client_id=None: {"task_id": task_id, "status": "completed", "metadata": {"m": 1}},
    )
    monkeypatch.setattr(
        local_job_mutation, "cleanup_task_all_files",
        lambda task_id, metadata=None: cleaned.append((task_id, metadata)) or {},
    )
    monkeypatch.setattr(
        local_job_mutation, "delete_jobs",
        lambda ids, client_id=None: deleted.append((ids, client_id)) or 1,
    )

    response = _client().delete("/jobs/t1", headers=_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "task_id": "t1", "deleted": True}
    assert cleaned == [("t1", {"m": 1})]
    assert deleted == [(["t1"], "desktop-a")]


def test_delete_fallback_post_route_works(monkeypatch):
    monkeypatch.setattr(
        local_job_mutation, "get_job",
        lambda task_id, client_id=None: {"task_id": task_id, "status": "completed"},
    )
    monkeypatch.setattr(local_job_mutation, "cleanup_task_all_files", lambda task_id, metadata=None: {})
    monkeypatch.setattr(local_job_mutation, "delete_jobs", lambda ids, client_id=None: 1)

    response = _client().post("/jobs/t1/delete", headers=_HEADERS)

    assert response.status_code == 200
    assert response.json()["deleted"] is True


def test_delete_returns_404_when_store_reports_nothing_deleted(monkeypatch):
    monkeypatch.setattr(
        local_job_mutation, "get_job",
        lambda task_id, client_id=None: {"task_id": task_id, "status": "completed"},
    )
    monkeypatch.setattr(local_job_mutation, "cleanup_task_all_files", lambda task_id, metadata=None: {})
    monkeypatch.setattr(local_job_mutation, "delete_jobs", lambda ids, client_id=None: 0)

    response = _client().delete("/jobs/t1", headers=_HEADERS)

    assert response.status_code == 404


def test_local_job_mutation_stays_off_server_helpers():
    source = Path("backend/routers/local_job_mutation.py").read_text(encoding="utf-8")
    assert "import backend.core.server_helpers" not in source
    assert "from backend.core.server_helpers" not in source
