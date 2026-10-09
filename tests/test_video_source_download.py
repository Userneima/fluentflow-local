"""A link download either lands whole or leaves nothing behind.

``download_video_source`` treats a file at the final name as "already
downloaded" and never fetches it again, so a truncated file left by a timeout,
a failed exit or a cancel was transcribed as the video on the next attempt.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import backend.core.video_source as vs


def _fake_run(*, returncode: int, write: bool, raise_timeout: bool = False):
    def run(args, *, timeout, cancellation_event=None, **_k):
        target = Path(args[args.index("-o") + 1])
        if write:
            target.write_bytes(b"half a video")
        if raise_timeout:
            raise subprocess.TimeoutExpired(args, timeout)
        return subprocess.CompletedProcess(args, returncode, "", "ERROR: HTTP Error 403")
    return run


def test_a_finished_download_lands_at_the_final_name(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "_run_process", _fake_run(returncode=0, write=True))
    final = tmp_path / "talk.mp4"

    size = vs.download_yt_dlp_media("https://www.youtube.com/watch?v=abc", final)

    assert final.read_bytes() == b"half a video" and size == len(b"half a video")
    assert list(tmp_path.iterdir()) == [final], "no scratch file survives a success"


def test_a_failed_exit_leaves_nothing_at_the_final_name(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "_run_process", _fake_run(returncode=1, write=True))
    final = tmp_path / "talk.mp4"

    with pytest.raises(RuntimeError, match="403"):
        vs.download_yt_dlp_media("https://www.youtube.com/watch?v=abc", final)

    assert list(tmp_path.iterdir()) == [], "a partial file at any name is a trap for the next attempt"


def test_a_timed_out_download_leaves_nothing_at_the_final_name(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "_run_process", _fake_run(returncode=0, write=True, raise_timeout=True))
    final = tmp_path / "talk.mp4"

    with pytest.raises(RuntimeError, match="超时"):
        vs.download_yt_dlp_media("https://www.youtube.com/watch?v=abc", final)

    assert list(tmp_path.iterdir()) == []


def test_a_cancelled_download_leaves_nothing_at_the_final_name(tmp_path, monkeypatch):
    def run(args, *, timeout, cancellation_event=None, **_k):
        Path(args[args.index("-o") + 1]).write_bytes(b"half")
        raise vs.VideoSourceCancelled("cancelled")

    monkeypatch.setattr(vs, "_run_process", run)
    final = tmp_path / "talk.mp4"

    with pytest.raises(vs.VideoSourceCancelled):
        vs.download_yt_dlp_media("https://www.youtube.com/watch?v=abc", final)

    assert list(tmp_path.iterdir()) == []


def test_a_download_too_large_is_removed_before_it_can_be_trusted(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "_run_process", _fake_run(returncode=0, write=True))
    monkeypatch.setattr(vs, "max_video_bytes", lambda: 4)
    final = tmp_path / "talk.mp4"

    with pytest.raises(RuntimeError, match="过大"):
        vs.download_yt_dlp_media("https://www.youtube.com/watch?v=abc", final)

    assert list(tmp_path.iterdir()) == []


# ── the trace says what yt-dlp said ─────────────────────────────────────────

def test_a_failed_link_records_what_the_tool_said(monkeypatch):
    """"unavailable" is a category. Without the tool's own words beside it, a
    failed link could not be diagnosed from the task record."""
    monkeypatch.setattr(
        vs, "_resolve_with_yt_dlp_attempt",
        lambda *_a, **_k: (None, "unavailable", "ERROR: [youtube] abc: Video unavailable"),
    )

    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video("https://www.youtube.com/watch?v=abc")

    failed = caught.value.resolution_trace[0]
    assert failed["provider"] == "yt-dlp" and failed["status"] == "failed"
    assert "Video unavailable" in failed["detail"]


def test_a_yt_dlp_error_is_kept_as_reason_and_detail(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("ERROR: Unsupported URL: https://example.invalid/x")

    monkeypatch.setattr(vs, "run_yt_dlp", boom)

    resolved, reason, detail = vs._resolve_with_yt_dlp_once("https://www.youtube.com/watch?v=abc")

    assert resolved is None and reason
    assert "Unsupported URL" in detail


# ── stopping the download stops everything it started ───────────────────────

_PARENT_WITH_CHILD = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print(child.pid, flush=True)\n"
    "time.sleep(60)\n"
)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie answers signal 0; it is dead once its parent (or init, after
    # reparenting) reaps it, which takes a moment.
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return "Z" not in stat.read().split(")")[-1].split()[0]
    except OSError:
        return True


def _wait_until_gone(pid: int, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="no process groups on this platform")
def test_a_timeout_stops_the_grandchild_too():
    """yt-dlp runs ffmpeg as a child of its own. Terminating yt-dlp alone left
    that ffmpeg writing into the download after the task had given up."""
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        vs._run_process([sys.executable, "-c", _PARENT_WITH_CHILD], timeout=1.0)

    grandchild = int((caught.value.output or "").strip().splitlines()[0])
    assert _wait_until_gone(grandchild), f"grandchild {grandchild} outlived the download"


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="no process groups on this platform")
def test_a_cancel_stops_the_grandchild_too():
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()

    with pytest.raises(vs.VideoSourceCancelled):
        vs._run_process([sys.executable, "-c", _PARENT_WITH_CHILD], timeout=30, cancellation_event=cancel)

    # The cancel path discards the child's output, so find the grandchild by
    # asking the system which processes still run the sleeper.
    listing = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout
    survivors = [line for line in listing.splitlines() if "time.sleep(60)" in line and "ps -eo" not in line]
    deadline = time.monotonic() + 3.0
    while survivors and time.monotonic() < deadline:
        time.sleep(0.1)
        listing = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout
        survivors = [line for line in listing.splitlines() if "time.sleep(60)" in line and "ps -eo" not in line]
    assert not survivors, survivors
