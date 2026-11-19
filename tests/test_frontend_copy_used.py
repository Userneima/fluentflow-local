"""Every entry in the page's copy table is used by the page.

Requirements (owner, 2026-11-19, docs/方案/2026-11-19-全项目优化和防再犯机制.md):

- Each key in the copy table in ``frontend/src/app/shared.jsx`` is named, as a
  whole quoted string, somewhere in the product code. When a button goes, its
  copy goes with it: 24 entries outlived their buttons, and 「重新转录」 among
  them let a hint pointing at the removed button look valid
  (tests/test_hint_buttons_exist.py reads the page's text).
- Keys built at run time from a prefix (``t(`status.${x}`)``) are exempt by
  that prefix.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
SHARED = SRC / "app" / "shared.jsx"
ENTRY = re.compile(r"'([a-zA-Z]+(?:\.[a-zA-Z0-9_]+)+)':'(?:[^'\\]|\\.)*',?")
BUILT_AT_RUN_TIME = ("status.",)


def test_every_copy_key_is_used() -> None:
    shared = SHARED.read_text(encoding="utf-8")
    keys = sorted(set(ENTRY.findall(shared)))
    assert len(keys) > 50, "the copy table moved; update this test"
    code = "\n".join(
        p.read_text(encoding="utf-8")
        for p in SRC.rglob("*")
        if p.suffix in {".js", ".jsx"} and ".test." not in p.name and p != SHARED
    )
    code += "\n" + ENTRY.sub("", shared)
    unused = [
        key
        for key in keys
        if not key.startswith(BUILT_AT_RUN_TIME)
        and not re.search(rf"['\"`]{re.escape(key)}['\"`]", code)
    ]
    assert not unused, (
        "Copy nothing on the page uses; delete it from both languages in "
        f"shared.jsx: {unused}"
    )
