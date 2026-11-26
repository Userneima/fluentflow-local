"""Children must not read the service's stdin.

When the service is started from a terminal, a child that reads stdin can keep
polling that tty after its input ends and never exit (2026-11-25: audio
extraction of a 68-minute recording hung at 18%). Every ffmpeg and ffprobe is
started through ``backend/core/ffmpeg_cmd.py``, which adds ``-nostdin`` and an
empty stdin; the scan below fails on a call that starts one any other way.
yt-dlp is started with an empty stdin too, and the service Windows starts has
no terminal on stdin at all (2026-11-26).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from backend.core import audio_handler, ffmpeg_cmd, video_source

ROOT = Path(__file__).resolve().parents[1]

_SPAWNERS = {"run", "Popen", "check_output", "check_call", "call"}

# Direct subprocess calls whose command is a parameter, in modules that also
# mention ffmpeg. Each says why it never starts ffmpeg or ffprobe itself.
_NOT_FFMPEG = {
    # yt-dlp (which starts its own ffmpeg to merge, with its own stdin
    # handling), started with an empty stdin itself.
    ("backend/core/video_source.py", "_run_process"),
    ("backend/core/video_source.py", "_run_process_streaming"),
}


def _sources() -> list[Path]:
    files = []
    for top in ("backend", "scripts"):
        files += [p for p in (ROOT / top).rglob("*.py") if "tests" not in p.relative_to(ROOT).parts]
    return files


def _mentions_media_tool(text: str) -> bool:
    return "ffmpeg" in text or "ffprobe" in text


def _spawn_calls(tree: ast.AST):
    """(call, enclosing function) for each direct subprocess spawn."""
    def visit(node: ast.AST, func: ast.AST | None):
        for child in ast.iter_child_nodes(node):
            inner = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "subprocess"
                and child.func.attr in _SPAWNERS
            ):
                yield child, func
            yield from visit(child, inner)
    yield from visit(tree, None)


def _argv_source(call: ast.Call, func: ast.AST | None) -> tuple[str, bool]:
    """The text the command is built from, and whether it comes from somewhere
    we cannot see into (a parameter, an attribute, a call)."""
    if not call.args:
        return "", False
    first = call.args[0]
    if isinstance(first, ast.Name) and func is not None:
        assigned = [
            ast.unparse(node.value)
            for node in ast.walk(func)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(isinstance(t, ast.Name) and t.id == first.id
                    for t in (node.targets if isinstance(node, ast.Assign) else [node.target]))
            and node.value is not None
        ]
        if assigned:
            return " ".join(assigned), False
        return first.id, True
    if isinstance(first, (ast.List, ast.Tuple)):
        return ast.unparse(first), False
    # An attribute (self._args), a call or anything else built elsewhere: we
    # cannot see what it starts, so it counts as a command handed in.
    return ast.unparse(first), True


def test_every_ffmpeg_and_ffprobe_starts_through_ffmpeg_cmd():
    offenders = []
    for path in _sources():
        rel = path.relative_to(ROOT).as_posix()
        if rel == "backend/core/ffmpeg_cmd.py":
            continue
        text = path.read_text(encoding="utf-8")
        if not _mentions_media_tool(text):
            continue
        for call, func in _spawn_calls(ast.parse(text)):
            source, is_param = _argv_source(call, func)
            name = getattr(func, "name", "<module>")
            if is_param and (rel, name) not in _NOT_FFMPEG:
                offenders.append(f"{rel}:{call.lineno} {name}() spawns a command built elsewhere")
            elif _mentions_media_tool(source):
                offenders.append(f"{rel}:{call.lineno} starts ffmpeg/ffprobe with subprocess")
    assert not offenders, (
        "Start ffmpeg and ffprobe with ffmpeg_cmd.run/popen (adds -nostdin and an "
        "empty stdin), or list a runner that never starts them in _NOT_FFMPEG:\n"
        + "\n".join(offenders)
    )


def test_ffmpeg_cmd_adds_nostdin_once_and_closes_stdin(monkeypatch):
    assert ffmpeg_cmd.argv(["/opt/bin/ffmpeg", "-i", "a"]) == ["/opt/bin/ffmpeg", "-nostdin", "-i", "a"]
    assert ffmpeg_cmd.argv([r"C:\tools\FFMPEG.EXE", "-i", "a"])[1] == "-nostdin"
    assert ffmpeg_cmd.argv(["ffmpeg", "-y", "-nostdin", "-i", "a"]).count("-nostdin") == 1
    assert ffmpeg_cmd.argv(["ffprobe", "-v", "error", "a"]) == ["ffprobe", "-v", "error", "a"]

    seen = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: seen.append(("run", args, kw)))
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: seen.append(("popen", args, kw)))
    ffmpeg_cmd.run(["ffmpeg", "-i", "a"], check=True)
    ffmpeg_cmd.popen(["ffprobe", "a"])
    assert [kw["stdin"] for _, _, kw in seen] == [subprocess.DEVNULL, subprocess.DEVNULL]
    assert seen[0][1][1] == "-nostdin" and seen[0][2]["check"] is True


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


class _NoOutput:
    """A child's pipe with nothing in it."""

    def readline(self) -> str:
        return ""

    def close(self) -> None:
        pass


def test_yt_dlp_is_started_without_the_terminal(monkeypatch):
    """The same rule for yt-dlp: it runs the recording's download and can sit
    waiting on the terminal it inherited, like ffmpeg did (2026-11-26)."""
    seen: list[dict] = []

    class FakePopen:
        pid = 4242
        returncode = 0

        def __init__(self, args, **kwargs):
            seen.append(kwargs)
            self.stdout, self.stderr = _NoOutput(), _NoOutput()

        def communicate(self, timeout=None):
            return "", ""

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(video_source.subprocess, "Popen", FakePopen)
    video_source._run_process(["yt-dlp", "--version"], timeout=5)
    video_source._run_process(["yt-dlp", "a link"], timeout=5, on_output_line=lambda _line: None)

    assert [kwargs.get("stdin") for kwargs in seen] == [subprocess.DEVNULL, subprocess.DEVNULL]


def test_the_service_windows_starts_has_no_terminal_on_stdin():
    """Windows' process service gives the service no console, but nothing it
    starts may depend on that: ``scripts/run_service.py`` puts the null device
    on stdin before uvicorn runs. Checked in a child of its own, so this
    test's own stdin is not touched: before the redirect it reads what was
    handed to it, after it reads nothing (end of file at once)."""
    script = "\n".join([
        "import os, sys",
        f"sys.path.insert(0, {str(ROOT / 'scripts')!r})",
        "import run_service",
        "run_service.detach_stdin()",
        "print('null' if os.read(0, 10) == b'' else 'inherited')",
    ])
    done = subprocess.run(
        [sys.executable, "-c", script], input="kept open\n", capture_output=True, text=True, timeout=120,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "null", done.stdout
