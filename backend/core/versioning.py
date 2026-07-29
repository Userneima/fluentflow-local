"""Application version metadata shared by backend routes.

The plain `VERSION` file is the single source of truth for product releases.
Git metadata is best-effort: production bundles may not include a `.git`
directory, so deployment can also provide the values via environment variables.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def get_app_version() -> str:
    return (
        (os.environ.get("FLUENTFLOW_VERSION") or "").strip()
        or _read_text(repo_root() / "VERSION")
        or "0.0.0-dev"
    )


def _git_value(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root(),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    value = (result.stdout or "").strip()
    return value or None


def _git_dirty() -> bool | None:
    env_value = (os.environ.get("FLUENTFLOW_GIT_DIRTY") or "").strip().lower()
    if env_value in {"1", "true", "yes", "dirty"}:
        return True
    if env_value in {"0", "false", "no", "clean"}:
        return False
    status = _git_value("status", "--porcelain")
    if status is None:
        return None
    return bool(status)


@lru_cache(maxsize=1)
def get_version_info() -> dict[str, Any]:
    commit = (os.environ.get("FLUENTFLOW_GIT_COMMIT") or "").strip() or _git_value("rev-parse", "HEAD")
    branch = (
        (os.environ.get("FLUENTFLOW_GIT_BRANCH") or "").strip()
        or _git_value("rev-parse", "--abbrev-ref", "HEAD")
    )
    build_time = (
        (os.environ.get("FLUENTFLOW_BUILD_TIME") or "").strip()
        or datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    return {
        "app": "FluentFlow",
        "version": get_app_version(),
        "commit": commit,
        "short_commit": commit[:7] if commit else None,
        "branch": branch,
        "dirty": _git_dirty(),
        "build_time": build_time,
    }


def version_payload(*, component: str) -> dict[str, Any]:
    return {
        **get_version_info(),
        "component": component,
    }
