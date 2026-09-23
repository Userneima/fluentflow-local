from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routers.local_job_read as local_job_read
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.core.local_job_runtime import JOB_EVENTS
from backend.routers.local_job_read import router

_HEADERS = {"x-fluentflow-client-id": "desktop-a"}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---- events (SSE) -----------------------------------------------------------

def test_events_missing_job_returns_404(monkeypatch):
    scoped: list[tuple[str, str | None]] = []

    def missing(task_id, client_id=None):
        scoped.append((task_id, client_id))
        return None

    monkeypatch.setattr(local_job_read, "get_job", missing)

    response = _client().get("/jobs/nope/events", headers=_HEADERS)

    assert response.status_code == 404
    assert scoped == [("nope", LOCAL_OWNER_ID)]


def test_events_stream_uses_local_hub(monkeypatch):
    async def fake_subscribe(task_id, *, since=0):
        yield f"data: {task_id}:{since}\n\n"

    monkeypatch.setattr(local_job_read, "get_job", lambda task_id, client_id=None: {"task_id": task_id})
    monkeypatch.setattr(JOB_EVENTS, "subscribe", fake_subscribe)

    response = _client().get("/jobs/local-1/events?since=3", headers=_HEADERS)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == "data: local-1:3\n\n"


# ---- source -----------------------------------------------------------------

def test_source_missing_job_returns_404(monkeypatch):
    monkeypatch.setattr(local_job_read, "get_job", lambda task_id, client_id=None: None)
    response = _client().get("/jobs/local-1/source", headers=_HEADERS)
    assert response.status_code == 404


def test_source_missing_file_returns_404(monkeypatch):
    monkeypatch.setattr(local_job_read, "get_job", lambda task_id, client_id=None: {"task_id": task_id})
    monkeypatch.setattr(local_job_read, "find_source_file", lambda task_id: None)
    response = _client().get("/jobs/local-1/source", headers=_HEADERS)
    assert response.status_code == 404


def test_source_returns_saved_file(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video-bytes")
    monkeypatch.setattr(local_job_read, "get_job", lambda task_id, client_id=None: {"task_id": task_id})
    monkeypatch.setattr(local_job_read, "find_source_file", lambda task_id: source)

    response = _client().get("/jobs/local-1/source", headers=_HEADERS)

    assert response.status_code == 200
    assert response.content == b"video-bytes"


# ---- artifacts --------------------------------------------------------------

def test_artifact_unknown_kind_returns_404(monkeypatch, tmp_path):
    monkeypatch.setattr(local_job_read, "get_job", lambda task_id, client_id=None: {"task_id": task_id})
    monkeypatch.setattr(local_job_read, "_artifact_storage_dir", lambda: tmp_path)
    response = _client().get("/jobs/local-1/artifacts/unknown_kind", headers=_HEADERS)
    assert response.status_code == 404


def test_artifact_returns_summary_markdown(monkeypatch, tmp_path):
    task_dir = tmp_path / "local-1"
    task_dir.mkdir()
    (task_dir / "note_summary.md").write_text("# Note", encoding="utf-8")
    monkeypatch.setattr(local_job_read, "get_job", lambda task_id, client_id=None: {"task_id": task_id, "result": {}})
    monkeypatch.setattr(local_job_read, "_artifact_storage_dir", lambda: tmp_path)

    response = _client().get("/jobs/local-1/artifacts/summary_md", headers=_HEADERS)

    assert response.status_code == 200
    assert response.text == "# Note"


def test_artifact_frame_rejects_path_traversal(monkeypatch, tmp_path):
    (tmp_path / "local-1").mkdir()
    monkeypatch.setattr(local_job_read, "get_job", lambda task_id, client_id=None: {"task_id": task_id, "result": {}})
    monkeypatch.setattr(local_job_read, "_artifact_storage_dir", lambda: tmp_path)
    response = _client().get("/jobs/local-1/artifacts/frame?file=../secret", headers=_HEADERS)
    assert response.status_code == 404


# ---- graph boundary ---------------------------------------------------------

def test_local_job_read_stays_off_server_helpers():
    source = Path("backend/routers/local_job_read.py").read_text(encoding="utf-8")
    assert "import backend.core.server_helpers" not in source
    assert "from backend.core.server_helpers" not in source
