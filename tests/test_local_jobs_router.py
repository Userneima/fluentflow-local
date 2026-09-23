from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routers.local_jobs as local_jobs
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.core import local_task_detail
from backend.routers.local_jobs import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


_JOB = {
    "task_id": "local-1",
    "status": "completed",
    "source_type": "video",
    "result": {
        "transcript_text": "Transcript",
        "summary_markdown": "# Note",
        "stt_provider": "local",
        "stt_model": "small",
    },
}


def test_local_jobs_list_reads_the_local_owner_and_uses_local_projection(monkeypatch):
    seen: list[str | None] = []

    def fake_summaries(*, limit, client_id):
        seen.append(client_id)
        return [dict(_JOB)]

    monkeypatch.setattr(local_jobs, "list_job_summaries", fake_summaries)

    response = _client().get("/jobs", headers={"x-fluentflow-client-id": "desktop-a"})

    assert response.status_code == 200
    assert seen == [LOCAL_OWNER_ID]
    assert response.json()["jobs"][0]["task_snapshot"] == local_task_detail.build_task_snapshot(_JOB)


def test_local_jobs_list_is_the_same_whichever_client_id_is_sent(monkeypatch):
    seen: list[str | None] = []

    def fake_summaries(*, limit, client_id):
        seen.append(client_id)
        return []

    monkeypatch.setattr(local_jobs, "list_job_summaries", fake_summaries)

    _client().get("/jobs", headers={"x-fluentflow-client-id": "desktop-a"})
    _client().get("/jobs", headers={"x-fluentflow-client-id": "desktop-b"})
    _client().get("/jobs")

    # Local has one user: the page, the MCP server and scripts send different
    # client ids (or none), and every one of them must read the same task list.
    assert seen == [LOCAL_OWNER_ID, LOCAL_OWNER_ID, LOCAL_OWNER_ID]


def test_local_job_read_uses_local_snapshot(monkeypatch):
    scoped: list[tuple[str, str | None]] = []

    def scoped_job(task_id, client_id=None):
        scoped.append((task_id, client_id))
        return dict(_JOB)

    monkeypatch.setattr(local_jobs, "get_job", scoped_job)

    response = _client().get("/jobs/local-1", headers={"x-fluentflow-client-id": "desktop-a"})

    assert response.status_code == 200
    assert scoped == [("local-1", LOCAL_OWNER_ID)]
    assert response.json()["task_snapshot"] == local_task_detail.build_task_snapshot(_JOB)


def test_local_job_detail_uses_local_projection(monkeypatch):
    monkeypatch.setattr(local_jobs, "get_job", lambda task_id, client_id=None: dict(_JOB))
    monkeypatch.setattr(local_jobs, "list_job_steps", lambda *, task_id, limit: [])

    response = _client().get("/jobs/local-1/detail", headers={"x-fluentflow-client-id": "desktop-a"})

    assert response.status_code == 200
    assert response.json() == local_task_detail.build_task_detail(_JOB, job_steps=[])


def test_local_job_missing_returns_404(monkeypatch):
    monkeypatch.setattr(local_jobs, "get_job", lambda task_id, client_id=None: None)

    response = _client().get("/jobs/nope", headers={"x-fluentflow-client-id": "desktop-a"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


def test_local_job_query_stays_off_server_helpers():
    source = Path("backend/routers/local_jobs.py").read_text(encoding="utf-8")
    assert "import backend.core.server_helpers" not in source
    assert "from backend.core.server_helpers" not in source
    assert "from backend.core.task_detail import" not in source
    # The local adapter must project through the local task-detail policy.
    assert "local_task_detail" in Path("backend/routers/local_jobs.py").read_text(encoding="utf-8")
