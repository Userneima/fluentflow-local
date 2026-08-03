#!/usr/bin/env python3
"""Move an existing FluentFlow data directory onto the preferred data drive.

Windows installs created before the data-drive default keep their jobs
database, sources, and artifacts under ``%APPDATA%\\FluentFlow`` — on the system
drive, where a few long recordings can fill the disk. The backend deliberately
keeps reading that legacy directory rather than silently starting empty, so
moving it is an explicit, reviewable step.

The copy is verified before anything is removed, and the old directory is
renamed rather than deleted, so a failed or unwanted move is always reversible.
"""

from __future__ import annotations

import argparse
import shutil
import socket
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.runtime_paths import (  # noqa: E402
    APP_NAME,
    _candidate_data_drives,
    app_data_root,
    resolve_workspace,
    write_data_root_pointer,
)


def _service_is_running(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _tree_signature(root: Path) -> dict[str, int]:
    return {
        str(path.relative_to(root)): path.stat().st_size
        for path in root.rglob("*")
        if path.is_file()
    }


def _format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _resolve_target(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    drives = _candidate_data_drives()
    return Path(f"{drives[0][1]}\\") / APP_NAME if drives else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Move the FluentFlow data directory to a data drive.")
    parser.add_argument("--target", help="Destination directory (default: the fixed non-system drive with the most free space)")
    parser.add_argument("--port", type=int, default=8000, help="Local service port to check before moving (default: 8000)")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args()

    if not sys.platform.startswith("win"):
        print("This migration only applies to Windows installs.")
        return 0

    # Whatever the backend resolves today, not just the original %APPDATA%
    # location: a workspace that has already been moved once must still be
    # movable, and the source has to be the directory actually in use.
    source = app_data_root()
    if not source.is_dir() or not any(source.iterdir()):
        print(f"Nothing to move: {source} does not exist or is empty.")
        return 0

    target = _resolve_target(args.target)
    if target is None:
        print("No fixed non-system drive with at least 5 GB free was found.")
        print("Pass --target to choose a location, or set FLUENTFLOW_DATA_DIR.")
        return 1
    if target == source:
        print(f"Source and target are the same directory: {source}")
        return 1
    if target.exists() and any(target.iterdir()):
        print(f"Target already exists and is not empty: {target}")
        print("Move or remove it first so this migration cannot merge two workspaces.")
        return 1

    if _service_is_running(args.port):
        print(f"FluentFlow is still running on port {args.port}.")
        print("Close the launcher window first — moving an open database corrupts it.")
        return 1

    signature = _tree_signature(source)
    total = sum(signature.values())
    print(f"From : {source}")
    print(f"To   : {target}")
    print(f"Size : {_format_size(total)} in {len(signature)} files")
    if not args.yes:
        if input("Proceed? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Cancelled. Nothing was changed.")
            return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    print("Copying...")
    try:
        shutil.copytree(source, target, dirs_exist_ok=True)
    except OSError as exc:
        print(f"Copy failed: {exc}")
        print(f"Nothing was removed. The original data is still at {source}.")
        return 1

    copied = _tree_signature(target)
    missing = {name for name in signature if name not in copied}
    mismatched = {name for name, size in signature.items() if name in copied and copied[name] != size}
    if missing or mismatched:
        print(f"Verification failed: {len(missing)} missing, {len(mismatched)} size mismatch.")
        print(f"Nothing was removed. The original data is still at {source}.")
        return 1

    # Record the new location before retiring the old one. Startup reads this
    # instead of re-running the free-space heuristic, so "now uses" below is a
    # fact rather than a guess that a differently sized drive can overturn.
    write_data_root_pointer(target)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    retired = source.with_name(f"{source.name}.migrated-{stamp}")
    source.rename(retired)
    print(f"Done. FluentFlow now uses: {resolve_workspace().describe()}")
    print(f"The old copy was renamed to: {retired}")
    print("Start FluentFlow, confirm your records are there, then delete that folder.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
