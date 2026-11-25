"""How every ffmpeg and ffprobe process is started.

ffmpeg reads its stdin for keystrokes unless told ``-nostdin``. When the
service was started from a terminal, an ffmpeg that inherited that tty kept
polling it after writing its output and never exited (2026-11-25: audio
extraction of a 68-minute recording sat at 18% for half an hour). So every
ffmpeg gets ``-nostdin`` right after the binary, and every ffmpeg and ffprobe
gets an empty stdin, here, instead of at each call site where one can be
forgotten. ``tests/test_ffmpeg_stdin.py`` fails on a call that starts one
any other way.

``run`` and ``popen`` take the same keyword arguments as ``subprocess.run``
and ``subprocess.Popen`` (except ``stdin`` and ``input``), so a caller keeps
its own output handling, timeouts and process group.
"""

from __future__ import annotations

import subprocess
from pathlib import PureWindowsPath
from typing import Any, Sequence


def _is_ffmpeg(program: str) -> bool:
    # Same test as ``device_profile.is_ffmpeg`` (``ffmpeg.exe`` on Windows);
    # repeated here so device_profile can start ffmpeg through this module.
    return PureWindowsPath(str(program)).name.lower() in {"ffmpeg", "ffmpeg.exe"}


def argv(command: Sequence[str]) -> list[str]:
    """``command`` as strings, with ``-nostdin`` right after the binary when it
    is ffmpeg and does not already say so. Anything else comes back unchanged."""
    args = [str(part) for part in command]
    if args and _is_ffmpeg(args[0]) and "-nostdin" not in args:
        args.insert(1, "-nostdin")
    return args


def run(command: Sequence[str], **kwargs: Any) -> "subprocess.CompletedProcess[Any]":
    """``subprocess.run`` for ffmpeg/ffprobe: ``-nostdin`` and an empty stdin."""
    return subprocess.run(argv(command), stdin=subprocess.DEVNULL, **kwargs)


def popen(command: Sequence[str], **kwargs: Any) -> "subprocess.Popen[Any]":
    """``subprocess.Popen`` for ffmpeg/ffprobe: ``-nostdin`` and an empty stdin."""
    return subprocess.Popen(argv(command), stdin=subprocess.DEVNULL, **kwargs)


__all__ = ["argv", "popen", "run"]
