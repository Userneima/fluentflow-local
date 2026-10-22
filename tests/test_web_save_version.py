"""The web editor's saves always name the version they were made on.

Requirements (owner, 2026-10-23, after the 240-character preview was saved
over a 40,000-character note; decision 6.30):

1. A note or transcript save from the web editor without a version is refused
   and nothing changes; the page is told to reload.
2. A save naming the version the task holds goes through.
3. A save naming an older version is refused as stale.
4. Other ways of writing the note are not affected: the agent's saves, the
   rewrites and restores run through other routes and are covered by their
   own suites (test_agent_note.py, test_note_history.py, test_no_overwrite.py).
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import job_store
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.routers.local_job_edit import router as edit_router

HEADERS = {"x-fluentflow-client-id": LOCAL_OWNER_ID}
NOTE = "# 笔记\n\n原来的正文，有好几段。\n"
SEGMENTS = [{"start": 0.0, "end": 2.0, "text": "第一句。"}]


def _task() -> str:
    task_id = f"wsv-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, result={
        "task_id": task_id, "transcript_text": "第一句。", "summary_markdown": NOTE, "raw_segments": SEGMENTS,
    })
    return task_id


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(edit_router)
    return TestClient(app)


def _note(task_id: str) -> str:
    return job_store.get_job(task_id)["result"]["summary_markdown"]


def _transcript(task_id: str) -> str:
    return job_store.get_job(task_id)["result"]["transcript_text"]


def test_a_note_save_without_a_version_is_refused_and_the_note_is_kept():
    task = _task()

    response = _client().patch(f"/jobs/{task}/summary", headers=HEADERS, json={"summary_markdown": "# 笔记\n\n别的。\n"})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "version_required"
    assert "刷新页面" in response.json()["detail"]["message"]
    assert _note(task) == NOTE


def test_an_empty_version_counts_as_none():
    task = _task()

    response = _client().patch(f"/jobs/{task}/summary", headers=HEADERS, json={"summary_markdown": "# 别的\n", "base_version": ""})

    assert response.status_code == 409
    assert _note(task) == NOTE


def test_a_transcript_save_without_a_version_is_refused_and_the_transcript_is_kept():
    task = _task()

    response = _client().patch(f"/jobs/{task}/transcript", headers=HEADERS, json={
        "transcript_text": "改过的一句。", "segments": [{"start": 0.0, "end": 2.0, "text": "改过的一句。"}],
    })

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "version_required"
    assert _transcript(task) == "第一句。"


def test_saves_naming_the_current_version_go_through():
    task = _task()
    loaded = job_store.get_job(task)["result"]

    note = _client().patch(f"/jobs/{task}/summary", headers=HEADERS, json={
        "summary_markdown": "# 笔记\n\n改好的正文。\n", "base_version": loaded["note_version"],
    })
    transcript = _client().patch(f"/jobs/{task}/transcript", headers=HEADERS, json={
        "transcript_text": "改过的一句。", "segments": [{"start": 0.0, "end": 2.0, "text": "改过的一句。"}],
        "base_version": loaded["transcript_version"],
    })

    assert note.status_code == 200
    assert transcript.status_code == 200
    assert _note(task) == "# 笔记\n\n改好的正文。\n"
    assert _transcript(task) == "改过的一句。"


def test_saves_naming_an_older_version_are_refused_as_stale():
    task = _task()
    loaded = job_store.get_job(task)["result"]
    job_store.save_result_changes(task, loaded, {
        **loaded, "summary_markdown": "# 笔记\n\n在 Obsidian 里改的。\n", "transcript_text": "别处改的一句。",
        "raw_segments": [{"start": 0.0, "end": 2.0, "text": "别处改的一句。"}],
    })

    note = _client().patch(f"/jobs/{task}/summary", headers=HEADERS, json={
        "summary_markdown": "# 旧副本\n", "base_version": loaded["note_version"],
    })
    transcript = _client().patch(f"/jobs/{task}/transcript", headers=HEADERS, json={
        "transcript_text": "旧副本。", "segments": SEGMENTS, "base_version": loaded["transcript_version"],
    })

    assert note.status_code == 409 and note.json()["detail"]["code"] == "stale"
    assert transcript.status_code == 409 and transcript.json()["detail"]["code"] == "stale"
    assert "Obsidian 里改的" in _note(task)
    assert _transcript(task) == "别处改的一句。"
