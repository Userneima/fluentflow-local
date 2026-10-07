"""With "export to Feishu automatically" on, the note written after the
pipeline is exported too.

Requirement: the user turned automatic export on, so every task whose note got
written ends with either a Feishu document or the reason there is none. The
pipeline's own export only covers the note the pipeline writes; the frame note
written after it was never exported and the task said "waiting for export"
forever.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from backend.core import job_store
from backend.routers import local_processing as lp


def _ctx(task_id: str, *, do_lark=True, deferred=True):
    return SimpleNamespace(
        task_id_value=task_id, client_id=None, do_lark=do_lark, note_deferred_to_visual_note=deferred,
        source_type="video", source_filename="talk.mp4", display_title_value="讲座", title="讲座",
        lark_export_route=None, lark_via_cli=None, lark_app_id=None, lark_app_secret=None, folder_token=None,
    )


@pytest.fixture()
def task():
    task_id = f"lark-after-note-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status="completed", client_id=None, result={
        "task_id": task_id, "summary_markdown": "# 讲座\n\n正文", "summary_status": "completed",
    })
    yield task_id
    job_store.delete_jobs([task_id])


@pytest.fixture()
def events(monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(lp, "log_event", lambda **kw: recorded.append(kw))
    return recorded


def test_the_written_note_is_exported_and_the_link_kept(task, events, monkeypatch):
    sent: list[str] = []

    def fake_export(**values):
        sent.append(values["summary_markdown"])
        return {"doc_title": "讲座", "export_target": "lark_openapi", "response": {"url": "https://feishu.example/doc"}}

    monkeypatch.setattr(lp, "_auto_export_local_lark", fake_export)
    asyncio.run(lp._export_note_written_after_pipeline(_ctx(task)))

    assert sent == ["# 讲座\n\n正文"]
    result = job_store.get_job(task)["result"]
    assert result["lark_response"]["url"] == "https://feishu.example/doc"
    assert [e["event_name"] for e in events] == ["lark_export_started", "lark_export_completed"]


def test_a_failed_export_leaves_the_reason_and_keeps_the_note(task, events, monkeypatch):
    def broken(**_values):
        raise RuntimeError("Feishu app secret is invalid")

    monkeypatch.setattr(lp, "_auto_export_local_lark", broken)
    asyncio.run(lp._export_note_written_after_pipeline(_ctx(task)))

    result = job_store.get_job(task)["result"]
    assert result["lark_error"]
    assert result["summary_markdown"] == "# 讲座\n\n正文"


@pytest.mark.parametrize(("do_lark", "deferred"), [(False, True), (True, False)])
def test_nothing_is_exported_when_not_asked_or_when_the_pipeline_already_did(task, events, monkeypatch, do_lark, deferred):
    monkeypatch.setattr(lp, "_auto_export_local_lark", lambda **_v: pytest.fail("must not export"))
    asyncio.run(lp._export_note_written_after_pipeline(_ctx(task, do_lark=do_lark, deferred=deferred)))
    assert not events


def test_a_task_whose_note_failed_is_not_exported(events, monkeypatch):
    task_id = f"lark-no-note-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status="completed", client_id=None,
                         result={"summary_markdown": "", "summary_status": "failed"})
    monkeypatch.setattr(lp, "_auto_export_local_lark", lambda **_v: pytest.fail("must not export"))
    try:
        asyncio.run(lp._export_note_written_after_pipeline(_ctx(task_id)))
    finally:
        job_store.delete_jobs([task_id])
    assert not events
