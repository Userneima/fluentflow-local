"""Requirements for scripts/update_yt_dlp.py, run by the launchers on every start.

- Checked successfully today: pip is not run at all.
- Never checked, or last checked on an earlier day: pip upgrades yt-dlp and
  today's date is recorded.
- --force upgrades even when already checked today.
- pip failing or hanging past the timeout: no exception, a readable line, the
  date is NOT recorded so the next launch tries again.
- The pip command upgrades yt-dlp in the running interpreter's environment.
"""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import update_yt_dlp as upd

TODAY = dt.date(2026, 10, 8)


@pytest.fixture
def pip_calls(monkeypatch):
    calls: list[dict] = []
    outcome = {"result": SimpleNamespace(returncode=0, stdout="", stderr="")}

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        value = outcome["result"]
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(upd.subprocess, "run", fake_run)
    monkeypatch.setattr(upd, "installed_version", lambda: "2026.8.19")
    return calls, outcome


def test_skips_when_already_checked_today(tmp_path, pip_calls):
    calls, _ = pip_calls
    stamp = tmp_path / "yt_dlp_last_update"
    stamp.write_text(TODAY.isoformat(), encoding="utf-8")

    line = upd.update(today=TODAY, stamp=stamp)

    assert calls == []
    assert "跳过" in line


@pytest.mark.parametrize("previous", [None, "2026-10-07", "garbage"])
def test_runs_pip_and_records_today_when_stale(tmp_path, pip_calls, previous):
    calls, _ = pip_calls
    stamp = tmp_path / "nested" / "yt_dlp_last_update"
    if previous is not None:
        stamp.parent.mkdir(parents=True)
        stamp.write_text(previous, encoding="utf-8")

    upd.update(today=TODAY, stamp=stamp)

    assert len(calls) == 1
    cmd = calls[0]["cmd"]
    assert cmd[:4] == [sys.executable, "-m", "pip", "install"]
    assert "-U" in cmd and cmd[-1] == "yt-dlp"
    assert calls[0]["timeout"] == upd.PIP_TIMEOUT_SECONDS
    assert stamp.read_text(encoding="utf-8") == TODAY.isoformat()


def test_force_runs_even_when_checked_today(tmp_path, pip_calls):
    calls, _ = pip_calls
    stamp = tmp_path / "yt_dlp_last_update"
    stamp.write_text(TODAY.isoformat(), encoding="utf-8")

    upd.update(force=True, today=TODAY, stamp=stamp)

    assert len(calls) == 1


def test_reports_version_change(tmp_path, pip_calls, monkeypatch):
    versions = iter(["2026.8.19", "2026.10.1"])
    monkeypatch.setattr(upd, "installed_version", lambda: next(versions))

    line = upd.update(today=TODAY, stamp=tmp_path / "s")

    assert "2026.8.19" in line and "2026.10.1" in line


def test_pip_failure_does_not_raise_or_record(tmp_path, pip_calls):
    _, outcome = pip_calls
    outcome["result"] = SimpleNamespace(
        returncode=1, stdout="", stderr="ERROR: Could not find a version that satisfies yt-dlp\n"
    )
    stamp = tmp_path / "yt_dlp_last_update"

    line = upd.update(today=TODAY, stamp=stamp)

    assert "更新失败" in line and "2026.8.19" in line
    assert "Could not find a version" in line
    assert not stamp.exists()


def test_pip_timeout_does_not_raise_or_record(tmp_path, pip_calls):
    _, outcome = pip_calls
    outcome["result"] = subprocess.TimeoutExpired(cmd="pip", timeout=upd.PIP_TIMEOUT_SECONDS)
    stamp = tmp_path / "yt_dlp_last_update"

    line = upd.update(today=TODAY, stamp=stamp)

    assert "更新失败" in line
    assert not stamp.exists()


def test_unexpected_error_is_swallowed(tmp_path, pip_calls):
    _, outcome = pip_calls
    outcome["result"] = RuntimeError("boom")

    line = upd.update(today=TODAY, stamp=tmp_path / "s")

    assert "boom" in line


def test_main_always_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(upd, "update", lambda force=False: "yt-dlp：测试")
    assert upd.main([]) == 0
    assert upd.main(["--unknown-flag"]) == 0
    assert "yt-dlp：测试" in capsys.readouterr().out


def test_stamp_lives_in_fluentflow_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_DATA_DIR", str(tmp_path))
    assert upd.stamp_path() == tmp_path / upd.STAMP_NAME
