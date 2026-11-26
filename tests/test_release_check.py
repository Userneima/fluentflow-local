"""Requirements for telling the user a newer release exists.

- Newer means a higher version number; equal, older or unreadable is not.
- The answer never waits on the network: the first call starts a background
  check and says nothing is known yet; one check a day.
- A check that reached nothing is not taken as the day's answer: the next ask
  tries again after a few minutes, not 24 hours (a machine offline at startup
  otherwise hid a new version for a day, 2026-11-26).
- FLUENTFLOW_RELEASE_CHECK=0 stops it entirely.
- The answer says how this copy is updated: the install command with its own
  folder, or git pull for a checkout, so nobody installs a second copy in the
  default place.
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


def _quiet(monkeypatch):
    started = []
    monkeypatch.setattr(release_check, "_state", {"latest": None, "checked_at": 0.0, "failed": False})
    monkeypatch.setattr(release_check.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: started.append(1)})())
    monkeypatch.setattr(release_check, "get_app_version", lambda: "0.11.0")
    return started


def test_the_call_does_not_wait_and_checks_once_a_day(monkeypatch):
    started = _quiet(monkeypatch)

    first = release_check.status(now=1_000_000.0)
    assert first["current"] == "0.11.0" and first["latest"] is None and first["update_available"] is False
    release_check._state["latest"] = "0.12.0"
    second = release_check.status(now=1_000_100.0)
    assert second["update_available"] is True
    assert started == [1]
    release_check.status(now=1_000_000.0 + release_check.REFRESH_SECONDS)
    assert started == [1, 1]


def test_a_failed_check_tries_again_soon_not_a_day_later(monkeypatch):
    started = _quiet(monkeypatch)
    release_check.status(now=1_000_000.0)
    assert started == [1]
    # The background check found nothing (offline): the next ask is allowed
    # again after a few minutes, not after REFRESH_SECONDS.
    release_check._state["failed"] = True
    assert release_check.RETRY_SECONDS < release_check.REFRESH_SECONDS
    release_check.status(now=1_000_000.0 + 60)
    assert started == [1]
    release_check.status(now=1_000_000.0 + release_check.RETRY_SECONDS)
    assert started == [1, 1]


def test_a_switch_stops_the_check(monkeypatch):
    started = _quiet(monkeypatch)
    monkeypatch.setenv("FLUENTFLOW_RELEASE_CHECK", "0")
    assert release_check.enabled() is False
    answer = release_check.status(now=1_000_000.0)
    assert started == [] and answer["latest"] is None
    monkeypatch.setenv("FLUENTFLOW_RELEASE_CHECK", "1")
    assert release_check.enabled() is True


def test_the_answer_says_how_this_copy_is_updated(monkeypatch, tmp_path):
    _quiet(monkeypatch)
    monkeypatch.setattr(release_check, "PROJECT_ROOT", tmp_path / "app")
    (tmp_path / "app").mkdir()

    release = release_check.status(now=1_000_000.0)

    assert release["checkout"] == "release"
    # The command names this copy's folder: run alone, the install command would
    # install into the default place, a second copy beside this one.
    assert str(tmp_path / "app") in release["update_command"]
    assert "install" in release["update_command"]

    (tmp_path / "app" / ".git").mkdir()
    release = release_check.status(now=1_000_000.0)
    assert release["checkout"] == "git"
    assert "git pull" in release["update_command"]
    assert str(tmp_path / "app") in release["update_command"]


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


def test_a_check_that_reached_nothing_is_remembered_as_failed(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(release_check, "_fetch_latest", lambda: None)
    release_check._refresh()
    assert release_check._state["failed"] is True
    monkeypatch.setattr(release_check, "_fetch_latest", lambda: "0.12.0")
    release_check._refresh()
    assert release_check._state["failed"] is False and release_check._state["latest"] == "0.12.0"


def test_the_update_command_names_the_install_folder_not_the_code_folder(monkeypatch, tmp_path):
    """A Windows one-folder install keeps its code in <folder>\\app: giving the
    installer that folder makes it take the install for the older layout and
    skip the checks that layout needs (2026-11-27)."""
    root = tmp_path / "FluentFlow"
    app = root / "app"
    app.mkdir(parents=True)
    (root / ".fluentflow-home").write_text("FluentFlow Local lives here", encoding="ascii")
    monkeypatch.setattr(release_check, "PROJECT_ROOT", app)

    assert release_check.install_folder() == root
    command = release_check.update_command()
    assert f'"{root}"' in command and f'"{app}"' not in command

    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setattr(release_check, "PROJECT_ROOT", plain)
    assert release_check.install_folder() == plain
    assert f'"{plain}"' in release_check.update_command()
