"""A user's change is never replaced by someone else's older copy.

Requirements (owner, 2026-10-18, after a read-only audit):
1. A page that loaded the note or subtitles before they changed elsewhere (in
   Obsidian, by an agent, in another tab) cannot save its old copy over them:
   the save is refused and the page reloads.
2. Putting the previous note back keeps the hand-edited note it puts aside,
   to switch back to.
3. Rewriting the note from the page keeps the note it replaces restorable.
4. A rewrite already paid for is not refused because something unrelated to
   the note changed during the model call; an edit to the note still wins.
5. A rule an agent writes into the note skill is refused when the skill
   changed since the agent read it; feedback recorded at the same moment by
   two agents is all kept.
6. A transcript or subtitle copy changed in the output folder is kept beside,
   not silently replaced.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routers.local_note_regen as local_note_regen
from backend.core import job_store, note_feedback, note_skills, output_folder, visual_note_job
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.routers.local_job_edit import router as edit_router

HEADERS = {"x-fluentflow-client-id": LOCAL_OWNER_ID}


def _task(**result) -> str:
    task_id = f"no-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, result={
        "task_id": task_id, "transcript_text": "第一句。", "summary_markdown": "# 笔记\n\n原来的。\n",
        "raw_segments": [{"start": 0.0, "end": 2.0, "text": "第一句。"}], **result,
    })
    return task_id


def _edit_client() -> TestClient:
    app = FastAPI()
    app.include_router(edit_router)
    return TestClient(app)


# ── 1. the page's old copy ─────────────────────────────────────────────────

def test_the_page_cannot_save_its_old_note_over_a_newer_one():
    task = _task()
    loaded = job_store.get_job(task)["result"]
    # Meanwhile an Obsidian edit or an agent changes the note.
    job_store.save_result_changes(task, loaded, {**loaded, "summary_markdown": "# 笔记\n\n在 Obsidian 里改的。\n"})

    response = _edit_client().patch(f"/jobs/{task}/summary", headers=HEADERS, json={
        "summary_markdown": "# 笔记\n\n页面上的旧副本。\n", "base_version": loaded["note_version"],
    })

    assert response.status_code == 409
    assert "最新的版本" in response.json()["detail"]["message"]
    assert "Obsidian 里改的" in job_store.get_job(task)["result"]["summary_markdown"]


def test_the_page_saves_when_it_edited_the_latest_note():
    task = _task()
    loaded = job_store.get_job(task)["result"]

    first = _edit_client().patch(f"/jobs/{task}/summary", headers=HEADERS, json={
        "summary_markdown": "# 笔记\n\n第一次改。\n", "base_version": loaded["note_version"],
    })
    second = _edit_client().patch(f"/jobs/{task}/summary", headers=HEADERS, json={
        "summary_markdown": "# 笔记\n\n第二次改。\n", "base_version": first.json()["result"]["note_version"],
    })

    assert first.status_code == 200 and second.status_code == 200
    assert "第二次改" in job_store.get_job(task)["result"]["summary_markdown"]


LONG_NOTE = "# 访谈\n\n" + "".join(f"## 第{i}节\n\n第{i}节讲了很多内容，每一段都很长。\n\n" for i in range(1, 80))


def test_the_task_list_preview_saved_as_the_note_is_refused():
    """The task list carries a note's first 240 characters. A page that saves
    that preview back must not replace the whole note (2026-10-22: a 40,000
    character note became 240 in the task, its folder and Feishu)."""
    task = _task(summary_markdown=LONG_NOTE)
    loaded = job_store.get_job(task)["result"]

    response = _edit_client().patch(f"/jobs/{task}/summary", headers=HEADERS, json={
        "summary_markdown": LONG_NOTE[:240], "base_version": loaded["note_version"],
    })

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "preview_not_note"
    assert job_store.get_job(task)["result"]["summary_markdown"] == LONG_NOTE


def test_a_note_cut_short_on_purpose_still_saves():
    """Deleting most of a note and keeping a new short text is an edit, not the preview."""
    task = _task(summary_markdown=LONG_NOTE)
    loaded = job_store.get_job(task)["result"]
    kept = "# 访谈\n\n只留下这一句总结。\n"

    response = _edit_client().patch(f"/jobs/{task}/summary", headers=HEADERS, json={
        "summary_markdown": kept, "base_version": loaded["note_version"],
    })

    assert response.status_code == 200
    assert job_store.get_job(task)["result"]["summary_markdown"] == kept


def test_the_page_cannot_save_old_subtitles_over_newer_ones():
    task = _task()
    loaded = job_store.get_job(task)["result"]
    job_store.save_result_changes(task, loaded, {
        **loaded, "transcript_text": "重新剪过的字幕。",
        "raw_segments": [{"start": 0.0, "end": 1.5, "text": "重新剪过的字幕。"}],
    })

    response = _edit_client().patch(f"/jobs/{task}/transcript", headers=HEADERS, json={
        "transcript_text": "旧的。", "segments": [{"start": 0.0, "end": 2.0, "text": "旧的。"}],
        "base_version": loaded["transcript_version"],
    })

    assert response.status_code == 409
    assert job_store.get_job(task)["result"]["transcript_text"] == "重新剪过的字幕。"


def test_a_note_save_leaves_a_field_written_meanwhile_alone():
    task = _task()
    loaded = job_store.get_job(task)["result"]
    job_store.save_result_changes(task, loaded, {**loaded, "lark_doc_title": "飞书里的标题"})

    response = _edit_client().patch(f"/jobs/{task}/summary", headers=HEADERS, json={
        "summary_markdown": "# 笔记\n\n改了。\n", "base_version": loaded["note_version"],
    })

    assert response.status_code == 200
    assert job_store.get_job(task)["result"]["lark_doc_title"] == "飞书里的标题"


# ── 2. restoring the previous note ─────────────────────────────────────────

def test_putting_the_previous_note_back_keeps_the_hand_edited_one_to_switch_back_to():
    task = _task(
        summary_markdown="# 笔记\n\nClaude 写的，又被我改过。\n",
        summary_edited=True,
        visual_note={"status": "completed", "markdown": "# 笔记\n\nClaude 写的。\n", "promoted": True,
                     "replaced_note": {"previous_markdown": "# 笔记\n\n最早那篇。\n"}},
    )

    visual_note_job.restore_previous_note(task)
    assert "最早那篇" in job_store.get_job(task)["result"]["summary_markdown"]
    visual_note_job.use_generated_note(task)

    result = job_store.get_job(task)["result"]
    assert "又被我改过" in result["summary_markdown"]
    assert result["summary_edited"] is True


# ── 3 and 4. rewriting the note from the page ──────────────────────────────

def _regen_client(monkeypatch, during=None):
    def summarize(transcript, **kwargs):
        if during:
            during()
        return SimpleNamespace(markdown="# 重写的笔记\n\n新的。", requested_mode="auto", resolved_mode="direct",
                               chunk_count=1, segment_count=1, chapter_coverage={"chapters": []})

    monkeypatch.setattr(local_note_regen, "summarize_transcript_with_metadata", summarize)
    monkeypatch.setattr(local_note_regen, "log_event", lambda **kw: None)
    app = FastAPI()
    app.include_router(local_note_regen.router)
    return TestClient(app)


def test_a_page_rewrite_keeps_the_note_it_replaces_restorable(monkeypatch):
    task = _task(summary_markdown="# 笔记\n\n我手改过的。\n", summary_edited=True)

    response = _regen_client(monkeypatch).post("/regenerate-summary", headers=HEADERS,
                                               data={"transcript": "第一句。", "task_id": task})

    assert response.status_code == 200, response.text
    visual_note_job.restore_previous_note(task)
    assert "我手改过的" in job_store.get_job(task)["result"]["summary_markdown"]


def test_a_rewrite_is_kept_when_only_something_else_changed_meanwhile(monkeypatch):
    task = _task()

    def export_finishes():
        now = job_store.get_job(task)["result"]
        job_store.save_result_changes(task, now, {**now, "lark_doc_title": "导出完成"})

    response = _regen_client(monkeypatch, during=export_finishes).post(
        "/regenerate-summary", headers=HEADERS, data={"transcript": "第一句。", "task_id": task})

    assert response.status_code == 200, response.text
    result = job_store.get_job(task)["result"]
    assert "重写的笔记" in result["summary_markdown"] and result["lark_doc_title"] == "导出完成"


def test_an_edit_to_the_note_during_a_rewrite_still_wins(monkeypatch):
    task = _task()

    def user_edits():
        now = job_store.get_job(task)["result"]
        job_store.save_result_changes(task, now, {**now, "summary_markdown": "# 笔记\n\n生成期间我改的。\n"})

    response = _regen_client(monkeypatch, during=user_edits).post(
        "/regenerate-summary", headers=HEADERS, data={"transcript": "第一句。", "task_id": task})

    assert response.status_code == 409
    assert "生成期间我改的" in job_store.get_job(task)["result"]["summary_markdown"]


# ── 5. the note skill and feedback ─────────────────────────────────────────

def test_a_rule_written_on_an_old_skill_is_refused():
    item = note_feedback.record_feedback(task_id="", problem="标题太长", kind="pattern", criterion="标题不超过 20 字")
    read = note_skills.load_note_skill()
    note_skills.save_user_skill(read.text + "\n- 用户自己加的一条。\n")

    with pytest.raises(note_feedback.NoteFeedbackError, match="被改过"):
        note_feedback.apply_rule(skill_text=read.text + "\n- 标题不超过 20 字。\n", rule="标题不超过 20 字",
                                 feedback_ids=[item["id"]], rounds=1, evidence="试过", base_version=read.version)
    assert "用户自己加的一条" in note_skills.load_note_skill().text


def test_feedback_recorded_at_the_same_moment_is_all_kept():
    ready = threading.Barrier(8)
    ids: list[str] = []

    def record(i: int) -> None:
        ready.wait()
        ids.append(note_feedback.record_feedback(task_id="", problem=f"问题 {i}", kind="one_off")["id"])

    threads = [threading.Thread(target=record, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    kept = {item["id"] for item in note_feedback.list_feedback()}
    assert set(ids) <= kept


# ── 6. the folder's copies ─────────────────────────────────────────────────

def test_a_subtitle_copy_fixed_in_the_folder_is_kept_beside(tmp_path):
    source = tmp_path / "srt.txt"
    source.write_text("FluentFlow 的字幕\n", encoding="utf-8")
    target = tmp_path / "会议.srt"
    target.write_text("我在文件夹里改过的字幕\n", encoding="utf-8")
    stamp = time.time() + 30
    import os
    os.utime(target, (stamp, stamp))

    output_folder._refresh_copy(source, target, {"md_mtime": time.time() - 60})

    assert target.read_text(encoding="utf-8") == "FluentFlow 的字幕\n"
    kept = list(tmp_path.glob("会议_我的修改-*.srt"))
    assert len(kept) == 1 and "改过的字幕" in kept[0].read_text(encoding="utf-8")
