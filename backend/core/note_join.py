"""Join the parts of a long recording's note into one note, without a model.

Each part is written on its own and only knows its own stretch, so each one
numbers its sections from 「一、」 and ends with its own 「存疑」 section. Read in
a row that is four notes stapled together. The fixes are all mechanical, which is
why they live here and not in another request:

- one H1 title for the whole note: the one the first part wrote if it wrote one,
  otherwise the task's title; titles written by later parts are dropped;
- numbered second level headings are renumbered in order across all parts;
- every 「存疑」 section is lifted out of its part and the items are put in one
  section at the end of the note.

Headings inside fenced code blocks are left alone.
"""

from __future__ import annotations

import re

DOUBT_TITLE = "存疑"

_DIGITS = "零一二三四五六七八九"
_NUMBERED_H2 = re.compile(r"^##\s+[一二三四五六七八九十百零〇两]+[、．.]\s*(.*)$")
_H2 = re.compile(r"^##\s+(.*?)\s*#*\s*$")
_H1 = re.compile(r"^#\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def chinese_number(value: int) -> str:
    """1 → 一, 10 → 十, 33 → 三十三, 105 → 一百零五."""
    if value <= 0:
        raise ValueError("section numbers start at 1")
    if value < 10:
        return _DIGITS[value]
    if value < 20:
        return "十" + (_DIGITS[value % 10] if value % 10 else "")
    if value < 100:
        tens, ones = divmod(value, 10)
        return _DIGITS[tens] + "十" + (_DIGITS[ones] if ones else "")
    hundreds, rest = divmod(value, 100)
    if rest == 0:
        return _DIGITS[hundreds] + "百"
    if rest < 10:
        return _DIGITS[hundreds] + "百零" + _DIGITS[rest]
    tail = chinese_number(rest)
    # 110 is 一百一十, not 一百十.
    return _DIGITS[hundreds] + "百" + ("一" + tail if tail.startswith("十") else tail)


def _is_doubt(heading_text: str) -> bool:
    numbered = _NUMBERED_H2.match("## " + heading_text)
    title = numbered.group(1) if numbered else heading_text
    return title.strip().startswith(DOUBT_TITLE)


def _split_part(body: str) -> tuple[str, list[str], list[str], bool]:
    """A part's own H1 (if any), its lines without H1s or doubt sections, and the
    doubt sections' contents. The flag says whether a doubt heading was numbered."""
    title = ""
    kept: list[str] = []
    doubts: list[str] = []
    doubt_numbered = False
    in_fence = False
    in_doubt = False
    seen_content = False
    current: list[str] = []
    for line in body.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else (_H1.match(line) or _H2.match(line))
        if heading is not None:
            if in_doubt:
                doubts.append("\n".join(current).strip())
                current = []
                in_doubt = False
            if line.startswith("# "):
                if not seen_content and not title:
                    title = heading.group(1)
                continue
            if _is_doubt(heading.group(1)):
                in_doubt = True
                doubt_numbered = doubt_numbered or bool(_NUMBERED_H2.match(line))
                continue
        if in_doubt:
            current.append(line)
            continue
        if line.strip():
            seen_content = True
        kept.append(line)
    if in_doubt:
        doubts.append("\n".join(current).strip())
    return title, kept, [doubt for doubt in doubts if doubt], doubt_numbered


def join_parts(bodies: list[tuple[str, str]], *, title: str = "") -> str:
    """One note from ``(marker, markdown)`` pairs in recording order.

    ``marker`` is put above its part as it is (an HTML comment saying which
    stretch follows); ``title`` is used when the first part wrote no H1.
    """
    number = 0
    first_title = ""
    sections: list[str] = []
    doubts: list[str] = []
    doubt_numbered = False
    for position, (marker, body) in enumerate(bodies):
        part_title, lines, part_doubts, numbered = _split_part(body)
        if position == 0:
            first_title = part_title
        doubts.extend(part_doubts)
        doubt_numbered = doubt_numbered or numbered
        renumbered = []
        in_fence = False
        for line in lines:
            if _FENCE.match(line):
                in_fence = not in_fence
            found = None if in_fence else _NUMBERED_H2.match(line)
            if found:
                number += 1
                line = f"## {chinese_number(number)}、{found.group(1)}"
            renumbered.append(line)
        text = "\n".join(renumbered).strip()
        if text:
            sections.append(f"{marker}\n\n{text}" if marker else text)
    if doubts:
        heading = f"## {chinese_number(number + 1)}、{DOUBT_TITLE}" if doubt_numbered else f"## {DOUBT_TITLE}"
        sections.append(heading + "\n\n" + "\n\n".join(doubts))
    heading_title = (first_title or title or "").strip()
    if heading_title:
        sections.insert(0, f"# {heading_title}")
    return "\n\n".join(sections)
