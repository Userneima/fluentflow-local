"""Which model each text provider uses when the user has not picked one.

Requirement (owner, 2026-10-16): users do not change models themselves, so a
default fixed in the code left everyone on an old name, or a retired one
(deepseek-reasoner went unnoticed for months), until they installed a new
release. The defaults live in one small table, ``recommended_models.json``;
FluentFlow reads the copy on GitHub at most once a day and keeps the last good
one, so changing that file reaches every user without a release. Offline, or
when the file there is unreadable, the copy shipped with this build is used.
Only model names travel; nothing about the user is sent.

The choice in the table is the cheapest model good enough for notes (owner:
writing a note does not need the strongest model), by the provider's own name
that follows its newest model where it has one (``deepseek-flash``,
``qwen-plus``).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from backend.core.runtime_paths import runtime_path

logger = logging.getLogger(__name__)

BUNDLED = Path(__file__).with_name("recommended_models.json")
URL_ENV = "FLUENTFLOW_MODEL_TABLE_URL"
DEFAULT_URL = "https://raw.githubusercontent.com/Userneima/fluentflow-local/main/backend/core/recommended_models.json"
REFRESH_SECONDS = 24 * 3600
FETCH_TIMEOUT = httpx.Timeout(4.0, connect=3.0)

_lock = threading.Lock()
_state: dict[str, Any] = {"table": None, "checked_at": 0.0}


def _cache_file() -> Path:
    return runtime_path("FLUENTFLOW_MODEL_TABLE_CACHE", "recommended_models.json")


def _valid(table: Any) -> dict[str, dict[str, str]] | None:
    providers = table.get("providers") if isinstance(table, dict) else None
    if not isinstance(providers, dict):
        return None
    clean: dict[str, dict[str, str]] = {}
    for provider, roles in providers.items():
        if not isinstance(roles, dict):
            continue
        named = {role: str(name).strip() for role, name in roles.items() if isinstance(name, str) and name.strip()}
        if named:
            clean[str(provider)] = named
    return clean or None


def _read(path: Path) -> dict[str, dict[str, str]] | None:
    try:
        return _valid(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _fetch() -> dict[str, dict[str, str]] | None:
    from backend.core import fake_model

    url = os.environ.get(URL_ENV, DEFAULT_URL).strip()
    if not url or fake_model.enabled():
        # The fake model sends no request, this free one included.
        return None
    try:
        response = httpx.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True)
        if response.status_code != 200:
            return None
        return _valid(response.json())
    except (httpx.HTTPError, ValueError):
        return None


def _refresh() -> None:
    fetched = _fetch()
    if fetched is None:
        return
    try:
        cache = _cache_file()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"providers": fetched}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        logger.info("could not keep the model table", exc_info=True)
    with _lock:
        _state["table"] = fetched


def table(*, now: float | None = None) -> dict[str, dict[str, str]]:
    """The table in effect. A day after the last check, the copy on GitHub is
    fetched in the background; the call itself never waits on the network."""
    moment = time.time() if now is None else now
    with _lock:
        current = _state["table"]
        stale = moment - _state["checked_at"] >= REFRESH_SECONDS
        if stale:
            _state["checked_at"] = moment
    if current is None:
        current = _read(_cache_file()) or _read(BUNDLED) or {}
        with _lock:
            _state["table"] = _state["table"] or current
    if stale:
        threading.Thread(target=_refresh, name="model-table", daemon=True).start()
    return current


def default_model(provider: str, role: str = "text") -> str:
    """The default for ``provider``'s ``role`` ("text" or "vision"); empty when
    neither the table nor the bundled copy names one."""
    for source in (table(), _read(BUNDLED) or {}):
        name = (source.get(provider) or {}).get(role)
        if name:
            return name
    return ""


def defaults() -> dict[str, dict[str, str]]:
    """Every provider's defaults, for the settings page to say what "default" is."""
    merged = {provider: dict(roles) for provider, roles in (_read(BUNDLED) or {}).items()}
    for provider, roles in table().items():
        merged.setdefault(provider, {}).update(roles)
    return merged


def _reset_for_tests() -> None:
    with _lock:
        _state.update({"table": None, "checked_at": 0.0})


__all__ = ["BUNDLED", "DEFAULT_URL", "URL_ENV", "default_model", "defaults", "table"]
