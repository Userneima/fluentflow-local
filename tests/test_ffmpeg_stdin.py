"""ffmpeg must not read the service's stdin.

When the service is started from a terminal, an ffmpeg child that reads stdin
can keep polling that tty after its input ends and never exit (2026-11-25:
audio extraction of a 68-minute recording hung at 18%). Every ffmpeg and
ffprobe is started through ``backend/core/ffmpeg_cmd.py``, which adds
``-nostdin`` and an empty stdin; the scan below fails on a call that starts
one any other way.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from backend.core import audio_handler, ffmpeg_cmd

ROOT = Path(__file__).resolve().parents[1]

_SPAWNERS = {"run", "Popen", "check_output", "check_call", "call"}

# Direct subprocess calls whose command is a parameter, in modules that also
# mention ffmpeg. Each says why it never starts ffmpeg or ffprobe itself.
_NOT_FFMPEG = {
    # yt-dlp (it starts its own ffmpeg to merge, with its own stdin handling).
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
    """The text the command is built from, and whether it is a parameter we
    cannot see into."""
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
        params = {a.arg for a in func.args.args + func.args.kwonlyargs + func.args.posonlyargs}
        return first.id, first.id in params
    return ast.unparse(first), False


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
                offenders.append(f"{rel}:{call.lineno} {name}() spawns a command it is handed")
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
