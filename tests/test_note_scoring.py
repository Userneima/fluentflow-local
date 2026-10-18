"""Scoring a note against a fixed test sheet.

Requirements (owner, 2026-10-18): coverage of the confirmed key points (core
and detail apart, a partly covered point counts half), wrong statements per
thousand characters by kind, and whether the sheet's questions can be
answered from the note alone. The checker's answers are parsed and counted;
an answer that cannot be read is counted as unjudged, never as covered.
"""

from __future__ import annotations

import json

from backend.core import note_scoring as ns

SHEET = """# 考题：周会

## 读者打开笔记最想马上知道的问题
1. 定了什么？（两周 sprint）
2. 谁做什么？

## 要点清单

### 核心
- [00:27] 改成两周 sprint
- [18:15] 云泓负责权限清单
- [30:00] CI 太慢还没解决
### 细节
- [40:00] 公众号浏览量低
"""


def test_the_sheet_is_read_as_questions_core_and_detail():
    sheet = ns.parse_sheet(SHEET)
    assert len(sheet.questions) == 2 and sheet.questions[0].startswith("定了什么")
    assert [p.time for p in sheet.core] == ["00:27", "18:15", "30:00"]
    assert sheet.detail[0].text == "公众号浏览量低"


def test_coverage_counts_partly_covered_as_half_and_unreadable_as_unjudged():
    def chat(system, user):
        return json.dumps({"items": [{"n": 1, "verdict": "covered"}, {"n": 2, "verdict": "partial"}]})

    result = ns.coverage(chat, "笔记", ns.parse_sheet(SHEET).core)

    assert [i["verdict"] for i in result["items"]] == ["covered", "partial", "unjudged"]
    assert result["score"] == 0.75 and result["unjudged"] == 1


def test_errors_are_counted_per_thousand_characters_by_kind_and_not_twice():
    def chat(system, user):
        return '前言 {"errors": [{"quote": "魏星主持", "type": "人名数字错", "why": "应为魏鑫"},' \
               ' {"quote": "魏星主持", "type": "人名数字错", "why": "重复"},' \
               ' {"quote": "决定上线", "type": "编造", "why": "没说过"}]} 后记'

    result = ns.errors(chat, "字" * 2000, "转录")

    assert result["count"] == 2 and result["per_1000_chars"] == 1.0
    assert result["by_kind"]["人名数字错"] == 1 and result["by_kind"]["编造"] == 1


def test_a_long_transcript_is_checked_in_windows():
    calls = []
    transcript = "\n".join(f"[{i:03d}:00] 第 {i} 句" + "字" * 200 for i in range(400))

    def chat(system, user):
        calls.append(user)
        return '{"errors": []}'

    result = ns.errors(chat, "笔记", transcript)
    assert result["windows"] == len(calls) > 1


def test_questions_are_answered_from_the_note():
    def chat(system, user):
        assert "两周 sprint" in user
        return json.dumps({"items": [{"n": 1, "answer": "两周 sprint", "verdict": "answered"},
                                     {"n": 2, "answer": "", "verdict": "no"}]})

    result = ns.questions(chat, "笔记", ns.parse_sheet(SHEET).questions)
    assert result["score"] == 0.5


def test_the_report_names_what_is_missing():
    sheet = ns.parse_sheet(SHEET)

    def chat(system, user):
        if "要点" in user:
            return json.dumps({"items": [{"n": i, "verdict": "missing"} for i in range(1, 4)]})
        if "问题" in user:
            return json.dumps({"items": [{"n": 1, "verdict": "answered", "answer": "a"}]})
        return '{"errors": []}'

    result = ns.score(chat, sheet, "笔记", "转录", checker="fake")
    text = ns.report_markdown("周会", "现有笔记", result)
    assert "核心覆盖 0%" in text and "云泓负责权限清单" in text and "判卷：fake" in text


def test_the_checker_gets_the_correction_document():
    seen = []

    def chat(system, user):
        seen.append(user)
        return '{"errors": []}'

    ns.errors(chat, "笔记", "转录", corrections="- 魏鑫：常听成魏星")
    assert "校正文档" in seen[0] and "魏鑫" in seen[0]
