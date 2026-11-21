"""Requirements for telling the user a newer release exists.

- Newer means a higher version number; equal, older or unreadable is not.
- The answer never waits on the network: the first call starts a background
  check and says nothing is known yet; one check a day.
"""

from __future__ import annotations

from backend.core import release_check


def test_newer_means_a_higher_version():
    assert release_check.is_newer("0.12.0", "0.11.0")
    assert release_check.is_newer("v0.11.1", "0.11.0")
    assert not release_check.is_newer("0.11.0", "0.11.0")
    assert not release_check.is_newer("0.10.9", "0.11.0")
    assert not release_check.is_newer(None, "0.11.0")
    assert not release_check.is_newer("garbage", "0.11.0")


def test_the_call_does_not_wait_and_checks_once_a_day(monkeypatch):
    started = []
    monkeypatch.setattr(release_check, "_state", {"latest": None, "checked_at": 0.0})
    monkeypatch.setattr(release_check.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: started.append(1)})())
    monkeypatch.setattr(release_check, "get_app_version", lambda: "0.11.0")

    first = release_check.status(now=1_000_000.0)
    assert first == {"current": "0.11.0", "latest": None, "update_available": False}
    release_check._state["latest"] = "0.12.0"
    second = release_check.status(now=1_000_100.0)
    assert second["update_available"] is True
    assert started == [1]
    release_check.status(now=1_000_000.0 + release_check.REFRESH_SECONDS)
    assert started == [1, 1]
