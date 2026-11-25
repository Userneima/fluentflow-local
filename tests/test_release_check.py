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


def test_the_newer_of_the_mirror_and_github_wins(monkeypatch):
    class Answer:
        def __init__(self, status=200, body=None, headers=None):
            self.status_code, self._body, self.headers = status, body or {}, headers or {}

        def json(self):
            return self._body

    def get(url, **kwargs):
        if "npmmirror" in url:
            return Answer(body={"version": "0.11.0"})
        return Answer(302, headers={"location": "https://github.com/x/y/releases/tag/v0.12.0"})

    monkeypatch.setattr(release_check.httpx, "get", get)
    assert release_check._fetch_latest() == "0.12.0"
