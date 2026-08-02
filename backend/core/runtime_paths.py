"""Runtime storage paths for FluentFlow.

Code lives in the repository. Runtime data belongs in a user/system data
directory unless an explicit environment variable says otherwise.
"""

from __future__ import annotations

import os
import platform
import shutil
import string
from pathlib import Path


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


def app_data_root() -> Path:
    override = (os.environ.get("FLUENTFLOW_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser()

    # A recorded location always beats the free-space heuristic. Without this,
    # where the data lives is re-guessed on every startup, and a migration that
    # put it anywhere other than the drive the heuristic happens to pick is
    # silently abandoned — the workspace comes up empty on the next launch.
    recorded = read_data_root_pointer()
    if recorded:
        return recorded

    system = platform.system().lower()
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if system == "windows":
        return _windows_data_root()

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"
    return root / "fluentflow"


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
