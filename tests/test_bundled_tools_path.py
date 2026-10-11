"""The FFmpeg the installer downloads into the data directory is found by the backend."""

import os

from backend.core import runtime_paths


def test_the_installers_bin_goes_first_on_path(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_DATA_DIR", str(tmp_path))
    (tmp_path / "bin").mkdir()
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    runtime_paths.put_bundled_tools_on_path()
    runtime_paths.put_bundled_tools_on_path()

    entries = os.environ["PATH"].split(os.pathsep)
    assert entries[0] == str(tmp_path / "bin")
    assert entries.count(str(tmp_path / "bin")) == 1, "calling twice adds it once"


def test_nothing_changes_when_the_installer_downloaded_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    runtime_paths.put_bundled_tools_on_path()

    assert os.environ["PATH"] == "/usr/bin:/bin"


def test_version_info_does_not_run_git_outside_a_checkout(monkeypatch, tmp_path):
    """A release zip has no .git; running git there pops a dialog on a fresh Mac."""
    from backend.core import versioning

    calls = []
    monkeypatch.setattr(versioning, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(versioning.subprocess, "run", lambda *a, **k: calls.append(a))

    assert versioning._git_value("rev-parse", "HEAD") is None
    assert calls == []
