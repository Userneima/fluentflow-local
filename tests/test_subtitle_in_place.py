"""A subtitle file on this machine, submitted by path, gets its note beside it.

Requirement (owner, 2026-11-25; found 2026-10-29 sorting 「1.短视频」): an
.srt with no video, handed to ``submit_local_media`` by path like a recording,
gets what a recording by path gets — a task the agent writes the note for, an
output folder beside the file with its marker, the md and PDF there, an edit of
the md in Obsidian taken back in — and the file itself is left alone. Before
this it could only go in as transcript text, and its note stayed in FluentFlow.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.core.transcript_note as transcript_note
from backend.core import job_store, note_pdf, output_folder, storage_paths
from backend.routers.agent_v1 import tasks
from backend.routers.local_agent import router as agent_router

_TOKEN = "local-secret-token"
_HEADERS = {"x-fluentflow-client-id": "desktop-a", "x-fluentflow-access-token": _TOKEN}

SRT = """1
00:00:01,000 --> 00:00:04,000
今天讲最小二乘法。

2
00:00:05,000 --> 00:00:09,000
先看误差平方和为什么能代表拟合得好不好。
"""


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", _TOKEN)
    monkeypatch.setattr(tasks, "request_is_localhost", lambda _request: True)
    app = FastAPI()
    app.include_router(agent_router)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def pdfs(monkeypatch):
    def export(task_id, *, client_id=None, output_dir=None, filename=None):
        path = Path(output_dir) / f"{filename}.pdf"
        path.write_bytes(b"%PDF fake")
        return {"task_id": task_id, "pdf_path": str(path), "html_path": None}

    monkeypatch.setattr(note_pdf, "export_pdf", export)


def _srt(tmp_path: Path) -> Path:
    folder = tmp_path / "1.短视频"
    folder.mkdir()
    path = folder / "最小二乘法.srt"
    path.write_text(SRT, encoding="utf-8")
    return path


def _settle() -> None:
    output_folder._WORKER.submit(lambda: None).result(timeout=10)


def _submit(client: TestClient, path: Path, **extra) -> str:
    r = client.post("/agent/v1/tasks", headers=_HEADERS,
                    json={"path": str(path), "input_type": "local_path", **extra})
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]
    # The task runs on the service's own loop: wait_task is how an agent sees it end.
    waited = client.post(f"/agent/v1/tasks/{task_id}/wait", headers=_HEADERS, json={"timeout_seconds": 10}).json()
    assert waited["done"] is True, waited
    _settle()
    return task_id


def test_the_agent_writes_the_note_and_it_lands_beside_the_subtitles(client, pdfs, tmp_path):
    srt = _srt(tmp_path)
    task_id = _submit(client, srt)

    waited = client.post(f"/agent/v1/tasks/{task_id}/wait", headers=_HEADERS, json={"timeout_seconds": 0}).json()
    assert waited["done"] is True
    assert "get_agent_note_material" in waited["next"]
    job = job_store.get_job(task_id)
    assert job["status"] == "completed"
    assert job["metadata"]["display_title"] == "最小二乘法"
    # Only the output folder reads where it came from: the flows that play,
    # cut or re-queue a recording find no recording here.
    assert "folder_intake" not in job["metadata"]
    assert storage_paths.in_place_source_path(job) is None

    folder = srt.parent / "最小二乘法"
    assert (folder / output_folder.MARKER_NAME).read_text(encoding="utf-8").startswith(task_id)
    assert "最小二乘法" in (folder / "过程文件" / "最小二乘法.srt").read_text(encoding="utf-8")

    material = client.post(f"/agent/v1/tasks/{task_id}/agent-note/material", headers=_HEADERS, json={}).json()
    assert material["ok"] and "误差平方和" in material["transcript"]
    saved = client.post(f"/agent/v1/tasks/{task_id}/agent-note", headers=_HEADERS,
                        json={"note_markdown": "# 最小二乘法\n\n误差平方和越小，拟合越好。"})
    assert saved.status_code == 200, saved.text
    _settle()

    md = folder / "最小二乘法_笔记.md"
    assert "拟合越好" in md.read_text(encoding="utf-8")
    assert (folder / "最小二乘法_笔记.pdf").is_file()
    assert srt.read_text(encoding="utf-8") == SRT


def test_an_edit_in_obsidian_comes_back_to_the_task(client, pdfs, tmp_path, monkeypatch):
    srt = _srt(tmp_path)
    task_id = _submit(client, srt)
    client.post(f"/agent/v1/tasks/{task_id}/agent-note/material", headers=_HEADERS, json={})
    client.post(f"/agent/v1/tasks/{task_id}/agent-note", headers=_HEADERS,
                json={"note_markdown": "# 最小二乘法\n\n第一版。"})
    _settle()
    md = srt.parent / "最小二乘法" / "最小二乘法_笔记.md"
    md.write_text("# 最小二乘法\n\n在 Obsidian 里改过。\n", encoding="utf-8")
    later = md.stat().st_mtime + 30
    import os

    os.utime(md, (later, later))

    assert task_id in output_folder.edited_notes(now=later + output_folder.EDIT_SETTLE_SECONDS)
    output_folder.sync(task_id)

    assert "在 Obsidian 里改过" in job_store.get_job(task_id)["result"]["summary_markdown"]


def test_a_note_fluentflows_model_writes_goes_in_the_folder_too(client, pdfs, tmp_path, monkeypatch):
    monkeypatch.setattr(transcript_note, "summarize_transcript_with_metadata", lambda text, **kw: SimpleNamespace(
        markdown="# 最小二乘法\n\n模型写的。", requested_mode="auto", resolved_mode="direct", chunk_count=1,
    ))
    srt = _srt(tmp_path)
    task_id = _submit(client, srt, options={"intelligence": "fluentflow", "skip_summary": "false"})

    job = job_store.get_job(task_id)
    assert job["status"] == "completed" and "模型写的" in job["result"]["summary_markdown"]
    assert "模型写的" in (srt.parent / "最小二乘法" / "最小二乘法_笔记.md").read_text(encoding="utf-8")


def test_a_second_submission_gets_its_own_folder(client, pdfs, tmp_path):
    srt = _srt(tmp_path)
    first = _submit(client, srt)
    second = _submit(client, srt)

    assert (srt.parent / "最小二乘法" / output_folder.MARKER_NAME).read_text(encoding="utf-8").startswith(first)
    assert (srt.parent / "最小二乘法 (2)" / output_folder.MARKER_NAME).read_text(encoding="utf-8").startswith(second)


def test_the_subtitle_copy_in_an_output_folder_is_refused(client, pdfs, tmp_path):
    srt = _srt(tmp_path)
    _submit(client, srt)
    copy = srt.parent / "最小二乘法" / "过程文件" / "最小二乘法.srt"

    r = client.post("/agent/v1/tasks", headers=_HEADERS, json={"path": str(copy), "input_type": "local_path"})

    assert r.status_code == 400 and "产出文件夹" in r.json()["detail"]


def test_a_plain_text_file_by_path_is_still_refused(client, tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("# 不是转录稿", encoding="utf-8")

    r = client.post("/agent/v1/tasks", headers=_HEADERS, json={"path": str(notes), "input_type": "local_path"})

    assert r.status_code == 400 and "只处理音视频" in r.json()["detail"]


def test_a_subtitle_path_is_refused_when_the_request_is_not_from_this_machine(client, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "request_is_localhost", lambda _request: False)

    r = client.post("/agent/v1/tasks", headers=_HEADERS, json={"path": str(_srt(tmp_path)), "input_type": "local_path"})

    assert r.status_code == 403


@pytest.mark.parametrize("text", ["", "WEBVTT\n\n"])
def test_a_subtitle_file_with_no_timed_line_fails_the_task(client, tmp_path, text):
    """A bare header would otherwise be read as plain text and handed to the
    note writer as the transcript."""
    empty = tmp_path / "空.vtt"
    empty.write_text(text, encoding="utf-8")
    task_id = _submit(client, empty)

    job = job_store.get_job(task_id)
    assert job["status"] == "failed" and job["error_reason"]
    assert not (tmp_path / "空").exists()


def test_a_subtitle_file_over_the_limit_is_refused_before_a_task_exists(client, tmp_path, monkeypatch):
    monkeypatch.setattr(transcript_note, "max_transcript_upload_mb", lambda: 1.0)
    big = _srt(tmp_path)
    big.write_text(SRT * 20000, encoding="utf-8")
    before = len(job_store.list_jobs(limit=1000))

    r = client.post("/agent/v1/tasks", headers=_HEADERS, json={"path": str(big), "input_type": "local_path"})

    assert r.status_code == 413
    assert len(job_store.list_jobs(limit=1000)) == before


def test_a_cancelled_task_stays_cancelled_when_the_model_answers_later(client, tmp_path, monkeypatch):
    """Cancelled (and maybe deleted) while FluentFlow's model writes the note:
    the answer coming back afterwards must not complete the task again."""
    import threading

    release = threading.Event()

    def slow(text, **kw):
        assert release.wait(10)
        return SimpleNamespace(markdown="# 晚到的笔记", requested_mode="auto", resolved_mode="direct", chunk_count=1)

    monkeypatch.setattr(transcript_note, "summarize_transcript_with_metadata", slow)
    r = client.post("/agent/v1/tasks", headers=_HEADERS, json={
        "path": str(_srt(tmp_path)), "input_type": "local_path",
        "options": {"intelligence": "fluentflow", "skip_summary": "false"},
    })
    task_id = r.json()["task_id"]
    import time

    deadline = time.monotonic() + 10
    while job_store.get_job(task_id)["status"] != "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert client.post(f"/agent/v1/tasks/{task_id}/cancel", headers=_HEADERS).status_code == 200
    release.set()
    time.sleep(0.5)

    job = job_store.get_job(task_id)
    assert job["status"] == "cancelled"
    assert not str((job.get("result") or {}).get("summary_markdown") or "")


def test_a_folder_holding_a_hand_sorted_subtitle_copy_is_not_written_over(client, pdfs, tmp_path):
    """The subtitle sits in its own folder, whose 过程文件 already holds a file
    under the name this task would write: the task takes the next name."""
    folder = tmp_path / "课程"
    (folder / "过程文件").mkdir(parents=True)
    srt = folder / "课程.srt"
    srt.write_text(SRT, encoding="utf-8")
    kept = folder / "过程文件" / "课程.txt"
    kept.write_text("我自己整理的", encoding="utf-8")

    _submit(client, srt)

    assert kept.read_text(encoding="utf-8") == "我自己整理的"
    assert (folder / "过程文件" / "课程 (2).txt").is_file()


def test_a_subtitle_file_in_utf16_without_its_mark_says_it_may_be_the_encoding(client, tmp_path):
    """(independent review, 2026-11-25) Not read as UTF-16, the file has no
    timed line; the reason given points at the encoding."""
    path = tmp_path / "无标记.srt"
    path.write_bytes(SRT.encode("utf-16-le"))
    task_id = _submit(client, path)

    job = job_store.get_job(task_id)
    assert job["status"] == "failed" and "UTF-16" in job["error_reason"]
