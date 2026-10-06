"""A download reports every megabyte; the store and the page do not need to
hear every one of them."""

from __future__ import annotations

from backend.core.video_source import VideoSourceProgress
from backend.routers import local_video_sources as lvs


def _report(percent, stage="downloading", loaded=None, total=None):
    return VideoSourceProgress(stage=stage, message=f"{percent}%", percent=percent, loaded_bytes=loaded, total_bytes=total)


def test_reports_within_the_same_moment_go_out_every_five_percent(monkeypatch):
    monkeypatch.setattr(lvs.time, "monotonic", lambda: 100.0)
    should_publish = lvs._progress_throttle()

    published = [p for p in range(0, 21) if should_publish(_report(p))]

    assert published == [0, 5, 10, 15, 20]


def test_a_report_two_seconds_after_the_last_goes_out_whatever_the_percent(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(lvs.time, "monotonic", lambda: clock["now"])
    should_publish = lvs._progress_throttle()

    assert should_publish(_report(10)) is True
    clock["now"] = 101.0
    assert should_publish(_report(11)) is False
    clock["now"] = 102.0
    assert should_publish(_report(11)) is True, "a slow download still shows it is alive every two seconds"


def test_the_final_report_always_goes_out(monkeypatch):
    monkeypatch.setattr(lvs.time, "monotonic", lambda: 100.0)
    should_publish = lvs._progress_throttle()

    assert should_publish(_report(97)) is True
    assert should_publish(_report(98)) is False
    assert should_publish(_report(100)) is True
    assert should_publish(_report(99, loaded=500, total=500)) is True, "all bytes in is final even without the number"


def test_a_change_of_stage_always_goes_out(monkeypatch):
    monkeypatch.setattr(lvs.time, "monotonic", lambda: 100.0)
    should_publish = lvs._progress_throttle()

    assert should_publish(_report(None, stage="resolving")) is True
    assert should_publish(_report(1, stage="downloading")) is True
    assert should_publish(_report(2, stage="downloading")) is False
    assert should_publish(_report(None, stage="saving")) is True
