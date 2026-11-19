"""A hint that tells someone to click a button names a button the page has (decision 6.18).

Requirements (owner, 2026-11-19, docs/方案/2026-11-19-全项目优化和防再犯机制.md):

- Every 点「X」 / 点击「X」 in the backend and the MCP server names a whole
  piece of page text: a string literal or JSX text that is exactly X (「重新转录」
  must not pass because 「重新转录失败」 is an error message). Four hints in ``visual_note_job`` kept
  sending people to 「重新转录」 for weeks after 6.33 took that button off the
  editor; nothing failed.
- Buttons on windows FluentFlow does not draw (the macOS keychain prompt) are
  listed below with where they live.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLICK = re.compile(r"(?:点击|点一下|点)\s*「([^」]{1,30})」")

# Buttons on windows outside FluentFlow's page.
OTHER_WINDOWS = {
    "始终允许": "macOS keychain prompt when reading a browser's login",
}


def _frontend_text() -> str:
    files = (ROOT / "frontend" / "src").rglob("*")
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in files
        if p.suffix in {".js", ".jsx"} and ".test." not in p.name
    )


def _on_page(label: str, page: str) -> bool:
    quoted = re.escape(label)
    return re.search(rf"(['\"`]){quoted}\1|>\s*{quoted}\s*<", page) is not None


def test_every_button_a_hint_names_is_on_the_page() -> None:
    page = _frontend_text()
    missing = []
    for root in ("backend", "scripts"):
        for path in (ROOT / root).rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for match in CLICK.finditer(line):
                    label = match.group(1)
                    if label in OTHER_WINDOWS or _on_page(label, page):
                        continue
                    missing.append(f"{path.relative_to(ROOT)}:{number} 「{label}」")
    assert not missing, (
        "These hints send people to a button the page does not have; point them "
        "at what exists now:\n" + "\n".join(missing)
    )
