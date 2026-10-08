"""Every FLUENTFLOW_* environment variable the code reads is in the table.

Requirement: someone tuning this install reads docs/maintenance.md and finds
every setting backend/ and scripts/ honour; a variable read in code but missing
from the table is a setting nobody can discover. Code-level constants that only
share the prefix (e.g. the FLUENTFLOW_SYSTEM_PROMPT prompt text) are not
settings and are not required.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "docs" / "maintenance.md"


def _read_in_code() -> set[str]:
    literals: set[str] = set()
    constants: set[str] = set()
    for folder in ("backend", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            literals |= set(re.findall(r"[\"'](FLUENTFLOW_[A-Z0-9_]+)[\"']", text))
            constants |= set(re.findall(r"^\s*(FLUENTFLOW_[A-Z0-9_]+)\s*[:=]", text, re.M))
    return literals - constants


def _in_table() -> set[str]:
    names: set[str] = set()
    for line in TABLE.read_text(encoding="utf-8").splitlines():
        if line.startswith("| `FLUENTFLOW_"):
            # Whole row: an old alias is named in the meaning cell of its new name.
            names |= set(re.findall(r"`(FLUENTFLOW_[A-Z0-9_]+)`", line))
    return names


def test_every_variable_read_in_code_is_in_the_table():
    read = _read_in_code()
    assert "FLUENTFLOW_NOTE_DEADLINE_SECONDS" in read, "the scan no longer finds the reads"
    missing = sorted(read - _in_table())
    assert missing == [], f"read in backend/ or scripts/ but missing from docs/maintenance.md: {missing}"


def test_the_table_lists_nothing_the_code_does_not_read():
    stale = sorted(_in_table() - _read_in_code())
    assert stale == [], f"listed in docs/maintenance.md but read nowhere: {stale}"
