"""Blank lines in a review note: how the owner reads it in Obsidian, and how
the machines that print it or put it in Feishu need it.

Requirement (owner, 2026-11-16), for the review note md:
1. A heading has one blank line before it and none after it.
2. A line ending with a colon (「**为什么**：」「（转录稿补充）：」) runs straight
   into what follows it: no blank line between. A table keeps its blank line
   above: without it Obsidian shows the table as plain text.
3. A quote (a 核对 or another ``>`` block) next to a list item has no blank
   line on that side: a check inserted between items stays part of the list.
4. Elsewhere, at most one blank line in a row. Inside code blocks nothing
   changes.

``tidy`` makes the md that way. ``for_renderers`` puts back the blank lines a
Markdown renderer needs to see a list or a quote as its own block (the PDF's
Python-Markdown runs a quote right under a list item into the item, and
Feishu's import does the same), without touching the md on disk.
"""

from __future__ import annotations

import re

_HEADING = re.compile(r"^#{1,6}\s")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
_QUOTE = re.compile(r"^\s*>")
_COLON_END = re.compile(r"[：:]\s*$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_TABLE = re.compile(r"^\s*\|")


def is_fence(line: str) -> bool:
    return bool(_FENCE.match(line))


def in_code(lines: list[str]) -> list[bool]:
    """For each line, whether it sits inside a fenced code block (fence lines
    count as inside). Only a bare fence of the same kind, at least as long as
    the one that opened the block, closes it. The md, the PDF and Feishu all
    read code blocks through this."""
    out: list[bool] = []
    opener = ""  # the fence that opened the block we are in
    for line in lines:
        fence = _FENCE.match(line)
        if fence and not opener:
            opener = fence.group(1)
            out.append(True)
            continue
        if fence and opener and fence.group(1)[0] == opener[0] and len(fence.group(1)) >= len(opener) \
                and not line.strip()[len(fence.group(1)):].strip():
            opener = ""
            out.append(True)
            continue
        out.append(bool(opener))
    return out


def _blocks(text: str) -> list[tuple[bool, str]]:
    lines = text.splitlines()
    return list(zip(in_code(lines), lines))


def kind(line: str) -> str:
    """What a line of the note is, for where blank lines go."""
    if _HEADING.match(line):
        return "heading"
    if _TABLE.match(line):
        return "table"
    if _QUOTE.match(line):
        return "quote"
    if _LIST_ITEM.match(line):
        return "list"
    return "text"


def joins(before: str, after: str, *, before_colon: bool = False) -> bool:
    """Whether two neighbouring blocks of a review note, outside code, sit
    with no blank line between them (decision 6.75): none after a heading or
    a line ending with a colon (unless the colon line is a quote), none
    between a list and a quote next to it; a heading or a table always gets
    its blank line above. ``before`` and ``after`` are ``kind`` values. The md
    (``tidy``) and the Feishu layout (``feishu_note._layout``) both ask this."""
    if after in ("heading", "table"):
        return False  # a table needs a blank line above it to show as one
    if before == "heading":
        return True
    if before_colon and before != "quote":
        return True
    return {before, after} == {"list", "quote"}


def tidy(text: str) -> str:
    """The review note with blank lines where the owner wants them."""
    lines = _blocks(text)
    out: list[tuple[bool, str]] = []
    i = 0
    while i < len(lines):
        code, line = lines[i]
        if code or line.strip():
            out.append((code, line))
            i += 1
            continue
        # A run of blank lines outside code: keep one, or none.
        j = i
        while j < len(lines) and not lines[j][0] and not lines[j][1].strip():
            j += 1
        if not out or j >= len(lines):
            i = j  # none at the start or the end of the note
            continue
        before_code, before = out[-1]
        after_code, after = lines[j]
        keep = after_code or before_code or not joins(
            kind(before), kind(after), before_colon=bool(_COLON_END.search(before)))
        if keep:
            out.append((False, ""))
        i = j
    # A heading right under a line of text gets its blank line.
    final: list[str] = []
    for index, (code, line) in enumerate(out):
        if not code and _HEADING.match(line) and final and final[-1].strip():
            final.append("")
        final.append(line)
    return "\n".join(final).rstrip("\n") + "\n"


def for_renderers(text: str) -> str:
    """``text`` with a blank line before a quote, a table or a list that
    follows other text, after a quote that other text follows, and around
    each heading, so a renderer (and Feishu's layout, which counts blocks by
    blank lines) keeps each as its own block. Code blocks are left as they
    are."""
    out: list[str] = []
    previous_code = False
    for code, line in _blocks(text):
        previous = out[-1] if out else ""
        if not code and line.strip() and previous.strip() and not previous_code:
            quote, was_quote = bool(_QUOTE.match(line)), bool(_QUOTE.match(previous))
            item, was_item = bool(_LIST_ITEM.match(line)), bool(_LIST_ITEM.match(previous))
            table, was_table = bool(_TABLE.match(line)), bool(_TABLE.match(previous))
            if ((quote and not was_quote) or (was_quote and not quote) or (table and not was_table)
                    or _HEADING.match(previous) or _HEADING.match(line)
                    or (item and not was_item and not previous.startswith(" "))):
                out.append("")
        out.append(line)
        previous_code = code
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


__all__ = ["for_renderers", "in_code", "is_fence", "joins", "kind", "tidy"]
