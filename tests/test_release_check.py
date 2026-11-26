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

import subprocess

import pytest

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
    assert release_check.shell_literal(str(root)) in command or release_check.powershell_literal(str(root)) in command
    assert f'"{app}"' not in command and f"'{app}'" not in command

    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setattr(release_check, "PROJECT_ROOT", plain)
    assert release_check.install_folder() == plain
    command = release_check.update_command()
    assert release_check.shell_literal(str(plain)) in command or release_check.powershell_literal(str(plain)) in command


@pytest.mark.skipif(release_check.os.name == "nt", reason="the shell command is for macOS; the PowerShell one is checked below")
def test_a_folder_name_is_never_run_by_the_copied_command(monkeypatch, tmp_path):
    """The folder goes into a shell command the user copies and runs. Written in
    double quotes, a folder holding $(), a backtick or a quote would run part of
    itself (2026-11-27)."""
    folder = tmp_path / "a$(touch pwned)b'c"
    folder.mkdir()
    monkeypatch.setattr(release_check, "PROJECT_ROOT", folder)
    command = release_check.update_command()

    # The literal the command hands the installer, run by a real shell, gives the
    # folder back unchanged and runs nothing.
    literal = command.split("FLUENTFLOW_INSTALL_DIR=", 1)[1].split(" bash -c", 1)[0]
    result = subprocess.run(["bash", "-c", f"d={literal}; printf %s \"$d\""], capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(folder)
    assert not (tmp_path / "pwned").exists()
    # And no double-quoted form of the folder is left in the command.
    assert f'"{folder}"' not in command


def test_the_powershell_literal_keeps_a_quote_and_a_dollar_pair():
    """PowerShell writes a quote inside a single-quoted string as two."""
    assert release_check.powershell_literal("D:\\Fluent Flow\\a'b$(x)") == "'D:\\Fluent Flow\\a''b$(x)'"
    assert release_check.shell_literal("/a b/$(x)") == "'/a b/$(x)'"
    assert release_check.shell_literal("/a'b") == "'/a'\\''b'"


@pytest.mark.skipif(release_check.os.name != "nt", reason="the PowerShell command is for Windows")
def test_the_powershell_command_keeps_such_a_folder_literal(monkeypatch, tmp_path):
    """The same folder in the PowerShell command the page hands out: run, the
    assignment gives the folder back and runs nothing in it."""
    folder = tmp_path / "a$(touch pwned)b'c"
    folder.mkdir()
    monkeypatch.setattr(release_check, "PROJECT_ROOT", folder)
    command = release_check.update_command()

    assert command.startswith("$env:FLUENTFLOW_INSTALL_DIR = '")
    assignment = command.split(";", 1)[0]
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                             assignment + "; Write-Output $env:FLUENTFLOW_INSTALL_DIR"],
                            capture_output=True, text=True, encoding="utf-8", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(folder)
    assert not (tmp_path / "pwned").exists()
