"""The page can show a task's output folder in Explorer or Finder.

Found on Windows, 2026-10-18: a link video's outputs were filed under the
Videos folder, but nothing on the page said where, and the only button was
"download", which read as "the file is in the app".
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core import job_store, open_folder
from backend.core.local_request_scope import LOCAL_OWNER_ID


def test_each_system_opens_its_own_file_manager():
    folder = Path("D:/视频/FluentFlow/a")
    assert open_folder.open_command(folder, "win32") == ["explorer", str(folder)]
    assert open_folder.open_command(folder, "darwin") == ["open", str(folder)]
    assert open_folder.open_command(folder, "linux") == ["xdg-open", str(folder)]


def test_a_moved_folder_is_said_not_opened(tmp_path):
    with pytest.raises(open_folder.OpenFolderError, match="不在原来的位置"):
        open_folder.open_folder(tmp_path / "gone", launch=lambda *a, **k: pytest.fail("nothing to open"))


@pytest.fixture
def client(monkeypatch):
    from backend.local_main import create_local_app
    from backend.routers import local_processing

    monkeypatch.setattr(local_processing, "request_is_localhost", lambda _request: True)
    return TestClient(create_local_app())


def _task(result: dict) -> str:
    task_id = f"open-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, result=result)
    return task_id


def test_only_the_folder_the_task_recorded_is_opened(client, tmp_path, monkeypatch):
    opened: list[list[str]] = []
    monkeypatch.setattr(open_folder.subprocess, "Popen", lambda args, **kw: opened.append(list(args)))
    task_id = _task({"output_folder": {"path": str(tmp_path)}})

    answer = client.post(f"/jobs/{task_id}/output-folder/open", json={"path": "C:/Windows"})

    assert answer.status_code == 200, answer.text
    assert opened == [open_folder.open_command(tmp_path)], "the task's folder, never one from the request"
    job_store.delete_jobs([task_id])


def test_a_task_without_a_folder_says_so(client):
    task_id = _task({})
    answer = client.post(f"/jobs/{task_id}/output-folder/open")
    assert answer.status_code == 409 and "没有放进你文件夹" in answer.json()["detail"]
    job_store.delete_jobs([task_id])


def test_another_machine_cannot_open_a_window_here(monkeypatch, tmp_path):
    from backend.local_main import create_local_app
    from backend.routers import local_processing

    monkeypatch.setattr(local_processing, "request_is_localhost", lambda _request: False)
    task_id = _task({"output_folder": {"path": str(tmp_path)}})
    answer = TestClient(create_local_app()).post(f"/jobs/{task_id}/output-folder/open")
    assert answer.status_code == 403
    job_store.delete_jobs([task_id])
