"""The restart guard counts every task still using the service.

Requirement: restarting the service must never interrupt work. A task is
marked completed once its transcript is stored, while its cut renders and its
note is written for many minutes more; those count as running.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "launchers" / "macos" / "count_active_jobs.py"
_spec = importlib.util.spec_from_file_location("count_active_jobs", _PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def test_a_task_still_transcribing_is_busy():
    assert guard.is_busy({"status": "running"})


def test_a_completed_task_whose_note_is_on_its_way_is_busy():
    assert guard.is_busy({"status": "completed", "summary_status": "pending"})


def test_a_completed_task_whose_cut_is_rendering_is_busy():
    assert guard.is_busy({"status": "completed", "summary_status": "skipped",
                          "result": {"debreath": {"status": "running"}}})


def test_a_finished_task_is_not_busy():
    assert not guard.is_busy({"status": "completed", "summary_status": "completed",
                              "result": {"debreath": {"status": "completed"}}})
    assert not guard.is_busy({"status": "failed", "summary_status": "failed"})


def test_one_scope_not_answering_is_could_not_tell(monkeypatch):
    """The scope that did not answer may be the one with the running task, so
    an idle answer from the other is no reason to restart (second review of
    the light list, 2026-11-26)."""
    answers = {None: 0, "local-single-user": None}
    monkeypatch.setattr(guard, "count_for_scope", lambda base_url, scope: answers[scope])
    assert guard.active_job_count("http://127.0.0.1:9") == -1
    answers["local-single-user"] = 2
    assert guard.active_job_count("http://127.0.0.1:9") == 2


def test_every_check_says_could_not_tell_when_a_scope_does_not_answer():
    """The Windows helper and the installer's copy return -1 from the first
    scope that fails, as count_active_jobs.py does."""
    root = Path(__file__).resolve().parents[1]
    for text, name in (((root / "launchers" / "windows" / "fluentflow-env.ps1").read_text(encoding="utf-8-sig"),
                         "Get-FluentFlowBusyCount"),
                        ((root / "install.ps1").read_text(encoding="utf-8"), "Get-BusyTaskCount")):
        body = text[text.index(f"function {name}"):]
        body = body[:body.index("\n}\n")]
        assert "catch { return -1 }" in body
        assert "catch {}" not in body


def test_the_mac_uninstaller_takes_a_non_number_as_could_not_tell():
    root = Path(__file__).resolve().parents[1]
    text = (root / "launchers" / "macos" / "uninstall-local.sh").read_text(encoding="utf-8")
    assert '[[ "$running" =~ ^-?[0-9]+$ ]] || running=-1' in text
