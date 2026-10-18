"""Score a note against a fixed test sheet ("考题").

Requirement (owner, 2026-10-18; to-do batch 1, item 0): judge every change to
how notes are written on the same few real recordings, by three numbers that
can be compared from one version to the next:

- coverage: the share of the sheet's key points the note contains, core and
  detail apart. The points are a fixed list the owner confirmed, not chosen by
  the checker each time (the coverage check picked 60, 84 and 73 points for one
  note on three runs, so its numbers did not compare);
- error rate: wrong statements in the note per thousand characters, by kind
  (made up; wrong name or number; said by someone else; a misheard word
  written as fact);
- the reader's questions: whether the sheet's two or three questions can be
  answered from the note alone.

A model that did not write the note does the judging, one small request at a
time; this module only asks, parses and counts. ``chat(system, user) -> text``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

Chat = Callable[[str, str], str]

POINT_BATCH = 25
TRANSCRIPT_WINDOW = 60_000
WINDOW_OVERLAP = 2_000
ERROR_KINDS = ("编造", "人名数字错", "张冠李戴", "听错当事实", "其他")
COVERAGE_WEIGHT = {"covered": 1.0, "partial": 0.5, "missing": 0.0}
ANSWER_WEIGHT = {"answered": 1.0, "partial": 0.5, "no": 0.0}


@dataclass(frozen=True)
class Point:
    time: str
    text: str


@dataclass
class Sheet:
    questions: list[str] = field(default_factory=list)
    core: list[Point] = field(default_factory=list)
    detail: list[Point] = field(default_factory=list)


_POINT = re.compile(r"^-\s*\[([0-9:]+)\]\s*(.+)$")


def parse_sheet(text: str) -> Sheet:
    sheet = Sheet()
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            section = "questions" if "问题" in line else ("points" if "要点" in line else "")
            continue
        if line.startswith("### "):
            section = "core" if "核心" in line else ("detail" if "细节" in line else section)
            continue
        if section == "questions" and re.match(r"^\d+\.\s", line):
            sheet.questions.append(re.sub(r"^\d+\.\s*", "", line))
        elif section in ("core", "detail"):
            match = _POINT.match(line)
            if match:
                getattr(sheet, section).append(Point(match.group(1), match.group(2).strip()))
    return sheet


def _json(raw: str) -> dict[str, Any] | None:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _ask(chat: Chat, system: str, user: str) -> dict[str, Any] | None:
    for _ in range(2):
        parsed = _json(chat(system, user) or "")
        if parsed is not None:
            return parsed
    return None


COVERAGE_SYSTEM = (
    "你是笔记评审，不是作者。给你一篇笔记和一组要点，逐条判断笔记有没有写到这个要点的意思："
    "covered（意思写到了，说法可以不同）、partial（只写到一部分，或缺了关键的名字、数字、条件）、"
    "missing（没写到）。只看笔记里有没有，不判断笔记好坏。"
    '只回一个 JSON：{"items": [{"n": 序号, "verdict": "covered|partial|missing", "where": "笔记里对应的一小段原文，没写到就空"}]}'
)


def coverage(chat: Chat, note: str, points: list[Point]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    failed = 0
    for start in range(0, len(points), POINT_BATCH):
        batch = points[start:start + POINT_BATCH]
        listing = "\n".join(f"{start + i + 1}. [{p.time}] {p.text}" for i, p in enumerate(batch))
        answer = _ask(chat, COVERAGE_SYSTEM, f"--- 笔记 ---\n\n{note}\n\n--- 要点 ---\n\n{listing}")
        verdicts = {int(item.get("n", 0)): item for item in (answer or {}).get("items") or [] if isinstance(item, dict)}
        for i, point in enumerate(batch, start=start + 1):
            found = verdicts.get(i) or {}
            verdict = str(found.get("verdict") or "")
            if verdict not in COVERAGE_WEIGHT:
                failed += 1
                verdict = "unjudged"
            items.append({"n": i, "time": point.time, "point": point.text, "verdict": verdict,
                          "where": str(found.get("where") or "")[:200]})
    judged = [item for item in items if item["verdict"] in COVERAGE_WEIGHT]
    score = sum(COVERAGE_WEIGHT[item["verdict"]] for item in judged) / len(judged) if judged else None
    return {"score": score, "items": items, "unjudged": failed}


ERROR_SYSTEM = (
    "你是笔记评审，不是作者。给你一段录像的转录稿（可能只是其中一段，标了时间）和一篇笔记。"
    "找出笔记里和这段转录稿对不上的说法，每处标一种类型：编造（转录稿里没有这个说法）、"
    "人名数字错（人名、产品名、数字、日期写错）、张冠李戴（把一个人说的话算到另一个人头上）、"
    "听错当事实（把转录稿里明显听错的词当成真的写进去，没标存疑）、其他。"
    "只报确实有错的；说法不同但意思对的不算错；笔记标了「（存疑）」的不算错。"
    "转录稿里的说话人标号（说话人 A、B……）可能分错了，笔记把一句话算给哪个标号，和转录稿的标号对不上不算错，"
    "只有把明显是另一方的话（比如把提问算成回答）算错了人才算张冠李戴。"
    "笔记写明来自画面（截图、字幕、屏幕）的内容，转录稿里没有不算编造。"
    "如果给你的只是转录稿的一段，只判断笔记里明显在讲这一段内容的说法，别的不管，也不要因为这段里找不到就判编造。"
    '只回一个 JSON：{"errors": [{"quote": "笔记里的原句（尽量短）", "type": "类型", "why": "一句话说明", "time": "转录稿里对应的时间，没有就空"}]}'
)


def _windows(transcript: str) -> list[str]:
    if len(transcript) <= TRANSCRIPT_WINDOW:
        return [transcript]
    windows, start = [], 0
    while start < len(transcript):
        end = min(len(transcript), start + TRANSCRIPT_WINDOW)
        cut = transcript.rfind("\n", start, end) if end < len(transcript) else end
        end = cut if cut > start else end
        windows.append(transcript[start:end])
        if end >= len(transcript):
            break
        start = max(end - WINDOW_OVERLAP, start + 1)
    return windows


def errors(chat: Chat, note: str, transcript: str, *, corrections: str = "") -> dict[str, Any]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    windows = _windows(transcript)
    failed = 0
    for index, window in enumerate(windows, start=1):
        label = f"（转录稿第 {index}/{len(windows)} 段）" if len(windows) > 1 else ""
        known = (
            f"--- 用户的校正文档（转录里这些听法，按这里的写法是对的，不算错） ---\n\n{corrections}\n\n"
            if corrections.strip() else ""
        )
        answer = _ask(chat, ERROR_SYSTEM, f"{known}--- 转录稿{label} ---\n\n{window}\n\n--- 笔记 ---\n\n{note}")
        if answer is None:
            failed += 1
            continue
        for item in answer.get("errors") or []:
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote") or "").strip()
            key = re.sub(r"\s+", "", quote)[:40]
            if not quote or key in seen:
                continue
            seen.add(key)
            kind = str(item.get("type") or "其他")
            found.append({"quote": quote[:200], "type": kind if kind in ERROR_KINDS else "其他",
                          "why": str(item.get("why") or "")[:200], "time": str(item.get("time") or "")})
    by_kind = {kind: sum(1 for item in found if item["type"] == kind) for kind in ERROR_KINDS}
    per_thousand = round(len(found) / max(len(note), 1) * 1000, 2)
    return {"count": len(found), "per_1000_chars": per_thousand, "by_kind": by_kind, "items": found,
            "windows": len(windows), "failed_windows": failed}


QUESTION_SYSTEM = (
    "你是一个第一次看这篇笔记的读者，只能看笔记，不能看录像。对每个问题，用笔记里的内容回答，"
    "再判断笔记能不能答上：answered（答得完整）、partial（只答上一部分，或要翻很久、拼好几处才能答）、"
    "no（笔记里找不到答案）。问题括号里是出题人给的参考答案，用来对照你从笔记里答出的内容。"
    '只回一个 JSON：{"items": [{"n": 序号, "answer": "从笔记里答出的内容，一两句", "verdict": "answered|partial|no"}]}'
)


def questions(chat: Chat, note: str, asked: list[str]) -> dict[str, Any]:
    listing = "\n".join(f"{i}. {q}" for i, q in enumerate(asked, start=1))
    answer = _ask(chat, QUESTION_SYSTEM, f"--- 笔记 ---\n\n{note}\n\n--- 问题 ---\n\n{listing}")
    verdicts = {int(item.get("n", 0)): item for item in (answer or {}).get("items") or [] if isinstance(item, dict)}
    items = []
    for i, question in enumerate(asked, start=1):
        found = verdicts.get(i) or {}
        verdict = str(found.get("verdict") or "")
        items.append({"n": i, "question": question, "answer": str(found.get("answer") or "")[:300],
                      "verdict": verdict if verdict in ANSWER_WEIGHT else "unjudged"})
    judged = [item for item in items if item["verdict"] in ANSWER_WEIGHT]
    score = sum(ANSWER_WEIGHT[item["verdict"]] for item in judged) / len(judged) if judged else None
    return {"score": score, "items": items}


def score(chat: Chat, sheet: Sheet, note: str, transcript: str, *, checker: str = "", corrections: str = "") -> dict[str, Any]:
    return {
        "checker": checker,
        "note_chars": len(note),
        "coverage_core": coverage(chat, note, sheet.core),
        "coverage_detail": coverage(chat, note, sheet.detail),
        "errors": errors(chat, note, transcript, corrections=corrections),
        "questions": questions(chat, note, sheet.questions),
    }


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def summary_line(result: dict[str, Any]) -> str:
    return (
        f"核心覆盖 {_pct(result['coverage_core']['score'])}　细节覆盖 {_pct(result['coverage_detail']['score'])}　"
        f"错误 {result['errors']['count']} 处（每千字 {result['errors']['per_1000_chars']}）　"
        f"问题答上 {_pct(result['questions']['score'])}"
    )


def report_markdown(name: str, label: str, result: dict[str, Any]) -> str:
    lines = [f"# 评分：{name}（{label}）", "", summary_line(result), "",
             f"判卷：{result.get('checker') or '未注明'}；笔记 {result['note_chars']} 字。", ""]
    lines += ["## 问题", ""]
    for item in result["questions"]["items"]:
        lines.append(f"- {item['n']}. {item['verdict']}：{item['answer'] or '（答不上）'}")
    lines += ["", "## 错误", ""]
    by_kind = "、".join(f"{k} {v}" for k, v in result["errors"]["by_kind"].items() if v) or "无"
    lines.append(f"按类型：{by_kind}")
    for item in result["errors"]["items"]:
        lines.append(f"- [{item['type']}] {item['quote']}　→ {item['why']}" + (f"（{item['time']}）" if item["time"] else ""))
    for title, key in (("核心要点没写到或只写到一部分", "coverage_core"), ("细节要点没写到", "coverage_detail")):
        missing = [i for i in result[key]["items"] if i["verdict"] in ("missing", "partial", "unjudged")]
        lines += ["", f"## {title}（{len(missing)} 条）", ""]
        lines += [f"- [{i['time']}] {i['verdict']}：{i['point']}" for i in missing]
    return "\n".join(lines) + "\n"


__all__ = ["Point", "Sheet", "coverage", "errors", "parse_sheet", "questions", "report_markdown", "score", "summary_line"]
