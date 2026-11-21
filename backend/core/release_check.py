"""Whether a newer FluentFlow Local release exists, for the page to say so.

A friend updated twice by hand and never heard from the product that a new
version was out (2026-11). The latest version is read at most once a day, in
the background, from npmmirror (reachable in China) and else from GitHub's
releases/latest redirect; the call itself never waits on the network.
Nothing about the user is sent. Updating is the install command run again
(install.ps1 / install.sh update an existing install in place).
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

import httpx

from backend.core.versioning import get_app_version

REFRESH_SECONDS = 24 * 3600
TIMEOUT = httpx.Timeout(5.0, connect=4.0)
NPM_URL = "https://registry.npmmirror.com/fluentflow-local/latest"
GITHUB_URL = "https://github.com/Userneima/fluentflow-local/releases/latest"

_lock = threading.Lock()
_state: dict[str, Any] = {"latest": None, "checked_at": 0.0}


def _parse(version: str) -> tuple[int, ...] | None:
    match = re.match(r"^v?(\d+(?:\.\d+)*)$", (version or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(latest: str | None, current: str | None) -> bool:
    newer, mine = _parse(latest or ""), _parse(current or "")
    return bool(newer and mine and newer > mine)


def _fetch_latest() -> str | None:
    from backend.core import fake_model

    if fake_model.enabled():
        return None
    try:
        response = httpx.get(NPM_URL, timeout=TIMEOUT)
        if response.status_code == 200:
            version = str(response.json().get("version") or "")
            if _parse(version):
                return version
    except (httpx.HTTPError, ValueError):
        pass
    try:
        response = httpx.get(GITHUB_URL, timeout=TIMEOUT, follow_redirects=False)
        tag = response.headers.get("location", "").rsplit("/tag/", 1)[-1]
        if _parse(tag):
            return tag.lstrip("v")
    except httpx.HTTPError:
        pass
    return None


def _refresh() -> None:
    latest = _fetch_latest()
    if latest:
        with _lock:
            _state["latest"] = latest


def status(*, now: float | None = None) -> dict[str, Any]:
    """The running version, the newest one known, and whether to offer it."""
    moment = time.time() if now is None else now
    with _lock:
        stale = moment - _state["checked_at"] >= REFRESH_SECONDS
        if stale:
            _state["checked_at"] = moment
        latest = _state["latest"]
    if stale:
        threading.Thread(target=_refresh, name="release-check", daemon=True).start()
    current = get_app_version()
    return {"current": current, "latest": latest, "update_available": is_newer(latest, current)}


__all__ = ["is_newer", "status"]
