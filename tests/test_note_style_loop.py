"""Preference settings are read/restore only; the old model flow stays retired."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.core import job_store, note_skills
from backend.local_main import create_local_app

OWNER = "local-single-user"
GENERATED = "# 课程笔记\n\n## 词汇表\n\n- 回归：一种方法\n\n## 正文\n\n这节讲回归。"
EDITED = "# 课程笔记\n\n## 正文\n\n这节讲回归（regression，用已知点拟合一条线）。"


@pytest.fixture(autouse=True)
def own_skill_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_NOTE_SKILL_DIR", str(tmp_path / "mine"))


@pytest.fixture()
def client():
    return TestClient(create_local_app(), headers={"X-FluentFlow-Client-Id": OWNER})


@pytest.fixture()
def task():
    task_id = "style-" + uuid.uuid4().hex
    job_store.upsert_job(task_id=task_id, status="completed", client_id=OWNER, stage="done",
                         result={"task_id": task_id, "summary_markdown": GENERATED, "display_title": "回归"})
    yield task_id
    job_store.delete_jobs([task_id], client_id=OWNER)


def _edit(client, task_id, text):
    version = job_store.get_job(task_id, client_id=OWNER)["result"]["note_version"]
    response = client.patch(f"/jobs/{task_id}/summary", json={"summary_markdown": text, "base_version": version})
    assert response.status_code == 200
    return response.json()["result"]


def test_the_generated_note_is_kept_at_the_first_edit_only(client, task):
    _edit(client, task, EDITED)
    result = _edit(client, task, EDITED + "\n\n再改一次。")
    assert result["summary_original_markdown"] == GENERATED


def test_a_newly_written_note_starts_over(client, task):
    from backend.core.visual_note_job import _promotion

    _edit(client, task, EDITED)
    current = job_store.get_job(task, client_id=OWNER)["result"]
    fields, _ = _promotion(current, "# 新写的笔记")
    assert fields["summary_edited"] is False and fields["summary_original_markdown"] is None


def test_reading_settings_does_not_scan_tasks_or_call_a_model(client, monkeypatch):
    from backend.core import claude_text

    def forbidden(*args, **kwargs):
        raise AssertionError("opening settings must only read rules and their history")

    monkeypatch.setattr(job_store, "list_jobs", forbidden)
    monkeypatch.setattr(job_store, "list_job_summaries", forbidden)
    monkeypatch.setattr(claude_text, "claude_chat", forbidden)
    body = client.get("/note-style").json()
    assert body["skill"]["is_default"]
    assert set(body) == {"skill", "history", "rules_log"}


@pytest.mark.parametrize("method,path", [
    ("get", "/note-style/preview-tasks"),
    ("post", "/note-style/candidates/from-edits"),
    ("post", "/note-style/candidates/from-examples"),
    ("post", "/note-style/candidates/old/dismiss"),
    ("post", "/note-style/proposal"),
    ("post", "/note-style/proposal/apply"),
    ("post", "/note-style/proposal/discard"),
])
def test_old_tabs_cannot_start_or_apply_the_retired_flow(client, method, path, tmp_path):
    directory = note_skills.user_skill_dir()
    directory.mkdir()
    pending = directory / "proposal.json"
    pending.write_text('{"status":"ready","skill_text":"old candidate"}')
    assert getattr(client, method)(path).status_code == 410
    assert pending.read_text() == '{"status":"ready","skill_text":"old candidate"}'
    assert note_skills.load_note_skill().is_default


def test_restore_without_a_recording_or_model_keeps_the_replaced_rules(client):
    note_skills.save_user_skill("first preference")
    note_skills.save_user_skill("second preference")
    before = client.get("/note-style").json()
    response = client.post("/note-style/restore", json={
        "name": before["history"][0]["name"], "base_version": before["skill"]["version"],
    })
    assert response.status_code == 200
    assert response.json()["skill"]["text"] == "first preference"
    assert note_skills.skill_history()[0].read_text().strip() == "second preference"


def test_restore_rejects_missing_or_stale_versions_without_writing(client):
    note_skills.save_user_skill("first")
    old_version = note_skills.load_note_skill().version
    note_skills.save_user_skill("written elsewhere")
    before = [p.name for p in note_skills.skill_history()]
    for version in (None, old_version):
        response = client.post("/note-style/restore", json={"use_default": True, "base_version": version})
        assert response.status_code == 409
    assert note_skills.load_note_skill().text == "written elsewhere"
    assert [p.name for p in note_skills.skill_history()] == before


def test_restore_default_follows_future_product_defaults(client, monkeypatch, tmp_path):
    note_skills.save_user_skill("custom")
    response = client.post("/note-style/restore", json={
        "use_default": True, "base_version": note_skills.load_note_skill().version,
    })
    assert response.status_code == 200 and response.json()["skill"]["is_default"]
    changed_default = tmp_path / "default.md"
    changed_default.write_text("new product default")
    monkeypatch.setattr(note_skills, "DEFAULT_SKILL_PATH", changed_default)
    assert note_skills.load_note_skill().text == "new product default"


def test_restore_rejects_path_traversal_and_ambiguous_targets(client):
    version = note_skills.load_note_skill().version
    for payload in ({"name": "../../other.md"}, {"name": "old.md", "use_default": True}, {}):
        assert client.post("/note-style/restore", json={**payload, "base_version": version}).status_code == 400


def test_two_updates_based_on_one_version_cannot_overwrite_each_other():
    from concurrent.futures import ThreadPoolExecutor

    version = note_skills.load_note_skill().version

    def save(text):
        try:
            return note_skills.save_user_skill(text, base_version=version)
        except note_skills.NoteSkillConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["one", "two"]))
    assert sum(result is not None for result in results) == 1
    assert len(note_skills.skill_history()) == 1
