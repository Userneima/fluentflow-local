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
