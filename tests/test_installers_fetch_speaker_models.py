"""Requirement: both installers fetch the speaker-separation models after the
Python dependencies, so the feature works out of the box, and a failed model
download prints a warning and lets the install finish."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_macos_installer_fetches_the_models_without_failing_the_install():
    text = (ROOT / "launchers" / "macos" / "setup-local.sh").read_text(encoding="utf-8")
    call = text.index('scripts/fetch_diarization_models.py"')
    assert call > text.index("requirements-local.txt")
    line_start = text.rfind("\n", 0, call)
    assert text[line_start:call].lstrip().startswith("if !"), "the fetch must run inside `if !` so set -e does not abort"
    block = text[call: text.index("\nfi\n", call)]
    assert "fail " not in block


def test_windows_installer_fetches_the_models_without_failing_the_install():
    text = (ROOT / "launchers" / "windows" / "setup-local.ps1").read_text(encoding="utf-8-sig")
    call = text.index("fetch_diarization_models.py")
    assert call > text.index("requirements-local.txt")
    line = text[text.rfind("\n", 0, call): text.index("\n", call)]
    assert "Invoke-Checked" not in line, "Invoke-Checked would stop the install on a failed download"
    assert re.search(r"if \(\$LASTEXITCODE -ne 0\)", text[call: call + 400])
