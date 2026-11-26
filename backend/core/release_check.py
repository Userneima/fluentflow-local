"""Whether a newer FluentFlow Local release exists, for the page to say so.

A friend updated twice by hand and never heard from the product that a new
version was out (2026-11). The latest version is read at most once a day, in
the background, from npmmirror (reachable in China) and else from GitHub's
releases/latest redirect; the call itself never waits on the network.
Nothing about the user is sent. Updating is the install command run again
(install.ps1 / install.sh update an existing install in place); a git checkout
is updated with ``git pull`` and the setup script instead.

What a check that could not reach either host does (2026-11-26): the attempt
time is not counted as the day's check, so the next launch tries again; a
failing network therefore no longer hides a new version for 24 hours. Repeated
attempts while offline are at most one every few minutes.

``FLUENTFLOW_RELEASE_CHECK=0`` turns the check off for a user who does not want
the product to contact anything on its own.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from backend.core.local_config import PROJECT_ROOT
from backend.core.versioning import get_app_version

REFRESH_SECONDS = 24 * 3600
# A check that failed (offline, both hosts unreachable) is tried again this soon
# rather than being taken as the day's answer.
RETRY_SECONDS = 15 * 60
TIMEOUT = httpx.Timeout(5.0, connect=4.0)
NPM_URL = "https://registry.npmmirror.com/fluentflow-local/latest"
GITHUB_URL = "https://github.com/Userneima/fluentflow-local/releases/latest"

_lock = threading.Lock()
_state: dict[str, Any] = {"latest": None, "checked_at": 0.0, "failed": False}


def _parse(version: str) -> tuple[int, ...] | None:
    match = re.match(r"^v?(\d+(?:\.\d+)*)$", (version or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(latest: str | None, current: str | None) -> bool:
    newer, mine = _parse(latest or ""), _parse(current or "")
    return bool(newer and mine and newer > mine)


def enabled() -> bool:
    """Whether the product may look for a new release on its own."""
    value = (os.environ.get("FLUENTFLOW_RELEASE_CHECK") or "").strip().lower()
    return value not in ("0", "false", "no", "off")


def is_git_checkout() -> bool:
    """A developer's checkout: updated with git, not with the install command."""
    return (Path(PROJECT_ROOT) / ".git").exists()


def update_command() -> str | None:
    """The command that updates this copy, written out with its folder.

    The install command run without a folder installs into the default place;
    on a machine where the install lives in a folder the user chose, that made
    a second copy beside the first (2026-11-26). The page and agents get the
    command with this install's folder in it.
    """
    folder = str(PROJECT_ROOT)
    if is_git_checkout():
        if os.name == "nt":
            return ('cd "{0}"; git pull; powershell -NoProfile -ExecutionPolicy Bypass '
                    '-File ".\\launchers\\windows\\setup-local.ps1"').format(folder)
        return 'cd "{0}" && git pull && bash launchers/macos/setup-local.sh'.format(folder)
    if os.name == "nt":
        return ('$env:FLUENTFLOW_INSTALL_DIR = "{0}"; powershell -ExecutionPolicy Bypass '
                '-Command "irm https://raw.githubusercontent.com/Userneima/fluentflow-local/main/install.ps1 | iex"'
                ).format(folder)
    return 'FLUENTFLOW_INSTALL_DIR="{0}" bash -c "$(curl -fsSL https://raw.githubusercontent.com/Userneima/fluentflow-local/main/install.sh)"'.format(folder)


def _fetch_latest() -> str | None:
    """The newer of npmmirror's and GitHub's answers: the mirror can lag (a
    release published while npm publishing was off), GitHub can be unreachable."""
    from backend.core import fake_model

    if fake_model.enabled():
        return None
    found: list[str] = []
    try:
        response = httpx.get(NPM_URL, timeout=TIMEOUT)
        if response.status_code == 200:
            version = str(response.json().get("version") or "")
            if _parse(version):
                found.append(version)
    except (httpx.HTTPError, ValueError):
        pass
    try:
        response = httpx.get(GITHUB_URL, timeout=TIMEOUT, follow_redirects=False)
        tag = response.headers.get("location", "").rsplit("/tag/", 1)[-1]
        if _parse(tag):
            found.append(tag.lstrip("v"))
    except httpx.HTTPError:
        pass
    return max(found, key=lambda version: _parse(version)) if found else None


def _refresh() -> None:
    latest = _fetch_latest()
    with _lock:
        if latest:
            _state["latest"] = latest
            _state["failed"] = False
        else:
            _state["failed"] = True


def status(*, now: float | None = None) -> dict[str, Any]:
    """The running version, the newest one known, whether to offer it, and how
    this copy is updated."""
    moment = time.time() if now is None else now
    checking = enabled()
    with _lock:
        latest = _state["latest"]
        wait = RETRY_SECONDS if _state["failed"] else REFRESH_SECONDS
        stale = checking and moment - _state["checked_at"] >= wait
        if stale:
            _state["checked_at"] = moment
    if stale:
        threading.Thread(target=_refresh, name="release-check", daemon=True).start()
    current = get_app_version()
    return {
        "current": current,
        "latest": latest,
        "update_available": is_newer(latest, current),
        "update_command": update_command(),
        "checkout": "git" if is_git_checkout() else "release",
    }


__all__ = ["is_newer", "status", "update_command", "is_git_checkout", "enabled"]
