"""Third level headings are numbered （一）（二） within each chapter.

Requirements (owner, 2026-10-21):
1. Every third level heading carries （一）（二）…, counted again from （一）
   under each second level heading.
2. A number a heading already has (x.y, 1., （三）) is replaced, not doubled;
   a title that only starts with a number (「3.5小时」「2024年」「1T训练」) keeps it.
3. A link to a renumbered heading still leads to it.
4. Every way a note is written comes out numbered: notes joined from parts,
   a one-part note, and the text model's notes.
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.core import ai_summarizer, note_join, visual_note_job

NOTE = """# 访谈

## 一、开场

### 一开始为什么排斥

见[[#第一天：有灵魂|第一天]]和[[#第一天：有灵魂]]。

### 第一天：有灵魂

### 3.5小时的访谈

## 二、框架

### 1.2 旧编号

### （三）旧的中文编号

### 2024年：o1与R1

```
### 代码里的不算
```

### 1T训练的主要挑战
"""


def test_each_chapter_counts_its_subheadings_from_one():
    out = note_join.number_subheadings(NOTE)
    assert "### （一）一开始为什么排斥" in out
    assert "### （二）第一天：有灵魂" in out
    assert "### （三）3.5小时的访谈" in out
    assert "### （一）旧编号" in out, "a new chapter starts again, and the old number goes"
    assert "### （二）旧的中文编号" in out
    assert "### （三）2024年：o1与R1" in out
    assert "### （四）1T训练的主要挑战" in out
    assert "### 代码里的不算" in out


def test_links_follow_the_renamed_heading():
    out = note_join.number_subheadings(NOTE)
    assert "[[#（二）第一天：有灵魂|第一天]]" in out
    assert "[[#（二）第一天：有灵魂]]" in out


def test_numbering_twice_changes_nothing():
    once = note_join.number_subheadings(NOTE)
    assert note_join.number_subheadings(once) == once


def test_notes_joined_from_parts_and_one_part_notes_are_numbered():
    joined = note_join.join_parts([("", "## 一、甲\n\n### 小节\n"), ("", "## 一、乙\n\n### 小节\n")], title="T")
    assert joined.count("### （一）小节") == 2

    draft = SimpleNamespace(markdown="## 一、甲\n\n### 小节\n")
    import dataclasses

    @dataclasses.dataclass
    class Draft:
        markdown: str

    one = visual_note_job._joined_draft([Draft("## 一、甲\n\n### 小节\n")], [draft])
    assert "### （一）小节" in one.markdown


def test_the_text_models_notes_are_numbered(monkeypatch):
    produced = ai_summarizer.SummaryResult(
        markdown="## 一、甲\n\n### 3.1 小节\n", requested_mode="m", resolved_mode="m", transcript_length=1, chunk_count=1,
    )
    monkeypatch.setattr(ai_summarizer, "_summarize_transcript_with_metadata", lambda transcript, **kwargs: produced)
    assert "### （一）小节" in ai_summarizer.summarize_transcript_with_metadata("x").markdown
