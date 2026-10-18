"""Show a task's output folder in the system's file manager.

A task's outputs go into a folder of the user's (beside the recording, or under
the Videos folder for a link), but nothing on the page said where, and the only
button was "download", which reads as "the file is in the app" (owner,
2026-10-18, on Windows). The page now names the folder and opens it: Explorer
on Windows, Finder on macOS, the desktop's handler elsewhere.

Only a folder the task itself recorded is ever opened; the request names a
task, never a path.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class OpenFolderError(RuntimeError):
    """A reason worth showing the user."""


def task_output_folder(result: dict[str, Any] | None) -> Path | None:
    """The folder the task delivered into, if it recorded one."""
    record = (result or {}).get("output_folder") if isinstance(result, dict) else None
    path = str((record or {}).get("path") or "").strip() if isinstance(record, dict) else ""
    return Path(path) if path else None


def open_command(folder: Path, platform: str | None = None) -> list[str]:
    current = platform or sys.platform
    if current == "win32":
        return ["explorer", str(folder)]
    if current == "darwin":
        return ["open", str(folder)]
    return ["xdg-open", str(folder)]


def open_folder(folder: Path, *, launch: Callable[..., Any] | None = None) -> None:
    if not folder.is_dir():
        raise OpenFolderError(f"产出文件夹不在原来的位置了：{folder}。挪回去，或者在处理记录里重新整理一次。")
    try:
        # Not waited on: the file manager is the user's window from here.
        (launch or subprocess.Popen)(
            open_command(folder), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        logger.warning("could not open %s: %s", folder, exc)
        raise OpenFolderError(f"没能打开文件夹（{exc}）。路径是：{folder}") from exc


__all__ = ["OpenFolderError", "open_command", "open_folder", "task_output_folder"]
