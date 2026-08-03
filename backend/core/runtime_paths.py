"""Runtime storage paths for FluentFlow.

Code lives in the repository. Runtime data belongs in a user/system data
directory unless an explicit environment variable says otherwise.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Final


logger = logging.getLogger(__name__)

APP_NAME = "FluentFlow"
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# A single transcription keeps its whole source file on disk, so this directory
# grows in gigabytes, not megabytes. %APPDATA% lives on the system drive, which
# is the one drive a user cannot afford to fill, so on Windows a data drive is
# preferred over C: unless the machine has no suitable second drive.
DATA_DRIVE_MIN_FREE_BYTES = 5 * 1024 * 1024 * 1024


def windows_appdata_root() -> Path:
    appdata = os.environ.get("APPDATA")
    return (Path(appdata).expanduser() if appdata else Path.home() / "AppData" / "Roaming") / APP_NAME


DRIVE_FIXED = 3


def _system_drive() -> str:
    return (os.environ.get("SystemDrive") or "C:").strip().rstrip("\\").upper()


def _is_fixed_drive(drive: str) -> bool:
    """True only for internal disks.

    Removable and network volumes must never become the default: a USB disk
    with plenty of free space would win on size, and then the workspace
    disappears the moment it is unplugged.
    """
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\")) == DRIVE_FIXED
    except (AttributeError, OSError, ValueError):
        # No kernel32 (non-Windows, or a stubbed test environment): fall back to
        # treating the volume as usable, since the caller already filtered to
        # existing drives with free space.
        return True


def _candidate_data_drives() -> list[tuple[int, str]]:
    """Fixed non-system drives with room to spare, most free space first."""
    system = _system_drive()
    candidates: list[tuple[int, str]] = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:"
        if drive == system or not Path(f"{drive}\\").is_dir():
            continue
        try:
            free = shutil.disk_usage(f"{drive}\\").free
        except OSError:
            # Empty optical drives, disconnected network mappings, and
            # permission-denied volumes all fail here; none can hold data.
            continue
        if free >= DATA_DRIVE_MIN_FREE_BYTES and _is_fixed_drive(drive):
            candidates.append((free, drive))
    # Sort by free space, then letter, so the choice never flips between runs
    # on machines with equally sized volumes.
    return sorted(candidates, key=lambda item: (-item[0], item[1]))


def _windows_data_root() -> Path:
    legacy = windows_appdata_root()
    candidates = _candidate_data_drives()
    if not candidates:
        return legacy
    preferred = Path(f"{candidates[0][1]}\\") / APP_NAME
    # An install that already keeps its jobs database on the system drive must
    # keep reading it: silently pointing at an empty directory would present a
    # fresh, empty workspace and look like every past record was lost.
    # `scripts/migrate_data_dir.py` moves it deliberately instead.
    if preferred != legacy and not preferred.exists() and legacy.is_dir() and any(legacy.iterdir()):
        return legacy
    return preferred


def data_root_pointer_path() -> Path:
    """Where the data directory's location is recorded.

    Deliberately outside the data directory itself: a migration moves and
    renames that directory, so a marker kept inside it cannot survive the move
    it exists to describe.
    """
    if platform.system().lower() == "windows":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base).expanduser() if base else Path.home() / "AppData" / "Local"
        return root / APP_NAME / "data-root.txt"
    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "fluentflow" / "data-root"


def read_data_root_pointer() -> Path | None:
    """The recorded data directory, or None when nothing has been recorded.

    Returned even when the path is currently unreachable — an unplugged data
    drive must surface as a plain "cannot open database" error, not as a fresh
    empty workspace that reads like every record was lost.
    """
    try:
        recorded = data_root_pointer_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(recorded).expanduser() if recorded else None


def write_data_root_pointer(target: Path) -> None:
    """Record where the data directory now lives, for every later startup."""
    pointer = data_root_pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(Path(target).expanduser()), encoding="utf-8")


# Every environment variable that can move one piece of the workspace on its
# own. They are legitimate operator tools, but nine independent overrides mean a
# workspace can end up scattered across drives with nothing saying so — which is
# how a jobs database on one disk and a config file on another looked, from the
# UI, exactly like every record had been lost.
#
# So they stay, and `resolve_workspace().overrides` reports the ones in effect
# for the readiness check to print. Silence was the actual defect.
PATH_OVERRIDE_ENV_NAMES: Final[tuple[str, ...]] = (
    "FLUENTFLOW_CONFIG_PATH",
    "FLUENTFLOW_JOB_DB_PATH",
    "FLUENTFLOW_EVENT_DB_PATH",
    "FLUENTFLOW_SOURCE_DIR",
    "FLUENTFLOW_ARTIFACT_DIR",
    "FLUENTFLOW_EDITED_TRANSCRIPT_DIR",
    "FLUENTFLOW_TRANSCRIPT_EDIT_RECORDS_DIR",
    "FLUENTFLOW_VIDEO_SOURCE_DIR",
    "FLUENTFLOW_CODEX_EXPORT_DIR",
)

# How the workspace root was decided, most authoritative first.
DECIDED_BY_ENV: Final[str] = "env"
DECIDED_BY_RECORDED: Final[str] = "recorded"
DECIDED_BY_CHOSEN: Final[str] = "chosen"
DECIDED_BY_DEFAULT: Final[str] = "default"

# A guess, as opposed to something an operator or a past migration stated.
_GUESSED = frozenset({DECIDED_BY_CHOSEN, DECIDED_BY_DEFAULT})


@dataclass(frozen=True)
class Workspace:
    """Where the workspace is, how that was decided, and what is scattered."""

    root: Path
    decided_by: str
    overrides: tuple[tuple[str, Path], ...] = ()

    @property
    def is_guess(self) -> bool:
        return self.decided_by in _GUESSED

    def describe(self) -> str:
        reason = {
            DECIDED_BY_ENV: "由 FLUENTFLOW_DATA_DIR 指定",
            DECIDED_BY_RECORDED: "沿用已记录的迁移结果",
            DECIDED_BY_CHOSEN: "首次运行按可用空间选定",
            DECIDED_BY_DEFAULT: "使用系统默认目录",
        }.get(self.decided_by, self.decided_by)
        text = f"{self.root}（{reason}）"
        if self.overrides:
            scattered = "、".join(name for name, _ in self.overrides)
            text += f"；另有 {len(self.overrides)} 项被单独指向别处：{scattered}"
        return text


def _default_root() -> tuple[Path, str]:
    system = platform.system().lower()
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME, DECIDED_BY_DEFAULT
    if system == "windows":
        root = _windows_data_root()
        return root, DECIDED_BY_CHOSEN if root != windows_appdata_root() else DECIDED_BY_DEFAULT

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"
    return base / "fluentflow", DECIDED_BY_DEFAULT


def workspace_overrides() -> tuple[tuple[str, Path], ...]:
    """Per-path overrides currently in effect, in declaration order."""
    active: list[tuple[str, Path]] = []
    for name in PATH_OVERRIDE_ENV_NAMES:
        value = (os.environ.get(name) or "").strip()
        if value:
            active.append((name, Path(value).expanduser()))
    return tuple(active)


def resolve_workspace() -> Workspace:
    """The one place the workspace location is decided.

    Order: an explicit `FLUENTFLOW_DATA_DIR`, then the location a past migration
    recorded, then a default. Only the last is a guess, and
    `ensure_workspace_recorded` turns it into a recorded decision on first run so
    it is never guessed twice — a heuristic re-run on every startup is what
    silently abandoned a workspace that had been moved elsewhere.
    """
    override = (os.environ.get("FLUENTFLOW_DATA_DIR") or "").strip()
    if override:
        return Workspace(Path(override).expanduser(), DECIDED_BY_ENV, workspace_overrides())

    recorded = read_data_root_pointer()
    if recorded:
        return Workspace(recorded, DECIDED_BY_RECORDED, workspace_overrides())

    root, decided_by = _default_root()
    return Workspace(root, decided_by, workspace_overrides())


def ensure_workspace_recorded() -> Workspace:
    """Pin a first-run guess so later startups read it instead of re-deciding.

    Called from startup paths only. A failure to record is not fatal: the same
    location is still resolvable, it just gets decided again next time.
    """
    workspace = resolve_workspace()
    if not workspace.is_guess:
        return workspace
    try:
        write_data_root_pointer(workspace.root)
    except OSError:
        logger.warning("Could not record the workspace location at %s", data_root_pointer_path())
        return workspace
    return Workspace(workspace.root, DECIDED_BY_RECORDED, workspace.overrides)


def app_data_root() -> Path:
    return resolve_workspace().root


def runtime_path(env_name: str, *parts: str) -> Path:
    override = (os.environ.get(env_name) or "").strip()
    if override:
        return Path(override).expanduser()
    return app_data_root().joinpath(*parts)


def default_config_path() -> Path:
    return runtime_path("FLUENTFLOW_CONFIG_PATH", "fluentflow_config.json")


def default_job_db_path() -> Path:
    return runtime_path("FLUENTFLOW_JOB_DB_PATH", "fluentflow_jobs.sqlite")


def default_event_db_path() -> Path:
    return runtime_path("FLUENTFLOW_EVENT_DB_PATH", "fluentflow_events.sqlite")


def default_source_dir() -> Path:
    return runtime_path("FLUENTFLOW_SOURCE_DIR", "sources")


def default_artifact_dir() -> Path:
    return runtime_path("FLUENTFLOW_ARTIFACT_DIR", "artifacts")


def default_edited_transcript_dir() -> Path:
    return runtime_path("FLUENTFLOW_EDITED_TRANSCRIPT_DIR", "edited_transcripts")


def default_transcript_edit_records_dir() -> Path:
    return runtime_path("FLUENTFLOW_TRANSCRIPT_EDIT_RECORDS_DIR", "transcript_edit_records")


def default_video_source_dir() -> Path:
    return runtime_path("FLUENTFLOW_VIDEO_SOURCE_DIR", "video_sources")


def default_codex_export_dir() -> Path:
    return runtime_path("FLUENTFLOW_CODEX_EXPORT_DIR", "codex_exports")


def legacy_repo_data_root() -> Path:
    return PROJECT_ROOT / "data"


def legacy_backend_data_root() -> Path:
    return PROJECT_ROOT / "backend" / "data"


def legacy_backend_video_source_dir() -> Path:
    return PROJECT_ROOT / "backend" / "视频文件"
