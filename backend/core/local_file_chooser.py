"""Ask the machine's own file dialog which recording to work on.

The browser's file picker cannot answer the question this product needs answered.
It lets the user walk to a file and hands the page the bytes, the name, the size
and the type — and deliberately not the folder, because a web page must not learn
the shape of your disk. That is why "save the cut file next to the original" is
impossible for an upload: there is no "next to".

The local edition runs on the user's own machine, so it can ask the system
instead. ``osascript`` opens the same Finder dialog the user already knows and
returns a real absolute path. Two things follow from that path existing: the
recording is read where it is instead of being copied into the store first (no
second copy of a gigabyte), and the cut version can be written beside it.

Verified on macOS before this was built on: the dialog opens and blocks until
someone chooses, and the process returns POSIX paths.

Boundaries, because this makes a window appear on somebody's screen:

- **macOS only, local edition only.** Elsewhere it reports that it is unavailable
  and the caller falls back to the ordinary upload. There is no hosted route.
- **It times out.** A dialog nobody answers must not hold a worker or a request
  forever, so it is killed and reported as cancelled.
- **Cancel is a normal answer**, not an error to show.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Long enough to walk to a folder and think, short enough that a forgotten dialog
# does not pin a request open for the rest of the day.
DEFAULT_TIMEOUT_SECONDS = 300

TIMEOUT_ENV = "FLUENTFLOW_FILE_CHOOSER_TIMEOUT"

# AppleScript's own code for "the user pressed Cancel".
_CANCEL_MARKERS = ("-128", "User canceled", "用户已取消")

# AppleScript's code for "the app you asked me to talk to is not allowed".
_NOT_AUTHORISED_MARKERS = ("-1743", "Not authorized", "not allowed to send")

# The picker's own file filter. Without it the dialog lists every file in the
# folder and the recording has to be found by eye among them.
_MEDIA_TYPES = '{"public.movie", "public.audio"}'

_COLLECT = """
set out to ""
if class of chosen is list then
    repeat with item_ref in chosen
        set out to out & POSIX path of item_ref & linefeed
    end repeat
else
    set out to POSIX path of chosen
end if
return out
"""

# Measured on macOS 15, and the reason this is not a bare `choose file`: osascript
# is a background process, so the dialog opens *behind* the browser and the owner
# has to go hunting for it. With the dialog open, the frontmost application was
# still Google Chrome. A plain `activate` does not fix it and neither does System
# Events — both are background too. Finder is a real application and can come
# forward, so Finder is asked to put up the dialog.
_SCRIPT_VIA_FINDER = """
tell application "Finder"
    activate
    set chosen to choose file with prompt "{prompt}" of type {types}{location} {multiple}
end tell
""" + _COLLECT

# Talking to Finder needs this machine's permission to control it, and the owner
# can refuse. Refusal must not cost them the picker, only its manners.
_SCRIPT_PLAIN = """
set chosen to choose file with prompt "{prompt}" of type {types}{location} {multiple}
""" + _COLLECT


# The folder variant. Same two scripts for the same reason — Finder puts the
# dialog in front of the browser, and a machine that refuses to be told what to do
# still gets a working dialog with worse manners.
_FOLDER_VIA_FINDER = """
tell application "Finder"
    activate
    set chosen to choose folder with prompt "{prompt}"{location}
end tell
""" + _COLLECT

_FOLDER_PLAIN = """
set chosen to choose folder with prompt "{prompt}"{location}
""" + _COLLECT


class FileChooserError(RuntimeError):
    """A reason worth showing the user, in their language."""


@dataclass(frozen=True)
class ChooserResult:
    paths: list[Path]
    cancelled: bool = False

    @property
    def chose_something(self) -> bool:
        return bool(self.paths) and not self.cancelled


def timeout_seconds() -> int:
    raw = (os.environ.get(TIMEOUT_ENV) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def osascript_path() -> str | None:
    return shutil.which("osascript")


def unavailable_reason() -> str | None:
    """Why the system dialog cannot be used here, phrased for a user.

    ``None`` means it can. The caller is expected to keep the ordinary upload
    available either way — this is a convenience that knows the path, not the only
    way in.
    """
    if sys.platform != "darwin":
        return "只有 macOS 能调起系统的文件选择框。用上面的「本地上传」照样可以处理，只是剪后文件不会自动存到原文件旁边。"
    if not osascript_path():
        return "这台机器上找不到 osascript，没法调起系统的文件选择框。"
    return None


def _location_clause(start_in: Path | str | None) -> str:
    """Where the dialog opens, when there is a sensible answer.

    Without this it opens wherever Finder happened to be last, which for someone
    whose recordings all live in one folder means navigating there again every
    single time. The caller supplies the folder this machine last worked in.
    """
    if not start_in:
        return ""
    folder = Path(start_in).expanduser()
    if not folder.is_dir():
        return ""
    return f' default location (POSIX file "{str(folder).replace(chr(34), "")}")'


def choose_media_files(
    *,
    prompt: str = "选择要做笔记的录像",
    allow_multiple: bool = True,
    start_in: Path | str | None = None,
    runner: object = None,
) -> ChooserResult:
    """Open the system file dialog and return what was chosen.

    Asks Finder to put the dialog up so it lands in front of the browser rather
    than behind it, and falls back to a plain dialog if this machine refuses to
    let us talk to Finder — the picker still works then, it is just badly
    mannered, which beats not working.

    ``runner`` is injectable so tests can assert on the assembled command and on
    every outcome — chosen, cancelled, timed out — without a window opening on
    anybody's screen.
    """
    reason = unavailable_reason()
    if reason:
        raise FileChooserError(reason)
    binary = osascript_path()
    fields = {
        "prompt": prompt.replace('"', "'"),
        "types": _MEDIA_TYPES,
        "location": _location_clause(start_in),
        "multiple": "with multiple selections allowed" if allow_multiple else "",
    }
    return _run_dialog(binary, (_SCRIPT_VIA_FINDER, _SCRIPT_PLAIN), fields, runner)


def choose_media_folder(
    *,
    prompt: str = "选择装着录像的文件夹",
    start_in: Path | str | None = None,
    runner: object = None,
) -> ChooserResult:
    """Open the system folder dialog and return the folder that was chosen.

    Separate from the file dialog rather than a flag on it, because the two are
    different questions: one recording is one task, and a folder is however many
    recordings are in it, each spending a Claude call. Which of the two the user
    asked for decides what has to be confirmed afterwards, so the entry says which
    one it is instead of the caller inferring it from how many paths came back.
    """
    reason = unavailable_reason()
    if reason:
        raise FileChooserError(reason)
    binary = osascript_path()
    fields = {
        "prompt": prompt.replace('"', "'"),
        "location": _location_clause(start_in),
    }
    return _run_dialog(binary, (_FOLDER_VIA_FINDER, _FOLDER_PLAIN), fields, runner)


def _run_dialog(
    binary: object, templates: tuple[str, ...], fields: dict[str, str], runner: object
) -> ChooserResult:
    """Put one of these dialogs up, and read what came back.

    Shared by both entries so that a timeout, a cancel, or a machine that will not
    let us drive Finder behaves identically whichever one the user opened — those
    three outcomes are the ones a person actually hits, and they were tested once.
    """
    execute = runner if runner is not None else subprocess.run
    last_error = ""
    for template in templates:
        command = [str(binary), "-e", template.format(**fields)]
        try:
            completed = execute(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.info("file chooser timed out after %ss", timeout_seconds())
            return ChooserResult(paths=[], cancelled=True)
        except OSError as exc:
            raise FileChooserError(f"打不开系统的文件选择框：{exc}") from exc

        stderr = (getattr(completed, "stderr", "") or "").strip()
        if getattr(completed, "returncode", 0) != 0:
            if any(marker in stderr for marker in _CANCEL_MARKERS):
                return ChooserResult(paths=[], cancelled=True)
            last_error = stderr
            if any(marker in stderr for marker in _NOT_AUTHORISED_MARKERS):
                logger.info("not allowed to drive Finder, falling back to a plain dialog")
                continue
            raise FileChooserError(f"系统的文件选择框返回了错误：{stderr[:200] or '未知原因'}")

        paths = [
            Path(line.strip())
            for line in (getattr(completed, "stdout", "") or "").splitlines()
            if line.strip()
        ]
        if not paths:
            return ChooserResult(paths=[], cancelled=True)
        return ChooserResult(paths=paths)
    raise FileChooserError(f"系统的文件选择框返回了错误：{last_error[:200] or '未知原因'}")


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "TIMEOUT_ENV",
    "ChooserResult",
    "FileChooserError",
    "choose_media_files",
    "choose_media_folder",
    "osascript_path",
    "timeout_seconds",
    "unavailable_reason",
]
