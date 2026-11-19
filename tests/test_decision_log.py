"""The decision log stays where agents look for it (docs/02-决策记录.md is the index).

Requirements (owner, 2026-11-19, docs/评测/2026-11-19-agent改代码时读代码太多.md):

- One file per product part under ``docs/决策记录/``, named ``<n>-<part>.md``.
  Every entry heading in it is numbered ``n.x``: on 2026-10-28 an edit to the
  old single file overwrote two part headings, and the next 26 entries of part
  6 were filed under 安装 without anyone noticing.
- No number is used twice, in a part or its ``已取代/`` archive; 6.77 once was.
- Each part opens with a 现状, so an agent can read what holds now without
  reading every entry, and the 现状 is dated no earlier than the part's newest
  entry: a new entry makes its writer look at the 现状, bring it up to date if
  the step now works differently, and move its date. Before the split the
  现状 had not moved since mid-October while the entries below changed it.
- Every entry's first line starts with its date (YYYY-MM-DD).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "docs" / "决策记录"
ENTRY = re.compile(r"^### (\d+)\.(\d+)\b", re.M)
STATE_DATE = re.compile(r"^\*\*现状\*\*（(\d{4}-\d\d-\d\d)", re.M)
DATE = re.compile(r"^\d{4}-\d\d-\d\d")


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


def test_current_state_is_not_older_than_the_newest_entry() -> None:
    for part in _parts():
        text = part.read_text(encoding="utf-8")
        state = STATE_DATE.search(text)
        assert state, f"{part.name}: the 现状 line has no date"
        lines = text.split("\n")
        newest = ""
        for i, line in enumerate(lines):
            if not line.startswith("### "):
                continue
            first = next((x for x in lines[i + 1 :] if x.strip()), "")
            match = DATE.match(first)
            assert match, f"{part.name}: {line} has no date on its first line"
            newest = max(newest, match.group(0))
        assert state.group(1) >= newest, (
            f"{part.name}: an entry from {newest} is newer than the 现状 "
            f"({state.group(1)}). Read the 现状, change what the new entry "
            "changed, and set its date to today."
        )
