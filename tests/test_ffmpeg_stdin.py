"""ffmpeg must not read the service's stdin.

When the service is started from a terminal, an ffmpeg child that reads stdin
can keep polling that tty after its input ends and never exit (2026-11-25:
audio extraction of a 68-minute recording hung at 18%).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.core import audio_handler

ROOT = Path(__file__).resolve().parents[1]


def test_extract_stt_wav_closes_stdin(tmp_path, monkeypatch):
    src = tmp_path / "in.mov"
    src.write_bytes(b"x")
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(audio_handler, "_require_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(audio_handler.subprocess, "run", fake_run)
    audio_handler.extract_stt_wav(src, output_path=tmp_path / "out.wav")

    assert "-nostdin" in seen["args"]
    assert seen["kwargs"].get("stdin") is subprocess.DEVNULL


def test_macos_launcher_starts_service_without_terminal_stdin():
    text = (ROOT / "launchers/macos/FluentFlowLocal.command").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if l.startswith("exec ") and "uvicorn" in l)
    assert "</dev/null" in line
