"""Static checks on the Windows launchers, which CI cannot run.

From the requirement (2026-10-14):

- A .ps1 with any non-ASCII text (the Chinese messages) starts with a UTF-8 BOM:
  Windows PowerShell 5.1 reads a file without one in the ANSI code page, which
  garbles the text and can break the script's quoting.
- The launcher does not run uvicorn under ErrorActionPreference=Stop: PowerShell
  5.1 turns the first stderr line of a piped native command into a terminating
  error, and uvicorn logs to stderr.
- The uninstaller recognises a running local service by the fields /health has.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "launchers" / "windows"


def test_a_powershell_script_with_non_ascii_text_carries_a_bom():
    for script in ROOT.glob("*.ps1"):
        raw = script.read_bytes()
        if any(byte > 127 for byte in raw.removeprefix(b"\xef\xbb\xbf")):
            assert raw.startswith(b"\xef\xbb\xbf"), f"{script.name} has non-ASCII text but no UTF-8 BOM"


def test_uvicorn_does_not_run_under_stop():
    text = (ROOT / "FluentFlowLocal.ps1").read_text(encoding="utf-8-sig")
    before = text[: text.index("-m uvicorn")]
    assert before.rfind('$ErrorActionPreference = "Continue"') > before.rfind('$ErrorActionPreference = "Stop"')


def test_the_uninstaller_reads_the_fields_health_has():
    text = (ROOT / "uninstall-local.ps1").read_text(encoding="utf-8-sig")
    assert "$Health.runtime.execution" in text or "$Health.edition" in text
    assert "$Jobs.jobs" in text


def test_the_service_starts_in_python_utf8_mode():
    """Owner's Windows machine, 2026-10-16: without UTF-8 mode every text read
    that names no encoding used GBK, and ffmpeg's or yt-dlp's UTF-8 output with
    a Chinese name in it stopped the read waiting for it."""
    for script in (ROOT / "FluentFlowLocal.ps1", ROOT.parents[1] / "install.ps1"):
        text = script.read_text(encoding="utf-8-sig")
        started = text.index("backend.local_main:app", text.index("uvicorn"))
        assert '$env:PYTHONUTF8 = "1"' in text[:started], script.name
