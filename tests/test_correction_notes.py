"""A series' correction document reaches the note writer.

Requirement (owner, 2026-10-18): a folder of recordings of one series can hold
``FluentFlow 校正文档.md`` (names and terms the transcript keeps getting
wrong). When a recording is processed, the document is looked for from its
folder upwards, and the model that writes the note gets it; without one,
nothing changes. The search stops at the home folder.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from backend.core import correction_notes, job_store

DOC = "## 人名\n- 魏鑫：转录常听成魏星\n"


def _task(recording: Path) -> str:
    task_id = f"cn-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status="completed",
                         metadata={"folder_intake": {"original_path": str(recording)}})
    return task_id


def test_the_document_is_found_above_the_recording(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    series = tmp_path / "会议记录"
    (series / "第十五周" / "周一").mkdir(parents=True)
    (series / correction_notes.FILENAME).write_text(DOC, encoding="utf-8")
    task = _task(series / "第十五周" / "周一" / "周会.mp4")

    rules = correction_notes.rules_for(task, "写法规则")

    assert rules.startswith("写法规则") and "魏鑫" in rules and "校正文档" in rules


def test_the_nearest_document_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / correction_notes.FILENAME).write_text("外层", encoding="utf-8")
    (tmp_path / "a" / "b" / correction_notes.FILENAME).write_text("内层", encoding="utf-8")
    assert correction_notes.for_path(tmp_path / "a" / "b" / "x.mp4") == "内层"


def test_without_a_document_the_rules_are_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "x").mkdir()
    assert correction_notes.rules_for(_task(tmp_path / "x" / "r.mp4"), "写法规则") == "写法规则"
    assert correction_notes.rules_for("no-such-task", "写法规则") == "写法规则"


def test_the_search_stops_at_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "x").mkdir(parents=True)
    (tmp_path / correction_notes.FILENAME).write_text(DOC, encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert correction_notes.for_path(home / "x" / "r.mp4") == ""


def test_the_agent_gets_it_in_its_instructions(tmp_path, monkeypatch):
    """The agent writing the note reads the same document."""
    import backend.core.agent_note as an

    monkeypatch.setattr(an.correction_notes, "for_task", lambda task_id, client_id=None: DOC)
    text = an.claude_vision.system_prompt(an.correction_notes.rules_for("t", "写法"))
    assert "魏鑫" in text
