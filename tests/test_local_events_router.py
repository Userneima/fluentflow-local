from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routers.local_events as local_events
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.routers.local_events import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_local_events_keep_only_safe_client_metadata(monkeypatch):
    logged: list[dict] = []
    monkeypatch.setattr(local_events, "log_event", lambda **values: logged.append(values))

    response = _client().post(
        "/events",
        headers={"x-fluentflow-client-id": "desktop-one"},
        json={
            "event_name": "summary_downloaded",
            "task_id": "task-1",
            "metadata": {"format": "markdown", "secret": "do-not-store"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "task_id": "task-1"}
    assert logged[0]["metadata"]["format"] == "markdown"
    assert "secret" not in logged[0]["metadata"]


def test_local_cancel_event_checks_the_scoped_job(monkeypatch):
    requested: list[tuple[str, str]] = []

    def missing_job(task_id: str, *, client_id: str):
        requested.append((task_id, client_id))
        return None

    monkeypatch.setattr(local_events, "get_job", missing_job)

    response = _client().post(
        "/events",
        headers={"x-fluentflow-client-id": "desktop-two"},
        json={"event_name": "task_cancelled", "task_id": "missing"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}
    assert requested == [("missing", LOCAL_OWNER_ID)]


def test_local_events_reject_unknown_event_names():
    response = _client().post(
        "/events",
        json={"event_name": "arbitrary_event"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Unsupported event: arbitrary_event"}
