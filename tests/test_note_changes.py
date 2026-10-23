"""What each change to a note changed, in 过程文件/修改记录.md.

Requirements (owner, 2026-10-23, decision 6.36):
1. One change of the note is one entry, whichever places it then reaches: an
   edit on the page is not logged again when the md and the Feishu document
   follow it, and an md edited in Obsidian and taken in is one entry, not one
   for the md and one for the task's note.
2. An entry says when, what made it (the writer's declaration, 未记录 when
   none), the chapter (nearest ## or ### heading), the words before and after.
   In a long paragraph only the changed words, with some words around them.
   Several changes at once are each listed.
3. A change to more than half the note, or a sharp drop in length, is one
   line with the length before and after, no words.
4. The newest entry is on top, under a line saying what the file is, that the
   whole earlier versions are in 历史版本 and how to restore one.
5. Writing the log never fails the save.
6. No 笔记_上一版.md is written beside the md any more.
7. A push to Feishu keeps no Feishu-side version when the document was not
   edited in Feishu since the last push; it keeps one when it was.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from backend.core import feishu_sync, job_store, note_changes, note_versions, output_folder
from tests.test_feishu_chapter_update import FakeDoc
from tests.test_note_history import (  # noqa: F401 - _quiet is an autouse fixture
    _in_feishu,
    _quiet,
    _result,
    _task,
    _web_edit,
)
from tests.test_output_folder_edits import pdfs  # noqa: F401

pytestmark = pytest.mark.usefixtures("pdfs")

LONG = "这一段讲的是训练的流程，" * 12
NOTE = f"""# 访谈

## 一、开场

开场的话。

## 二、框架

### （一）分工

她说团队只研究小王学（存疑）。

{LONG}后面还有一句收尾。

![架构图](/jobs/{{task}}/artifacts/frame?file=note_0001.jpg)
"""


def _log(md: Path) -> str:
    path = md.parent / output_folder.PROCESS_DIRNAME / note_changes.FILENAME
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _entries(md: Path) -> list[str]:
    return ["## " + part for part in _log(md).split("\n## ")[1:]]


def _note(task: str) -> str:
    return _result(task)["summary_markdown"]


def _edit_md(md: Path, old: str, new: str) -> None:
    md.write_text(md.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    stamp = time.time() + 30
    os.utime(md, (stamp, stamp))


# ── 1. one change, one entry ───────────────────────────────────────────────

def test_one_character_changed_on_the_page_is_one_entry_saying_where_what_and_who(tmp_path, monkeypatch):
    task, md = _task(tmp_path, NOTE)
    doc = FakeDoc()
    _in_feishu(task, doc, monkeypatch)

    _web_edit(task, _note(task).replace("开场的话。", "开场的诂。"))
    output_folder.sync(task)
    feishu_sync.push(task, runner=doc)

    entries = _entries(md)
    assert len(entries) == 1, _log(md)
    assert "网页编辑" in entries[0]
    assert "「一、开场」" in entries[0]
    assert "原文：开场的话。" in entries[0] and "改成：开场的诂。" in entries[0]


def test_an_obsidian_edit_taken_in_is_one_entry_not_one_per_place(tmp_path):
    task, md = _task(tmp_path, NOTE)

    _edit_md(md, "只研究小王学（存疑）", "只研究强化学习（midtrain）")
    output_folder.sync(task)
    output_folder.sync(task)

    entries = _entries(md)
    assert len(entries) == 1, _log(md)
    assert "Obsidian改了md" in entries[0]
    assert "「二、框架 › （一）分工」" in entries[0], "a ### heading with the chapter it is in"
    assert "只研究小王学（存疑）" in entries[0] and "只研究强化学习（midtrain）" in entries[0]


def test_saving_the_same_note_again_adds_nothing(tmp_path):
    task, md = _task(tmp_path, NOTE)
    _web_edit(task, _note(task).replace("开场的话。", "开场的话！"))
    _web_edit(task, _note(task))
    job_store.update_job_result(task, {**_result(task), "lark_sync_error": "无关的写入"})

    assert len(_entries(md)) == 1


# ── 2. what an entry shows ─────────────────────────────────────────────────

def test_a_long_paragraph_shows_only_the_changed_words_with_some_around_them(tmp_path):
    task, md = _task(tmp_path, NOTE)

    _web_edit(task, _note(task).replace("后面还有一句收尾", "后面还有一句结尾"))

    entry = _entries(md)[0]
    line = next(line for line in entry.split("\n") if "原文：" in line)
    assert "收尾" in line and len(line) < 100, line
    assert line.count("这一段讲的是训练的流程") < 4


def test_several_changes_at_once_are_each_listed(tmp_path):
    task, md = _task(tmp_path, NOTE)

    _web_edit(task, _note(task).replace("开场的话。", "开场白。").replace("小王学（存疑）", "强化学习"))

    entry = _entries(md)[0]
    assert "「一、开场」" in entry and "「二、框架 › （一）分工」" in entry
    assert entry.count("原文：") == 2


def test_an_added_paragraph_is_listed_as_added(tmp_path):
    task, md = _task(tmp_path, NOTE)

    _web_edit(task, _note(task).replace("开场的话。", "开场的话。\n\n> 一个疑问：这里说的是哪年？"))

    entry = _entries(md)[0]
    assert "「一、开场」新增：> 一个疑问：这里说的是哪年？" in entry
    assert "原文：" not in entry


def test_spacing_alone_is_not_listed_as_changes(tmp_path):
    table = "| 项 | 值 |\n|---|---|\n| 甲 | 一 |\n"
    task, md = _task(tmp_path, NOTE.replace("开场的话。", "开场的话。\n\n" + table))

    _web_edit(task, _note(task).replace(table, "| 项   | 值   |\n| ---- | ---- |\n| 甲   | 一   |\n"))

    entry = _entries(md)[0]
    assert "原文" not in entry and "只改了空格和对齐" in entry


def test_a_change_nobody_declared_says_not_recorded(tmp_path):
    task, md = _task(tmp_path, NOTE)

    job_store.update_job_result(task, {**_result(task), "summary_markdown": _note(task).replace("开场的话。", "开场。")})

    assert note_versions.NOT_RECORDED in _entries(md)[0]


# ── 3. whole note replaced ─────────────────────────────────────────────────

def test_a_note_rewritten_whole_is_one_line_with_the_lengths(tmp_path):
    task, md = _task(tmp_path, NOTE)
    before = note_versions.chars(_note(task))
    rewritten = "# 访谈\n\n## 全新的一章\n\n" + "完全不同的内容。" * 30 + "\n"

    _web_edit(task, rewritten)

    entry = _entries(md)[0]
    assert f"整篇换掉：从 {before} 字到 {note_versions.chars(rewritten)} 字。" in entry
    assert "原文" not in entry and "完全不同的内容" not in entry


def test_a_sharp_drop_is_one_line_with_the_lengths(tmp_path):
    big = NOTE + "\n## 三、长章\n\n" + "很长的内容。" * 400 + "\n"
    task, md = _task(tmp_path, big)
    before = note_versions.chars(_note(task))

    fragment = "# 访谈\n\n只剩一句。\n"

    _web_edit(task, fragment)

    entry = _entries(md)[0]
    assert f"字数骤降：从 {before} 字到 {note_versions.chars(fragment)} 字。" in entry
    assert "只剩一句" not in entry


# ── 4. order and header ────────────────────────────────────────────────────

def test_the_newest_entry_is_on_top_under_the_header(tmp_path):
    task, md = _task(tmp_path, NOTE)

    _web_edit(task, _note(task).replace("开场的话。", "第一次改。"))
    _web_edit(task, _note(task).replace("第一次改。", "第二次改。"))

    log = _log(md)
    assert log.startswith(note_changes.HEADER)
    assert "历史版本" in note_changes.HEADER and "restore_note_version" in note_changes.HEADER
    entries = _entries(md)
    assert len(entries) == 2
    assert "改成：第二次改。" in entries[0] and "改成：第一次改。" in entries[1]


def test_a_note_with_no_folder_keeps_its_log_beside_its_versions():
    import uuid

    task = f"nc-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task, status="completed", result={"summary_markdown": NOTE, "summary_status": "completed"})
    try:
        job_store.update_job_result(task, {**_result(task), "summary_markdown": NOTE.replace("开场的话。", "开场。")})
        md = note_versions.md_for(task, _result(task))
        log = note_versions.folder_for(md).parent / note_changes.FILENAME
        assert "改成：开场。" in log.read_text(encoding="utf-8")
    finally:
        job_store.delete_jobs([task])


# ── 5. never fails the save ────────────────────────────────────────────────

def test_a_log_that_cannot_be_written_does_not_fail_the_save(tmp_path, monkeypatch):
    task, md = _task(tmp_path, NOTE)

    def broken(path, text):
        raise OSError("disk full")

    monkeypatch.setattr(note_changes, "record", broken)
    _web_edit(task, _note(task).replace("开场的话。", "开场。"))

    assert "开场。" in _note(task)


# ── 6. no previous-version copy beside the md ──────────────────────────────

def test_no_previous_copy_is_written_beside_the_md(tmp_path):
    task, md = _task(tmp_path, NOTE)
    _web_edit(task, _note(task).replace("开场的话。", "开场。"))
    output_folder.sync(task)
    _edit_md(md, "开场。", "开场白。")
    output_folder.sync(task)

    assert not list(md.parent.glob("*上一版*"))
    assert "上一版" not in output_folder.MD_HINT and "修改记录" in output_folder.MD_HINT


# ── 7. the Feishu side kept only when edited in Feishu ─────────────────────

def _feishu_side(md: Path) -> list[note_versions.Version]:
    return [v for v in note_versions.kept(md) if v.side == note_versions.FEISHU]


def test_a_push_over_a_document_nobody_edited_in_feishu_keeps_no_feishu_copy(tmp_path, monkeypatch):
    task, md = _task(tmp_path, NOTE)
    doc = FakeDoc()
    _in_feishu(task, doc, monkeypatch)

    _web_edit(task, _note(task).replace("开场的话。", "开场。"))
    assert feishu_sync.push(task, runner=doc)
    _web_edit(task, _note(task).replace("开场。", "开场白。"))
    assert feishu_sync.push(task, runner=doc)

    assert any("开场白。" in text for text in doc.texts())
    assert _feishu_side(md) == []


def test_a_push_over_a_document_edited_in_feishu_keeps_the_feishu_copy(tmp_path, monkeypatch):
    task, md = _task(tmp_path, NOTE)
    doc = FakeDoc()
    _in_feishu(task, doc, monkeypatch)

    # Edited in Feishu, then the same chapter edited on the page later: the
    # push writes the page's words over Feishu's.
    doc.edit("开场的话。", "开场的话，飞书里补的。")
    feishu_sync.pull(task, modified_at=doc.modified, runner=doc)
    _web_edit(task, _note(task).replace("开场的话，飞书里补的。", "开场，网页上改的。"))
    feishu_sync.push(task, runner=doc)

    kept = _feishu_side(md)
    assert len(kept) == 1
    assert "飞书里补的" in kept[0].path.read_text(encoding="utf-8")


def test_the_agent_is_told_where_the_change_log_is(tmp_path):
    from tests.test_note_history import AGENT, _agent

    task, md = _task(tmp_path, NOTE)
    _web_edit(task, _note(task).replace("开场的话。", "开场。"))

    listed = _agent().get(f"/agent/v1/tasks/{task}/note/versions", headers=AGENT).json()

    assert Path(listed["change_log"]).read_text(encoding="utf-8") == _log(md)
