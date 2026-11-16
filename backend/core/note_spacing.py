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
_FENCE = re.compile(r"^\s*(```|~~~)")
_TABLE = re.compile(r"^\s*\|")


def _blocks(text: str) -> list[tuple[bool, str]]:
    """Each line with whether it sits inside a fenced code block (fence
    lines count as inside)."""
    out: list[tuple[bool, str]] = []
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            out.append((True, line))
            fenced = not fenced
            continue
        out.append((fenced, line))
    return out


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
        keep = True
        if after_code:
            keep = True
        elif _HEADING.match(after) or _TABLE.match(after):
            keep = True  # a table needs a blank line above it to show as one
        elif not before_code and _HEADING.match(before):
            keep = False
        elif not before_code and _COLON_END.search(before) and not _QUOTE.match(before):
            keep = False
        elif _QUOTE.match(after) and not before_code and _LIST_ITEM.match(before):
            keep = False
        elif not before_code and _QUOTE.match(before) and _LIST_ITEM.match(after):
            keep = False
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
    """``text`` with a blank line before a quote or a list that follows other
    text, and after a quote that other text follows, so a renderer keeps
    each as its own block. Code blocks are left as they are."""
    out: list[str] = []
    previous_code = False
    for code, line in _blocks(text):
        previous = out[-1] if out else ""
        if not code and line.strip() and previous.strip() and not previous_code:
            quote, was_quote = bool(_QUOTE.match(line)), bool(_QUOTE.match(previous))
            item, was_item = bool(_LIST_ITEM.match(line)), bool(_LIST_ITEM.match(previous))
            table, was_table = bool(_TABLE.match(line)), bool(_TABLE.match(previous))
            if ((quote and not was_quote) or (was_quote and not quote) or (table and not was_table)
                    or (item and not was_item and not previous.startswith(" "))):
                out.append("")
        out.append(line)
        previous_code = code
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


__all__ = ["for_renderers", "tidy"]
