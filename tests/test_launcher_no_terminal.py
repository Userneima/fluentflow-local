"""The Mac launcher never starts Terminal on its own.

Requirement (2026-11-26, decision 7.9): the desktop app starts the
service through FluentFlowLocal.command with no terminal. An AppleScript
`tell application "Terminal"` launches Terminal when it is not running, and
Terminal opens a new window as it starts, so every open of FluentFlow showed
an empty terminal window. Talking to Terminal is allowed only when the script
itself runs in Terminal and Terminal is already open.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_terminal_is_only_told_when_the_script_runs_in_an_open_terminal():
    text = (ROOT / "launchers/macos/FluentFlowLocal.command").read_text(encoding="utf-8")
    guard = 'if [[ "${TERM_PROGRAM:-}" == "Apple_Terminal" ]] && pgrep -xq Terminal; then'
    tells = [m.start() for m in re.finditer(r'^\s*tell application "Terminal"', text, re.MULTILINE)]
    assert tells
    for at in tells:
        before = text[:at]
        opened = before.rfind(guard)
        assert opened != -1, "a Terminal tell outside the guard"
        assert "\nfi\n" not in before[opened:], "a Terminal tell after the guard closed"
