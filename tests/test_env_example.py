"""The settings template lists only settings FluentFlow Local reads.

Requirement: a user who copies ``distribution/local.env.example`` and sets a
``FLUENTFLOW_*`` line there gets an effect. A line nothing reads looks like a
setting and silently does nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "distribution" / "local.env.example"
READERS = ("backend", "scripts", "launchers", "frontend/src")
SUFFIXES = {".py", ".sh", ".command", ".ps1", ".applescript", ".js", ".jsx", ".ts", ".tsx", ".mjs"}


def _sources() -> str:
    chunks = []
    for folder in READERS:
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and path.suffix in SUFFIXES and "node_modules" not in path.parts:
                chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    for extra in ("package.json", "frontend/vite.config.js", "frontend/vite.config.mjs"):
        path = ROOT / extra
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


def test_every_fluentflow_setting_in_the_template_is_read_somewhere():
    names = sorted(set(re.findall(r"^#?\s*(FLUENTFLOW_[A-Z0-9_]+)=", EXAMPLE.read_text(encoding="utf-8"), re.M)))
    assert names, "the template lists no FLUENTFLOW_ settings at all"
    sources = _sources()
    unread = [name for name in names if not re.search(rf"\b{name}\b", sources)]
    assert unread == [], f"listed in local.env.example but read nowhere: {unread}"
