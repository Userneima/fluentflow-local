"""What was done to a recording, written for the person who recorded it.

Requirement (owner, 2026-10-15). Every judgement the cut made is shown on the
task: which gaps were removed, which silent stretches were kept because the
picture mattered, which talk was taken out, which quiet words were saved. The
user sees what the product did, and can say where it was wrong.

The program lists the facts (``facts``): each with its time in the original
recording, what happened and why. A model organises them for reading
(``organize``): groups, their titles, what is open and what is folded, one
sentence per item. It refers to facts by id and never writes a time itself, so
every time on the page is one the program measured. A fact it leaves out is not
lost: it goes into a last, folded group. Without a model the facts are grouped
by kind (``fallback``).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

Chat = Callable[[str, str], str]

REQUEST_DEADLINE_SECONDS = 300.0
# Quiet words saved from the cut listed one by one, the longest first; the
# rest are counted in one line.
MAX_SPOKEN_ITEMS = 30
CONTEXT_CHARS = 40
OTHER_TITLE = "其他处理"

SYSTEM = """你会读到 FluentFlow 处理一条录像时做过的每一件事，每行一条，开头的方括号是编号。
把它们整理成给录像主人看的处理说明：他想知道产品对他的录像做了什么、为什么，哪里可能做错了。

- 分成几组，每组一个短标题；他最可能关心的放前面：他自己改过的、去掉的有人说话的内容、因为画面重要而保留的空白、很长的空白。理由里写着"你说……"的是他自己改的，照实说。
- 零碎、重复、没什么可看的（大量短停顿、说得很轻被保住的零碎字）放后面，并把这组 folded 设为 true（默认折叠）。
- 每条用一句话（不超过 40 字）说清做了什么、为什么，用大白话，可以借助前后文说明这段在干什么。
- 用编号引用条目，不要自己写时间点，时间由程序给出。不重要的条目可以不列，没列的会自动放进"其他处理"并折叠。
- summary 用一两句话概括这次处理的整体情况，不写数字。

只回一个 JSON 对象：
{"summary": "…", "sections": [{"title": "…", "folded": false, "items": [{"ref": "编号", "text": "一句话"}]}]}"""


def _clock(seconds: float) -> str:
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _length(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.1f} 秒"
    total = int(round(seconds))
    if total < 60:
        return f"{total} 秒"
    minutes, secs = divmod(total, 60)
    return f"{minutes} 分 {secs} 秒" if secs else f"{minutes} 分钟"


def _seconds(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _line_before(segments: list[dict[str, Any]], at: float) -> str:
    before = [seg for seg in segments if _seconds(seg.get("end")) <= at + 0.05]
    return str(before[-1].get("text") or "").strip()[-CONTEXT_CHARS:] if before else ""


def _line_after(segments: list[dict[str, Any]], at: float) -> str:
    after = [seg for seg in segments if _seconds(seg.get("start")) >= at - 0.05]
    return str(after[0].get("text") or "").strip()[:CONTEXT_CHARS] if after else ""


def _text_within(segments: list[dict[str, Any]], start: float, end: float, limit: int = 80) -> str:
    words = [
        str(seg.get("text") or "").strip() for seg in segments
        if _seconds(seg.get("end")) > start and _seconds(seg.get("start")) < end
    ]
    return " ".join(w for w in words if w)[:limit]


def facts(record: dict[str, Any], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Everything the cut did, one fact per thing, with the program's times."""
    out: list[dict[str, Any]] = []
    gaps = record.get("gaps") or {}
    short = gaps.get("short") or {}
    if short.get("count"):
        out.append({
            "id": "short", "kind": "short_gaps", "action": "removed", "seconds": short.get("seconds") or 0,
            "default": f"{short['count']} 处 3 秒以下的停顿全部剪掉，共 {_length(short.get('seconds') or 0)}。",
            "count": short["count"],
        })
    for n, gap in enumerate(gaps.get("long") or [], start=1):
        start, end = _seconds(gap.get("start")), _seconds(gap.get("end"))
        picture = gap.get("picture") or ""
        if gap.get("kept"):
            default = gap.get("reason") or "这段没人说话，但画面需要看，保留。"
        else:
            default = f"{_length(end - start)}没人说话，剪掉。" + (f"（{picture}）" if picture else "")
        out.append({
            "id": f"g{n}", "kind": "long_gap", "action": "kept" if gap.get("kept") else "removed",
            "start": start, "end": end, "seconds": round(end - start, 1), "picture": picture,
            "before": _line_before(segments, start), "after": _line_after(segments, end), "default": default,
        })
    for n, removal in enumerate(record.get("removed") or [], start=1):
        start, end = _seconds(removal.get("start")), _seconds(removal.get("end"))
        out.append({
            "id": f"r{n}", "kind": "removed_talk", "action": "removed", "start": start, "end": end,
            "seconds": round(end - start, 1), "said": _text_within(segments, start, end),
            "default": removal.get("reason") or "这段说的话和正式内容无关，去掉。",
        })
    for n, refused in enumerate(record.get("refused_removals") or [], start=1):
        start, end = _seconds(refused.get("start")), _seconds(refused.get("end"))
        out.append({
            "id": f"x{n}", "kind": "refused_removal", "action": "kept", "start": start, "end": end,
            "seconds": round(end - start, 1), "said": _text_within(segments, start, end),
            "default": "模型想去掉这段，但要去掉的说话太多，像是误判，没有去掉。",
        })
    for n, change in enumerate(
        (c for c in record.get("user_changes") or [] if c.get("kind") == "removed_talk" and c.get("user_said") == "kept"), start=1,
    ):
        start, end = _seconds(change.get("start")), _seconds(change.get("end"))
        out.append({
            "id": f"u{n}", "kind": "user_change", "action": "kept", "start": start, "end": end,
            "seconds": round(end - start, 1), "said": _text_within(segments, start, end),
            "default": "你说这段不该剪，已放回。",
        })
    spoken = sorted(record.get("spoken") or [], key=lambda s: _seconds(s.get("end")) - _seconds(s.get("start")), reverse=True)
    for n, span in enumerate(spoken[:MAX_SPOKEN_ITEMS], start=1):
        start, end = _seconds(span.get("start")), _seconds(span.get("end"))
        said = span.get("said") or _text_within(segments, start, end, 30)
        out.append({
            "id": f"s{n}", "kind": "quiet_speech", "action": "kept", "start": start, "end": end,
            "seconds": round(end - start, 1), "said": said,
            "default": "这里说得很轻，按音量会被剪掉；转录认出了字，人声检测器也听到了，保留。",
        })
    if len(spoken) > MAX_SPOKEN_ITEMS:
        rest = spoken[MAX_SPOKEN_ITEMS:]
        seconds = sum(_seconds(s.get("end")) - _seconds(s.get("start")) for s in rest)
        out.append({
            "id": "s_rest", "kind": "quiet_speech", "action": "kept", "seconds": round(seconds, 1),
            "count": len(rest), "default": f"另有 {len(rest)} 处很轻的说话同样保留，共 {_length(seconds)}。",
        })
    return out


def _fact_line(fact: dict[str, Any]) -> str:
    action = "剪掉" if fact["action"] == "removed" else "保留"
    parts = [f"[{fact['id']}] {action}"]
    if "start" in fact:
        parts.append(f"原片 {_clock(fact['start'])}–{_clock(fact['end'])}，{_length(fact['seconds'])}")
    kind = {
        "short_gaps": "短停顿汇总", "long_gap": "长空白", "removed_talk": "有人说话的段落",
        "refused_removal": "没执行的去除", "quiet_speech": "说得很轻的话", "user_change": "用户改过的",
    }[fact["kind"]]
    parts.append(kind)
    if fact.get("picture"):
        parts.append(fact["picture"])
    if fact.get("before"):
        parts.append(f"前一句「{fact['before']}」")
    if fact.get("after"):
        parts.append(f"后一句「{fact['after']}」")
    if fact.get("said"):
        parts.append(f"这段说的「{fact['said']}」")
    parts.append(f"理由：{fact['default']}")
    return " · ".join(parts)


def _item(fact: dict[str, Any], text: str | None = None) -> dict[str, Any]:
    item = {"ref": fact["id"], "kind": fact["kind"], "action": fact["action"], "text": (text or fact["default"]).strip()}
    for key in ("start", "end", "seconds", "count", "said"):
        if key in fact:
            item[key] = fact[key]
    return item


def assemble(all_facts: list[dict[str, Any]], answer: dict[str, Any], organized_by: str) -> dict[str, Any]:
    """The model's organisation over the program's facts. Unknown ids are
    dropped, each fact appears once, and every fact left out is folded into a
    last group."""
    by_id = {fact["id"]: fact for fact in all_facts}
    used: set[str] = set()
    sections: list[dict[str, Any]] = []
    for section in answer.get("sections") or []:
        if not isinstance(section, dict):
            continue
        items = []
        for entry in section.get("items") or []:
            if not isinstance(entry, dict):
                continue
            ref = str(entry.get("ref") or "")
            if ref not in by_id or ref in used:
                continue
            used.add(ref)
            items.append(_item(by_id[ref], str(entry.get("text") or "")[:120] or None))
        if items:
            sections.append({
                "title": str(section.get("title") or "").strip()[:30] or "处理",
                "folded": bool(section.get("folded")), "items": items,
            })
    rest = [fact for fact in all_facts if fact["id"] not in used]
    if rest:
        sections.append({"title": OTHER_TITLE, "folded": True, "items": [_item(fact) for fact in rest]})
    return {"summary": str(answer.get("summary") or "").strip()[:200], "sections": sections, "organized_by": organized_by}


def fallback(all_facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Grouped by kind, when no model organised them."""
    groups = [
        ("你改过的", {"user_change"}, False),
        ("去掉的说话", {"removed_talk"}, False),
        ("因为画面保留的空白", {"long_gap:kept"}, False),
        ("没执行的去除", {"refused_removal"}, False),
        ("剪掉的长空白", {"long_gap:removed"}, True),
        ("保住的轻声说话", {"quiet_speech"}, True),
        ("短停顿", {"short_gaps"}, True),
    ]

    def key(fact: dict[str, Any]) -> set[str]:
        return {fact["kind"], f"{fact['kind']}:{fact['action']}"}

    sections = []
    for title, kinds, folded in groups:
        items = [_item(f) for f in all_facts if key(f) & kinds]
        if items:
            sections.append({"title": title, "folded": folded, "items": items})
    return {"summary": "", "sections": sections, "organized_by": None}


def organize(all_facts: list[dict[str, Any]], chat: tuple[Chat, str] | None) -> dict[str, Any]:
    """The report for these facts: organised by ``chat`` when there is one and
    it answers usefully, otherwise by kind."""
    if not all_facts:
        return {"summary": "", "sections": [], "organized_by": None}
    if chat is None:
        return fallback(all_facts)
    from backend.core.note_coverage import _within_deadline

    try:
        text = _within_deadline(chat[0], SYSTEM, "\n".join(_fact_line(f) for f in all_facts), REQUEST_DEADLINE_SECONDS)
        match = re.search(r"\{.*\}", text or "", re.S)
        answer = json.loads(match.group(0)) if match else {}
        report = assemble(all_facts, answer if isinstance(answer, dict) else {}, chat[1])
    except Exception as exc:  # noqa: BLE001 - the facts are still shown
        logger.warning("cut report not organised: %s", exc)
        return fallback(all_facts)
    if not any(section["title"] != OTHER_TITLE for section in report["sections"]):
        return fallback(all_facts)
    return report


def build(record: dict[str, Any], segments: list[dict[str, Any]], chat: tuple[Chat, str] | None) -> dict[str, Any]:
    return organize(facts(record, segments), chat)


__all__ = ["build", "facts", "organize", "assemble", "fallback"]
