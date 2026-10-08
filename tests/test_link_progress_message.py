"""Requirement: while a link downloads, the page shows the download text
("正在下载视频 40%"), and the note's state (summary_status) never carries it.
The text belongs to the download: once the task moves on it is gone.
"""

from __future__ import annotations

import asyncio

from backend.core import job_store
from backend.core.local_entry_guards import CancellationGate
from backend.core.video_source import VideoSourceProgress
from backend.routers import local_video_sources as lvs


def _download_reports_then_stops(seen: dict):
    def download(*_a, on_progress=None, **_k):
        on_progress(VideoSourceProgress(stage="downloading", message="正在下载视频 40%", percent=40))
        seen["job"] = job_store.get_job(seen["task_id"])
        raise RuntimeError("stop here")
    return download


def test_download_text_is_the_progress_message_not_the_note_status(monkeypatch):
    task_id = "link-progress-message"
    job_store.upsert_job(task_id=task_id, status="queued", client_id="c", stage="queued")
    seen: dict = {"task_id": task_id}
    monkeypatch.setattr(lvs, "download_video_source", _download_reports_then_stops(seen))
    monkeypatch.setattr(lvs, "log_event", lambda **_k: None)
    monkeypatch.setattr(lvs, "get_preference", lambda _n: None)

    async def publish(*_a, **_k):
        return None

    monkeypatch.setattr(lvs.JOB_EVENTS, "publish", publish)

    asyncio.run(lvs._download_then_process(
        task_id=task_id, input_text="https://www.youtube.com/watch?v=abc", title=None, options={},
        allow_miuistore=False, client_id="c", gate=CancellationGate(), route="/r", previous=None,
        done=asyncio.Event(),
    ))

    during = seen["job"]
    assert during["progress_message"] == "正在下载视频 40%"
    assert during["summary_status"] is None, "the note has not started; its status must not hold download text"
    listed = next(job for job in job_store.list_job_summaries(client_id="c") if job["task_id"] == task_id)
    # After the download failed the line is stale and no longer shown.
    assert listed["progress_message"] is None
    assert listed["summary_status"] is None
    job_store.delete_jobs([task_id], client_id="c")


def test_progress_text_is_dropped_once_the_task_reaches_another_stage():
    task_id = "link-progress-stage"
    job_store.upsert_job(
        task_id=task_id, status="running", client_id="c", stage="downloading",
        metadata=job_store.progress_message_metadata("正在保存视频信息", "downloading"),
    )
    assert job_store.get_job(task_id)["progress_message"] == "正在保存视频信息"

    job_store.upsert_job(task_id=task_id, status="running", client_id="c", stage="transcribing")

    assert job_store.get_job(task_id)["progress_message"] is None, "transcribing must not still say 正在保存视频信息"
    job_store.delete_jobs([task_id], client_id="c")


def test_rows_written_before_the_fix_do_not_show_download_text_as_note_status():
    import sqlite3

    task_id = "legacy-progress-in-summary"
    job_store.upsert_job(task_id=task_id, status="running", client_id="c", stage="transcribing")
    with sqlite3.connect(job_store.DEFAULT_DB_PATH) as conn:
        conn.execute("UPDATE jobs SET summary_status = ? WHERE task_id = ?", ("正在保存视频信息", task_id))

    assert job_store.get_job(task_id)["summary_status"] is None
    job_store.upsert_job(task_id=task_id, status="completed", client_id="c", summary_status="completed")
    assert job_store.get_job(task_id)["summary_status"] == "completed"
    job_store.delete_jobs([task_id], client_id="c")


def test_the_task_list_says_when_the_text_model_wrote_the_note_instead():
    """Requirement: the task card can say the note was written by the text
    model from the transcript (no screenshots) and why, from the list payload."""
    task_id = "list-fallback-note"
    job_store.upsert_job(
        task_id=task_id, status="completed", client_id="c", stage="done",
        result={
            "summary_markdown": "笔记", "summary_status": "completed",
            "note_written_by": "text_fallback", "summary_written_from": "text_fallback",
            "note_fallback_reason": "没有填 Anthropic API Key。",
        },
    )

    listed = next(job for job in job_store.list_job_summaries(client_id="c") if job["task_id"] == task_id)

    assert listed["result"]["note_written_by"] == "text_fallback"
    assert listed["result"]["note_fallback_reason"] == "没有填 Anthropic API Key。"
    assert listed["result"]["note_from_frames"] is False
    job_store.delete_jobs([task_id], client_id="c")
