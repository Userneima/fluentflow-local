"""Where the note leaves the order of the recording, a line pointing to it.

The note is reorganised by topic, so what the video says at some moment can
sit in a chapter far from the one a reader following the video is on; the
reader takes it as missing (王愉超, 2026-10-19). Where that happens, the
section being read gets a callout naming the chapter, with a heading link the
editor and Obsidian both follow:

    > [!tip] 视频 17:29 起讲的「Agent 怎么"自学习"」在[[#五、…|第五章]]。

Found by the program from the section times table (``note_section_times``),
never by the model writing the note: it does not check afterwards what it
moved, and writing in parts it does not know the final chapter numbers.

How a move is found (``plan``). The reader goes down the note while the
video plays; a pointer they follow brings them back to where they were. Each
stretch of the recording belongs to one chapter (consecutive spans of the
same level-2 chapter are one stretch). For each stretch in time order, its
first section is compared with the section the reader is on:

- further down, with ``FAR_SECTIONS`` or more sections between that the video
  has not reached yet: the reader would read past them to get there;
- further up, with ``FAR_SECTIONS`` or more sections between: the reader has
  passed it (content moved earlier, or the video coming back to a topic).

Anything nearer is found by reading on. A stretch from a section's ``others``
shorter than ``MIN_OTHER_SECONDS`` is a passing mention (the opening teaser
of an interview) and gets none. A level-2 section whose span mostly lies over
its own sub-sections' is their sum, not a place in the order, and is left out
in favour of them. Calibrated on the 罗福莉 interview (2026-11-03): this finds
the two places 王愉超 marked by hand and no other.

The pointers this module wrote are recorded under ``RESULT_KEY``: one the
user deletes is not written again. A pointer whose target heading is already
linked from that section (a hand-written one) is not written either. The
lines themselves (``note_section_times.POINTER_LINE``) are left out of a
section's body hash, so inserting or deleting one never sends
the section to be aligned again.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from backend.core import job_store, note_section_times as times, note_versions

logger = logging.getLogger(__name__)

RESULT_KEY = "note_order_pointers"
FAR_SECTIONS = 2
MIN_OTHER_SECONDS = 60.0
# A level-2 span lying this much over its sub-sections' spans is their sum.
SUM_SHARE = 0.5
MAX_TARGETS = 3
# The closing sections the note skill writes (回顾 for a talk; 决定, 待办,
# 还没定的 for a meeting; 出处 for both) gather from the whole recording by
# design and are not places in its order.
CLOSING_HEADINGS = {"回顾", "决定", "待办", "还没定的", "出处"}

CHAPTER_NUMBER = re.compile(r"^([一二三四五六七八九十百零〇两]+)、\s*")
SUB_NUMBER = re.compile(r"^[（(]([一二三四五六七八九十百零〇两]+|\d+)[）)]\s*")
# Characters a heading link cannot carry in Obsidian.
LINK_UNSAFE = re.compile(r"[\[\]|#^]")


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _plain(heading: str) -> str:
    return CHAPTER_NUMBER.sub("", re.sub(r"[*_`]", "", heading)).strip()


def _units(sections: list[dict[str, Any]], rows: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """The sections that are places in the order, each with its chapter (the
    index of its level-2 section) and spans."""
    chapter = -1
    children: dict[int, list[int]] = {}
    owner: dict[int, int] = {}
    for section in sections:
        if section["level"] == 2:
            chapter = section["index"]
        else:
            children.setdefault(chapter, []).append(section["index"])
        owner[section["index"]] = chapter if chapter >= 0 else section["index"]
    units = []
    for section in sections:
        row = rows.get(section["index"])
        if row is None or section["level"] == 2 and _plain(section["heading"]) in CLOSING_HEADINGS:
            continue
        start, end = (float(x) for x in row["primary"])
        kids = [rows[i] for i in children.get(section["index"], []) if i in rows] if section["level"] == 2 else []
        if kids and end > start:
            covered = sum(
                max(0.0, min(end, float(k["primary"][1])) - max(start, float(k["primary"][0]))) for k in kids
            )
            if covered > (end - start) * SUM_SHARE:
                continue
        units.append({
            "index": section["index"],
            "chapter": owner[section["index"]],
            "primary": (start, end),
            "others": [tuple(float(x) for x in span) for span in row.get("others") or []],
        })
    return units


def _stretches(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spans = []
    for unit in units:
        spans.append((unit["primary"][0], unit["primary"][1], unit, True))
        for start, end in unit["others"]:
            spans.append((start, end, unit, False))
    spans.sort(key=lambda span: (span[0], span[1]))
    stretches: list[dict[str, Any]] = []
    for start, end, unit, primary in spans:
        last = stretches[-1] if stretches else None
        if last and last["chapter"] == unit["chapter"]:
            last["end"] = max(last["end"], end)
            last["seconds_other"] += 0 if primary else end - start
            last["primary"] = last["primary"] or primary
            if unit not in last["units"]:
                last["units"].append(unit)
            continue
        stretches.append({
            "chapter": unit["chapter"], "start": start, "end": end, "units": [unit],
            "primary": primary, "seconds_other": 0 if primary else end - start,
        })
    # Passing mentions out, then neighbours of one chapter joined again.
    kept = [s for s in stretches if s["primary"] or s["seconds_other"] >= MIN_OTHER_SECONDS]
    joined: list[dict[str, Any]] = []
    for stretch in kept:
        if joined and joined[-1]["chapter"] == stretch["chapter"]:
            joined[-1]["end"] = max(joined[-1]["end"], stretch["end"])
            joined[-1]["units"] += [u for u in stretch["units"] if u not in joined[-1]["units"]]
            continue
        joined.append(dict(stretch, units=list(stretch["units"])))
    return joined


def plan(markdown: str, table: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The pointers the note needs: ``{"at": section index, "targets":
    [section indexes], "start": seconds}``, in note order of ``at``."""
    sections = times.note_sections(markdown)
    rows = times.match_rows(list((table or {}).get("sections") or []), sections)
    units = _units(sections, rows)
    position = {unit["index"]: number for number, unit in enumerate(units)}
    cursor: int | None = None
    pointers = []
    for stretch in _stretches(units):
        # Read into from its top: the stretch's section nearest the top.
        at = min(position[u["index"]] for u in stretch["units"])
        if cursor is None:
            cursor = max(position[u["index"]] for u in stretch["units"])
            continue
        if at > cursor:
            between = [u for u in units[cursor + 1:at] if u["primary"][0] > stretch["start"]]
            far = len(between) >= FAR_SECTIONS
        else:
            far = cursor - at - 1 >= FAR_SECTIONS
        if far:
            pointers.append({
                "at": units[cursor]["index"],
                "targets": [u["index"] for u in stretch["units"][:MAX_TARGETS]],
                "start": stretch["start"],
            })
        elif at > cursor:
            cursor = max(position[u["index"]] for u in stretch["units"])
    return pointers


def _chapter(sections: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    return next((s for s in reversed(sections[:index + 1]) if s["level"] == 2), None)


def _label(sections: list[dict[str, Any]], index: int) -> tuple[str, str, str]:
    """How a section is named in a pointer: its chapter (``第五章``), its
    place in the chapter (``（四）``, empty for a chapter) and its name."""
    section = sections[index]
    chapter = _chapter(sections, index)
    number = ""
    if chapter is not None:
        match = CHAPTER_NUMBER.match(chapter["heading"])
        ordinal = sum(1 for s in sections[:chapter["index"] + 1] if s["level"] == 2)
        number = f"第{match.group(1)}章" if match else f"第 {ordinal} 章"
    if section["level"] == 2:
        return number, "", CHAPTER_NUMBER.sub("", section["heading"])
    match = SUB_NUMBER.match(section["heading"])
    name = SUB_NUMBER.sub("", section["heading"])
    return number, f"（{match.group(1)}）" if match else f"「{name}」一节", name


def _link(sections: list[dict[str, Any]], index: int, label: str) -> str:
    """An Obsidian heading link. A sub-section heading that repeats in the
    note (（一）主线 in two chapters) goes through its chapter,
    ``[[#三、…#（一）主线|…]]``; otherwise the heading alone, which is what
    Feishu and the PDF resolve best."""
    section = sections[index]
    chapter = _chapter(sections, index)
    repeats = sum(1 for s in sections if s["heading"] == section["heading"]) > 1
    path = [chapter["heading"], section["heading"]] if repeats and chapter is not None and chapter is not section else [section["heading"]]
    if any(LINK_UNSAFE.search(part) for part in path):
        return label
    return f"[[#{'#'.join(path)}|{label}]]"


def pointer_line(sections: list[dict[str, Any]], pointer: dict[str, Any]) -> str:
    names, links = [], []
    last_chapter = None
    for target in pointer["targets"]:
        chapter, place, name = _label(sections, target)
        names.append(f"「{name}」")
        label = place if place and chapter == last_chapter else (chapter + place or "下文")
        links.append(_link(sections, target, label))
        last_chapter = chapter
    return f"> [!tip] 视频 {_clock(pointer['start'])} 起讲的{''.join(names)}在{'、'.join(links)}。"


def _key(sections: list[dict[str, Any]], pointer: dict[str, Any]) -> list[str]:
    return [sections[pointer["at"]]["heading"], *(sections[t]["heading"] for t in pointer["targets"])]


def _already_points(sections: list[dict[str, Any]], body: str, pointer: dict[str, Any]) -> bool:
    """The section links to a target already, or to a target's chapter (a
    pointer written by hand)."""
    for target in pointer["targets"]:
        chapter = _chapter(sections, target)
        for heading in {sections[target]["heading"], *([chapter["heading"]] if chapter else [])}:
            if f"#{heading}|" in body or f"#{heading}]]" in body:
                return True
    return False


def _body_ends(markdown: str) -> tuple[list[int], list[int]]:
    """Per section in ``note_sections`` order, the line its body ends before,
    and the line of its heading."""
    lines = markdown.splitlines()
    heads = []
    in_fence = False
    for number, line in enumerate(lines):
        if times.FENCE.match(line):
            in_fence = not in_fence
            continue
        match = None if in_fence else times.HEADING.match(line)
        if match and match.group(2):
            heads.append(number)
    return [*heads[1:], len(lines)], heads


def with_pointers(markdown: str, table: dict[str, Any] | None, written: list[list[str]] | None = None) -> tuple[str, list[list[str]]]:
    """The note with the pointers it needs and has not had, and the keys of
    the ones added. ``written``: keys added before (not added again)."""
    sections = times.note_sections(markdown)
    ends, heads = _body_ends(markdown)
    lines = markdown.splitlines()
    if len(heads) != len(sections):
        return markdown, []
    done = {tuple(key) for key in written or []}
    inserts: dict[int, list[str]] = {}
    added = []
    for pointer in plan(markdown, table):
        key = _key(sections, pointer)
        body = "\n".join(lines[heads[pointer["at"]] + 1:ends[pointer["at"]]])
        if tuple(key) in done or _already_points(sections, body, pointer):
            continue
        inserts.setdefault(pointer["at"], []).append(pointer_line(sections, pointer))
        added.append(key)
        done.add(tuple(key))
    # From the bottom up, so the line numbers above stay right. Each goes
    # after the section's last text, before the blank lines and part marker
    # that close it.
    for at in sorted(inserts, reverse=True):
        position = ends[at]
        while position > heads[at] + 1 and (
            not lines[position - 1].strip() or times.PART_MARKER.fullmatch(lines[position - 1].strip())
        ):
            position -= 1
        block = [x for line in inserts[at] for x in ("", line)]
        if position < len(lines) and lines[position].strip():
            block.append("")
        lines[position:position] = block
    if not added:
        return markdown, []
    return "\n".join(lines) + ("\n" if markdown.endswith("\n") else ""), added


def add_to_task(task_id: str) -> dict[str, Any]:
    """Add the pointers the task's note needs, once its section times cover
    it. Returns ``{"added": n}``."""
    job = job_store.get_job(task_id)
    result = dict((job or {}).get("result") or {})
    markdown = str(result.get("summary_markdown") or "")
    if not job or not markdown.strip() or not times.covers_note(result):
        return {"added": 0}
    record = result.get(RESULT_KEY) if isinstance(result.get(RESULT_KEY), dict) else {}
    note, added = with_pointers(markdown, times.stored_table(result), list(record.get("written") or []))
    if not added:
        return {"added": 0}
    from backend.core.result_artifacts import _attach_result_artifacts

    after = dict(result)
    after["summary_markdown"] = note
    after[RESULT_KEY] = {
        "written": [*(record.get("written") or []), *added],
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    note_versions.declare(after, note_versions.POINTERS)
    after = _attach_result_artifacts(task_id, after, parts=("summary",))
    # Only over the note as read here: an edit saved meanwhile stays.
    saved = job_store.save_result_changes(task_id, result, after, touch_updated_at=False, unless_moved=("summary_markdown",))
    if saved is None:
        return {"added": 0}
    logger.info("note order pointers added to %s: %d", task_id, len(added))
    return {"added": len(added)}


__all__ = ["RESULT_KEY", "add_to_task", "plan", "pointer_line", "with_pointers"]
