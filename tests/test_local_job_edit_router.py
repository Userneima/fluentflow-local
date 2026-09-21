from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routers.job_edit as job_edit
from backend.routers.local_job_edit import router

_HEADERS = {"x-fluentflow-client-id": "desktop-a"}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _patch_common(monkeypatch, job):
    monkeypatch.setattr(job_edit, "get_job", lambda task_id, client_id=None: job)
    monkeypatch.setattr(job_edit, "_attach_result_artifacts", lambda task_id, result: result)
    monkeypatch.setattr(
        job_edit, "update_job_result",
        lambda task_id, result, client_id=None: {"task_id": task_id, "result": result, "client_id": client_id},
    )


# ---- transcript -------------------------------------------------------------

def test_transcript_missing_job_returns_404(monkeypatch):
    monkeypatch.setattr(job_edit, "get_job", lambda task_id, client_id=None: None)
    r = _client().patch("/jobs/nope/transcript", headers=_HEADERS, json={"transcript_text": "x"})
    assert r.status_code == 404


def test_transcript_requires_string_text(monkeypatch):
    _patch_common(monkeypatch, {"task_id": "t1", "result": {}})
    r = _client().patch("/jobs/t1/transcript", headers=_HEADERS, json={"transcript_text": 123})
    assert r.status_code == 400


def test_transcript_rejects_oversize(monkeypatch):
    _patch_common(monkeypatch, {"task_id": "t1", "result": {}})
    monkeypatch.setenv("FLUENTFLOW_MAX_TRANSCRIPT_EDIT_CHARS", "10")
    r = _client().patch("/jobs/t1/transcript", headers=_HEADERS, json={"transcript_text": "x" * 11})
    assert r.status_code == 413


def test_transcript_edit_persists_scoped(monkeypatch):
    scoped: list[str | None] = []

    def scoped_get(task_id, client_id=None):
        scoped.append(client_id)
        return {"task_id": task_id, "result": {}}

    monkeypatch.setattr(job_edit, "get_job", scoped_get)
    monkeypatch.setattr(job_edit, "_sanitize_edit_segments", lambda value: [])
    monkeypatch.setattr(job_edit, "_sanitize_edit_records", lambda value: [])
    monkeypatch.setattr(job_edit, "_canonical_display_segments", lambda result: [])
    monkeypatch.setattr(job_edit, "_write_edited_transcript_backup", lambda task_id, result: Path("/tmp/edited.txt"))
    monkeypatch.setattr(job_edit, "_write_transcript_edit_records_backup", lambda task_id, result, records: Path("/tmp/rec.json"))
    monkeypatch.setattr(job_edit, "_attach_result_artifacts", lambda task_id, result: result)
    monkeypatch.setattr(
        job_edit, "update_job_result",
        lambda task_id, result, client_id=None: {"task_id": task_id, "result": result},
    )

    r = _client().patch("/jobs/t1/transcript", headers=_HEADERS, json={"transcript_text": "hello world"})

    assert r.status_code == 200
    result = r.json()["result"]
    assert result["transcript_text"] == "hello world"
    assert result["transcript_edited"] is True
    assert scoped == ["desktop-a"]


def test_transcript_backup_failure_returns_500(monkeypatch):
    _patch_common(monkeypatch, {"task_id": "t1", "result": {}})
    monkeypatch.setattr(job_edit, "_sanitize_edit_segments", lambda value: [])
    monkeypatch.setattr(job_edit, "_sanitize_edit_records", lambda value: [])
    monkeypatch.setattr(job_edit, "_canonical_display_segments", lambda result: [])

    def boom(task_id, result):
        raise RuntimeError("disk full")

    monkeypatch.setattr(job_edit, "_write_edited_transcript_backup", boom)

    r = _client().patch("/jobs/t1/transcript", headers=_HEADERS, json={"transcript_text": "hi"})

    assert r.status_code == 500
    assert "backup failed" in r.json()["detail"].lower()


# ---- summary ----------------------------------------------------------------

def test_summary_missing_job_returns_404(monkeypatch):
    monkeypatch.setattr(job_edit, "get_job", lambda task_id, client_id=None: None)
    r = _client().patch("/jobs/nope/summary", headers=_HEADERS, json={"summary_markdown": "# x"})
    assert r.status_code == 404


def test_summary_requires_string(monkeypatch):
    _patch_common(monkeypatch, {"task_id": "t1", "result": {}})
    r = _client().patch("/jobs/t1/summary", headers=_HEADERS, json={"summary_markdown": None})
    assert r.status_code == 400


def test_summary_edit_persists(monkeypatch):
    _patch_common(monkeypatch, {"task_id": "t1", "result": {}})
    r = _client().patch("/jobs/t1/summary", headers=_HEADERS, json={"summary_markdown": "# Note"})
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["summary_markdown"] == "# Note"
    assert result["summary_edited"] is True
    assert result["summary_status"] == "completed"


def test_local_job_edit_stays_off_server_helpers():
    for path in ("backend/routers/local_job_edit.py", "backend/routers/job_edit.py"):
        source = Path(path).read_text(encoding="utf-8")
        assert "import backend.core.server_helpers" not in source
        assert "from backend.core.server_helpers" not in source
