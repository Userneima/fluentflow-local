"""The decision log stays where agents look for it (docs/02-决策记录.md is the index).

Requirements (owner, 2026-11-19, docs/评测/2026-11-19-agent改代码时读代码太多.md):

- One file per product part under ``docs/决策记录/``, named ``<n>-<part>.md``.
  Every entry heading in it is numbered ``n.x``: on 2026-10-28 an edit to the
  old single file overwrote two part headings, and the next 26 entries of part
  6 were filed under 安装 without anyone noticing.
- No number is used twice, in a part or its ``已取代/`` archive; 6.77 once was.
- Each part opens with a 现状, so an agent can read what holds now without
  reading every entry.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "docs" / "决策记录"
ENTRY = re.compile(r"^### (\d+)\.(\d+)\b", re.M)


def _parts() -> list[Path]:
    return sorted(LOG.glob("[0-9]-*.md"))


def test_eight_parts_each_with_a_current_state() -> None:
    parts = _parts()
    assert [p.name[0] for p in parts] == [str(n) for n in range(1, 9)]
    for part in parts:
        head = part.read_text(encoding="utf-8").split("\n### ", 1)[0]
        assert "**现状**" in head, f"{part.name} has no 现状 before its first entry"


def test_entries_sit_in_their_part_and_numbers_are_unique() -> None:
    for part in _parts():
        n = part.name[0]
        files = [part, LOG / "已取代" / part.name]
        seen: dict[str, str] = {}
        for f in files:
            if not f.exists():
                continue
            for major, minor in ENTRY.findall(f.read_text(encoding="utf-8")):
                number = f"{major}.{minor}"
                assert major == n, f"entry {number} is filed in {f.relative_to(ROOT)}"
                assert number not in seen, f"{number} appears twice ({seen[number]}, {f.name})"
                seen[number] = str(f.relative_to(LOG))
