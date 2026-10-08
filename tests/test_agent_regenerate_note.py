"""MCP/Agent "regenerate the note": the right writer, and nothing lost.

Requirements (2026-10-08 review):

- A note Claude wrote from the frames is rewritten by Claude from the frames,
  the way the editor does it, whenever that can work for this task. It is not
  silently replaced by a text-model note.
- Whoever rewrites it, the previous note can be put back afterwards.
- A failed rewrite leaves the task completed, with its note as it was; only the
  note's own status says the rewrite failed.
- The prompt and note mode reach the text model: the caller's, or else the ones
  the task was submitted with.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import job_store, visual_note_job
from backend.core.local_request_scope import LOCAL_OWNER_ID
import backend.routers.local_agent as local_agent
import backend.routers.local_job_visual_note as local_job_visual_note

_TOKEN = "regen-token"
_HEADERS = {"x-fluentflow-access-token": _TOKEN}

CLAUDE_NOTE = "# Claude 看画面写的笔记\n\n![](frame-001.jpg)"
TEXT_NOTE = "# 文本模型写的笔记"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", _TOKEN)
    monkeypatch.setattr(local_agent, "_attach_result_artifacts", lambda _task_id, result: result)
    monkeypatch.setattr(local_job_visual_note, "queue_is_busy", lambda: False)
    app = FastAPI()
    app.include_router(local_agent.router)
    return TestClient(app)


@pytest.fixture()
def text_model(monkeypatch):
    calls: list[dict] = []

    def summarize(transcript, **kwargs):
        calls.append({"transcript": transcript, **kwargs})
        return SimpleNamespace(markdown=TEXT_NOTE, requested_mode="auto", resolved_mode="direct", chunk_count=1)

    monkeypatch.setattr(local_agent, "summarize_transcript_with_metadata", summarize)
    return calls


def _claude_can_write(monkeypatch, eligible: bool, reason: str | None = None):
    def describe(task_id, job, **_kwargs):
        return {
            "eligible": eligible, "reason": None if eligible else reason,
            "model": "claude-test", "channel": "api_key", "channel_label": "API Key",
            "frame_budget": 20, "transcript_chars": 10, "source_filename": "cut.mp4",
            "media": None, "subtitle_timeline": None,
        }

    monkeypatch.setattr(visual_note_job, "describe", describe)


def _task(task_id: str, *, note: str, written_from: str | None, queue_options: dict | None = None) -> None:
    job_store.upsert_job(
        task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, stage="done", progress=100,
        metadata={"queue_options": queue_options or {}},
        result={
            "task_id": task_id,
            "transcript_text": "今天讲三件事",
            "summary_markdown": note,
            "summary_status": "completed",
            "summary_written_from": written_from,
        },
    )


def _stored(task_id: str) -> dict:
    return job_store.get_job(task_id, client_id=LOCAL_OWNER_ID)


def _restore(client: TestClient, task_id: str) -> dict:
    response = client.post(
        f"/agent/v1/tasks/{task_id}/visual-note", headers=_HEADERS, json={"restore_previous_note": True}
    )
    assert response.status_code == 200, response.text
    return _stored(task_id)["result"]


def test_a_claude_frame_note_is_rewritten_by_claude_from_the_frames(client, text_model, monkeypatch):
    _task("regen-claude", note=CLAUDE_NOTE, written_from=visual_note_job.SUMMARY_WRITTEN_FROM)
    _claude_can_write(monkeypatch, True)
    runs: list[dict] = []
    monkeypatch.setattr(
        visual_note_job, "run_visual_note", lambda task_id, **kwargs: runs.append({"task_id": task_id, **kwargs})
    )

    response = client.post("/agent/v1/tasks/regen-claude/note/regenerate", headers=_HEADERS, json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["writer"] == "claude_frames"
    assert body["previous_note_restorable"] is True
    assert text_model == [], "the text model never touched a Claude note"
    assert len(runs) == 1 and runs[0]["replace_note"] is True, "the editor's path, which keeps the old note"
    assert _stored("regen-claude")["result"]["summary_markdown"] == CLAUDE_NOTE, "nothing replaced before Claude finishes"


def test_when_claude_cannot_rewrite_it_the_text_note_replaces_it_and_the_claude_note_can_come_back(
    client, text_model, monkeypatch
):
    _task("regen-fallback", note=CLAUDE_NOTE, written_from=visual_note_job.SUMMARY_WRITTEN_FROM)
    _claude_can_write(monkeypatch, False, "剪后的文件已经不在了")

    response = client.post("/agent/v1/tasks/regen-fallback/note/regenerate", headers=_HEADERS, json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["writer"] == "text_model"
    assert body["frame_note_unavailable_reason"] == "剪后的文件已经不在了"
    result = _stored("regen-fallback")["result"]
    assert result["summary_markdown"] == TEXT_NOTE
    assert result["summary_written_from"] == "text_regeneration", "the page must not call it Claude's"

    restored = _restore(client, "regen-fallback")
    assert restored["summary_markdown"] == CLAUDE_NOTE
    assert restored["summary_written_from"] == visual_note_job.SUMMARY_WRITTEN_FROM


def test_a_text_note_rewritten_by_the_text_model_keeps_the_old_one_restorable(client, text_model):
    _task("regen-text", note="# 旧的文本笔记", written_from=None)

    response = client.post("/agent/v1/tasks/regen-text/note/regenerate", headers=_HEADERS, json={})

    assert response.status_code == 200, response.text
    assert response.json()["previous_note_restorable"] is True
    assert _stored("regen-text")["result"]["summary_markdown"] == TEXT_NOTE
    assert _restore(client, "regen-text")["summary_markdown"] == "# 旧的文本笔记"


def test_a_failed_rewrite_leaves_the_task_completed_and_its_note_as_it_was(client, monkeypatch):
    _task("regen-fail", note="# 原来的笔记", written_from=None)

    def broken(_transcript, **_kwargs):
        raise RuntimeError("provider timed out")

    monkeypatch.setattr(local_agent, "summarize_transcript_with_metadata", broken)

    response = client.post("/agent/v1/tasks/regen-fail/note/regenerate", headers=_HEADERS, json={})

    assert response.status_code == 500
    job = _stored("regen-fail")
    assert job["status"] == "completed"
    assert job["result"]["summary_markdown"] == "# 原来的笔记"
    assert job["result"]["summary_status"] == "failed"
    assert job["result"]["summary_error"]


def test_an_empty_answer_is_a_failure_not_an_empty_note(client, monkeypatch):
    _task("regen-empty", note="# 原来的笔记", written_from=None)
    monkeypatch.setattr(
        local_agent, "summarize_transcript_with_metadata",
        lambda _t, **_k: SimpleNamespace(markdown="  ", requested_mode="auto", resolved_mode="direct", chunk_count=1),
    )

    response = client.post("/agent/v1/tasks/regen-empty/note/regenerate", headers=_HEADERS, json={})

    assert response.status_code == 500
    job = _stored("regen-empty")
    assert job["status"] == "completed" and job["result"]["summary_markdown"] == "# 原来的笔记"


def test_the_callers_prompt_and_note_mode_reach_the_text_model(client, text_model):
    _task("regen-prompt", note="# 旧", written_from=None)

    client.post(
        "/agent/v1/tasks/regen-prompt/note/regenerate",
        headers=_HEADERS,
        json={"system_prompt": "只列待办", "note_mode": "chapter_coverage",
              "prompt_preset": "todo", "prompt_preset_label": "待办"},
    )

    assert text_model[0]["system_prompt"] == "只列待办"
    assert text_model[0]["note_mode"] == "chapter_coverage"
    result = _stored("regen-prompt")["result"]
    assert result["prompt_preset"] == "todo" and result["prompt_preset_label"] == "待办"


def test_without_a_prompt_the_tasks_own_settings_are_used_again(client, text_model):
    _task(
        "regen-same", note="# 旧", written_from=None,
        queue_options={"system_prompt": "按会议纪要写", "note_mode": "chapter_coverage", "prompt_preset": "meeting"},
    )

    client.post("/agent/v1/tasks/regen-same/note/regenerate", headers=_HEADERS, json={})

    assert text_model[0]["system_prompt"] == "按会议纪要写"
    assert text_model[0]["note_mode"] == "chapter_coverage"
    assert _stored("regen-same")["result"]["prompt_preset"] == "meeting"
