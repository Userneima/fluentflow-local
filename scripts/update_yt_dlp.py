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

Manual use:
    .venv/bin/python scripts/update_yt_dlp.py           # once per day
    .venv/bin/python scripts/update_yt_dlp.py --force   # upgrade now
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PIP_TIMEOUT_SECONDS = 120
STAMP_NAME = "yt_dlp_last_update"


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


def run_pip(timeout: float = PIP_TIMEOUT_SECONDS) -> tuple[bool, str]:
    """Run the upgrade. Returns (ok, short reason when not ok)."""
    cmd = [
        sys.executable, "-m", "pip", "install",
        "-U", "--disable-pip-version-check", "-q", "yt-dlp",
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
    try:
        today = today or dt.date.today()
        stamp = stamp or stamp_path()
        if not force and checked_today(stamp, today):
            return f"yt-dlp：今天已检查过更新（{installed_version() or '未安装'}），跳过。"

        before = installed_version()
        ok, reason = run_pip()
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
