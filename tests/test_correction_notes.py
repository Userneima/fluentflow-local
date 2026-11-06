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


def test_documents_on_every_level_are_read_together_the_nearest_first(tmp_path, monkeypatch):
    """A topic list above a series list: a recording of the series needs both,
    and the series' own spelling comes first (to-do, 2026-10-24)."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / correction_notes.FILENAME).write_text("外层", encoding="utf-8")
    (tmp_path / "a" / "b" / correction_notes.FILENAME).write_text("内层", encoding="utf-8")
    assert correction_notes.for_path(tmp_path / "a" / "b" / "x.mp4") == "内层\n\n外层"


def test_together_they_stay_within_the_total_and_the_nearest_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    for folder, mark in (("a", "上"), ("a/b", "中"), ("a/b/c", "下")):
        (tmp_path / folder / correction_notes.FILENAME).write_text(mark * 5000, encoding="utf-8")
    text = correction_notes.for_path(tmp_path / "a" / "b" / "c" / "x.mp4")
    assert text.startswith("下" * correction_notes.MAX_CHARS)
    assert len(text.replace("\n", "")) == correction_notes.MAX_TOTAL_CHARS
    assert "上" not in text


def test_a_link_task_reads_the_list_of_where_its_folder_will_be(tmp_path, monkeypatch):
    """A video from a link gets its folder under the link root after its note;
    its first note still reads the lists there (10-18 review, item 10)."""
    from backend.core import output_folder

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    root = tmp_path / "Movies" / "FluentFlow"
    root.mkdir(parents=True)
    monkeypatch.setenv(output_folder.LINK_ROOT_ENV, str(root))
    (root / correction_notes.FILENAME).write_text(DOC, encoding="utf-8")
    task_id = f"cn-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status="completed",
                         metadata={"video_source_url": "https://www.bilibili.com/video/BV1xx"})
    assert "魏鑫" in correction_notes.for_task(task_id)

    # Sorted into a topic folder with its own list: both are read.
    topic = root / "2.AI" / "某期访谈"
    topic.mkdir(parents=True)
    output_folder._add_owner(topic, task_id, "")
    (root / "2.AI" / correction_notes.FILENAME).write_text("话题词", encoding="utf-8")
    text = correction_notes.for_task(task_id)
    assert text.startswith("话题词") and "魏鑫" in text


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
