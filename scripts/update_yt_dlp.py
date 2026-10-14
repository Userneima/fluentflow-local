"""Upgrade yt-dlp in the current virtual environment, at most once per day.

Douyin, Bilibili and YouTube change their pages often and yt-dlp ships a fix
within days; an old copy makes a whole platform fail with a generic download
error the user cannot act on. The launchers run this script in the background
before starting the server, so every launch keeps yt-dlp current without
delaying startup. The backend runs yt-dlp as a fresh ``python -m yt_dlp``
subprocess per download, so an upgrade takes effect for the next download.

Behaviour:
- Skips when the last successful upgrade was today (local date). The date is
  kept in ``<FluentFlow data dir>/yt_dlp_last_update``.
- Records the date only after pip succeeds, so an offline launch tries again
  on the next launch instead of waiting a day.
- Never raises and always exits 0: a failed upgrade leaves the installed
  yt-dlp in place, which is still better than not starting.
- pip honours ``PIP_INDEX_URL`` and pip.conf, so a configured mirror is used.
  With none configured, a failed upgrade is tried once more through the
  Tsinghua mirror, the same fallback the installer uses, so a user in China
  who never set a mirror still gets the daily upgrade.
- One upgrade at a time: a second launch while the first one's pip is still
  running skips, instead of two pips writing the same package at once.

Manual use:
    .venv/bin/python scripts/update_yt_dlp.py           # once per day
    .venv/bin/python scripts/update_yt_dlp.py --force   # upgrade now
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Long enough for a slow connection to finish; killing pip halfway through an
# install is what leaves yt-dlp half-written.
PIP_TIMEOUT_SECONDS = 600
STAMP_NAME = "yt_dlp_last_update"
LOCK_NAME = "yt_dlp_update.lock"
# A lock older than one pip run plus a margin belongs to a run that died.
LOCK_STALE_SECONDS = PIP_TIMEOUT_SECONDS * 2 + 60
MIRROR_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"


def stamp_path() -> Path:
    from backend.core.runtime_paths import app_data_root

    return app_data_root() / STAMP_NAME


def checked_today(stamp: Path, today: dt.date) -> bool:
    try:
        return stamp.read_text(encoding="utf-8").strip() == today.isoformat()
    except (OSError, ValueError):
        return False


def installed_version() -> str | None:
    try:
        return metadata.version("yt-dlp")
    except metadata.PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a broken install must not stop the launcher
        return None


def acquire_lock(lock: Path, now: float | None = None) -> bool:
    """Take the one-upgrade-at-a-time lock, or say another run holds it."""
    now = time.time() if now is None else now
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.exists() and now - lock.stat().st_mtime > LOCK_STALE_SECONDS:
            lock.unlink(missing_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    except OSError:
        return True  # cannot lock at all: better one upgrade than none
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return True


def run_pip(timeout: float = PIP_TIMEOUT_SECONDS, index_url: str | None = None) -> tuple[bool, str]:
    """Run the upgrade. Returns (ok, short reason when not ok)."""
    cmd = [
        sys.executable, "-m", "pip", "install",
        "-U", "--disable-pip-version-check", "-q",
        *(["-i", index_url] if index_url else []),
        "yt-dlp",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, f"pip 超过 {int(timeout)} 秒没有完成，可能是网络不通"
    except OSError as exc:
        return False, f"无法运行 pip：{exc}"
    if result.returncode != 0:
        lines = [line.strip() for line in (result.stderr or result.stdout or "").splitlines() if line.strip()]
        detail = lines[-1] if lines else f"退出码 {result.returncode}"
        return False, detail[:300]
    return True, ""


def update(force: bool = False, today: dt.date | None = None, stamp: Path | None = None) -> str:
    """Do the work and return the one line to print. Never raises."""
    lock: Path | None = None
    try:
        today = today or dt.date.today()
        stamp = stamp or stamp_path()
        if not force and checked_today(stamp, today):
            return f"yt-dlp：今天已检查过更新（{installed_version() or '未安装'}），跳过。"
        candidate = stamp.with_name(LOCK_NAME)
        if not acquire_lock(candidate):
            return "yt-dlp：另一个启动正在更新，这次跳过。"
        lock = candidate

        before = installed_version()
        ok, reason = run_pip()
        if not ok and not os.environ.get("PIP_INDEX_URL"):
            ok, reason = run_pip(index_url=MIRROR_INDEX_URL)
        if not ok:
            return (
                f"yt-dlp：更新失败，继续使用现有版本 {before or '（未安装）'}。原因：{reason}。"
                "下次启动会再试；手动更新：python scripts/update_yt_dlp.py --force"
            )

        after = installed_version()
        try:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(today.isoformat(), encoding="utf-8")
        except OSError:
            pass  # without the stamp the next launch just checks again
        if before != after:
            return f"yt-dlp：已从 {before or '未安装'} 更新到 {after}，之后的链接下载用新版本。"
        return f"yt-dlp：已是最新版本 {after}。"
    except Exception as exc:  # noqa: BLE001 - must never break the launcher
        return f"yt-dlp：检查更新时出错，已跳过：{exc}"
    finally:
        if lock is not None:
            try:
                lock.unlink(missing_ok=True)
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upgrade yt-dlp at most once per day.")
    parser.add_argument("--force", action="store_true", help="upgrade now even if checked today")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 0
    line = update(force=args.force)
    try:
        # A redirected stdout on Windows uses the ANSI code page, which cannot
        # encode the Chinese message; the Windows launcher writes it to a log file.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    try:
        print(line, flush=True)
    except OSError:
        pass  # the terminal or log pipe went away; the upgrade itself is done
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
