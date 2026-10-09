"""What a link download leaves on disk, as the user expects it.

Requirements (2026-10-09):

1. A video fetched from a link is kept once, in its task. It used to stay in
   the video-link folder as well, for good. The download there goes only once
   the task's copy is on disk at the same size; until then it is the only copy.
2. A service killed mid-download leaves nothing behind for long: the next start
   removes the hidden scratch folders and stops the yt-dlp still writing into
   one. It never signals a process that is not that yt-dlp.
3. The task's records name no address that does not open
   (``/video-sources/files/…`` answered 404).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import backend.core.video_source as vs
import backend.routers.local_video_sources as lvs
from backend.core import job_store
from backend.core.local_entry_guards import CancellationGate
from backend.core.media_intake import adopt_source_file
from backend.core.storage_paths import _source_storage_dir, _video_source_storage_dir, find_source_file

LINK = "https://example.com/talk.mp4"


def _direct(monkeypatch, body: bytes = b"the video", *, calls: list | None = None, delay: float = 0.0):
    resolved = vs.ResolvedVideo(provider="direct", source_url=LINK, download_url=LINK, video_id="talk1")
    monkeypatch.setattr(vs, "resolve_video", lambda *_a, **_k: vs.ResolvedVideo(**vars(resolved)))

    def download(_resolved, file_path, *_a, **_k):
        if calls is not None:
            calls.append(file_path)
        time.sleep(delay)
        file_path.write_bytes(body)
        return len(body)

    monkeypatch.setattr(vs, "_download_media", download)
    monkeypatch.setattr(vs, "_reusable_download", lambda path: path.is_file())


def _media_in(folder: Path) -> list[Path]:
    return [p for p in folder.iterdir() if p.suffix in {".mp4", ".json"}]


# ── 1. kept once, in the task ────────────────────────────────────────────────

def test_a_link_video_is_kept_once_in_its_task(monkeypatch, tmp_path):
    _direct(monkeypatch)
    video_dir = tmp_path / "video_sources"

    saved = vs.download_video_source(
        LINK, video_dir=video_dir,
        deliver=lambda p: adopt_source_file("kept-once", p.suffix, p),
    )

    own = find_source_file("kept-once")
    assert own is not None and own.read_bytes() == b"the video"
    assert Path(saved.file_path) == own, "the task's record names the task's copy"
    assert _media_in(video_dir) == [], "no second copy in the video-link folder"


def test_a_copy_that_comes_out_short_keeps_the_download(monkeypatch, tmp_path):
    _direct(monkeypatch)
    video_dir = tmp_path / "video_sources"
    short = tmp_path / "short.mp4"

    def deliver(path: Path) -> Path:
        short.write_bytes(b"the vi")
        return short

    with pytest.raises(RuntimeError, match="任务目录"):
        vs.download_video_source(LINK, video_dir=video_dir, deliver=deliver)

    kept = [p for p in video_dir.iterdir() if p.suffix == ".mp4"]
    assert len(kept) == 1 and kept[0].read_bytes() == b"the video", "the only whole copy stays"
    assert not short.exists()


def test_two_tasks_for_one_video_each_end_with_their_own_copy(monkeypatch, tmp_path):
    """Two links to one video (a short and a long form) submitted together:
    the second must not find the first one's download gone and fail."""
    calls: list = []
    _direct(monkeypatch, calls=calls, delay=0.2)
    video_dir = tmp_path / "video_sources"
    errors: list = []

    def run(task_id: str) -> None:
        try:
            vs.download_video_source(
                LINK, video_dir=video_dir, deliver=lambda p: adopt_source_file(task_id, p.suffix, p),
            )
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(f"same-video-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    for i in range(2):
        assert find_source_file(f"same-video-{i}").read_bytes() == b"the video"
    assert _media_in(video_dir) == []


def test_the_link_task_records_its_own_copy_and_no_dead_address(monkeypatch):
    """Through the link entry: the task gets the downloaded video in its own
    folder, the video-link folder keeps nothing, and the download event names
    the task's copy instead of a /video-sources/files/ address that 404s."""
    task_id = "link-entry-own-copy"
    events: list[dict] = []
    seen: dict = {}

    def fake_download(*_a, deliver=None, video_dir=None, **_k):
        video_dir.mkdir(parents=True, exist_ok=True)
        downloaded = video_dir / "talk.mp4"
        downloaded.write_bytes(b"linked video")
        saved = vs.SavedVideoSource(
            ok=True, provider="direct", source_url=LINK, download_url=LINK, video_id="talk1",
            raw_title="讲座", display_title="讲座", title="讲座", filename="talk.mp4",
            file_path=str(downloaded), metadata_path="", size_bytes=12, downloaded_at="now",
            asset_strategy=vs.build_asset_strategy(
                media_type="video", source_url=LINK, file_path=downloaded, filename="talk.mp4",
            ),
        )
        delivered = vs._hand_over_download(downloaded, deliver)
        saved.file_path = str(delivered)
        return saved

    def stop_after_queueing(path):
        seen["source"] = Path(path)
        raise lvs.MediaPreflightError("media_container_unreadable", "stop here")

    monkeypatch.setattr(lvs, "download_video_source", fake_download)
    monkeypatch.setattr(lvs, "preflight_media_file", stop_after_queueing)
    monkeypatch.setattr(lvs, "log_event", lambda **k: events.append(k))
    monkeypatch.setattr(lvs, "get_preference", lambda _n: None)

    async def publish(*_a, **_k):
        return None

    monkeypatch.setattr(lvs.JOB_EVENTS, "publish", publish)
    job_store.upsert_job(task_id=task_id, status="queued", client_id="c", stage="queued")

    asyncio.run(lvs._download_then_process(
        task_id=task_id, input_text=LINK, title=None, options={}, allow_miuistore=False,
        client_id="c", gate=CancellationGate(), route="/r", previous=None, done=asyncio.Event(),
    ))

    own = find_source_file(task_id)
    assert own is not None and own.read_bytes() == b"linked video"
    assert seen["source"] == own, "the pipeline reads the task's copy"
    assert not (_video_source_storage_dir() / "talk.mp4").exists()
    downloaded_event = next(e for e in events if e["event_name"] == "video_source_downloaded")
    recorded = json.dumps(downloaded_event["metadata"], ensure_ascii=False)
    assert "/video-sources/files/" not in recorded
    assert "file_url" not in recorded
    assert downloaded_event["metadata"]["video_source"]["file_path"] == str(own)
    job_store.delete_jobs([task_id], client_id="c")


# ── 1b. tasks from before: the second copy goes once the task's copy is whole ─

CLIENT = "link-kept-twice"


def _old_link_task(task_id: str, *, download_bytes: bytes, own_bytes: bytes) -> dict:
    own_dir = _source_storage_dir() / task_id
    own_dir.mkdir(parents=True, exist_ok=True)
    own = own_dir / "source.mp4"
    own.write_bytes(own_bytes)
    video_dir = _video_source_storage_dir()
    video_dir.mkdir(parents=True, exist_ok=True)
    download = video_dir / f"{task_id}.mp4"
    download.write_bytes(download_bytes)
    details = video_dir / f"{task_id}.source.json"
    details.write_text("{}", encoding="utf-8")
    expires = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(timespec="seconds")
    job_store.upsert_job(
        task_id=task_id, status="completed", client_id=CLIENT, stage="done", progress=100,
        metadata={"route": "/video-sources/jobs",
                  "video_source": {"file_path": str(download), "metadata_path": str(details)}},
        result={"task_id": task_id, "source_file_available": True,
                "source_retention_expires_at": expires},
    )
    return {"own": own, "download": download, "details": details}


@pytest.fixture()
def _clean_client():
    yield
    job_store.delete_jobs([j["task_id"] for j in job_store.list_jobs(client_id=CLIENT, limit=100)], client_id=CLIENT)


def test_an_older_link_task_stops_keeping_its_video_twice(_clean_client):
    from backend.routers import local_processing as lp

    task = _old_link_task("kept-twice", download_bytes=b"same video", own_bytes=b"same video")

    lp._enforce_local_history_retention(CLIENT)

    assert not task["download"].exists() and not task["details"].exists()
    assert task["own"].read_bytes() == b"same video", "the task's copy stays"


def test_an_older_download_that_differs_from_the_tasks_copy_is_kept(_clean_client):
    from backend.routers import local_processing as lp

    task = _old_link_task("kept-twice-differs", download_bytes=b"the whole video", own_bytes=b"cut off")

    lp._enforce_local_history_retention(CLIENT)

    assert task["download"].is_file(), "it may be the only whole copy"


# ── 2. what a killed service leaves behind ──────────────────────────────────

def _age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


def _scratch(video_dir: Path, name: str = "talk.mp4") -> Path:
    scratch = video_dir / f".{name}.0123456789ab{vs.PARTIAL_DOWNLOAD_SUFFIX}"
    scratch.mkdir(parents=True)
    (scratch / "media.f137.mp4").write_bytes(b"half")
    return scratch


def _sleeper(*marks: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", *marks],
        start_new_session=True,
    )


def _gone(proc: subprocess.Popen, seconds: float = 5.0) -> bool:
    try:
        proc.wait(timeout=seconds)
        return True
    except subprocess.TimeoutExpired:
        return False


def test_old_scratch_folders_and_files_are_removed_and_finished_videos_kept(tmp_path):
    video_dir = tmp_path / "video_sources"
    old = _scratch(video_dir, "a.mp4")
    _age(old, 3600)
    loose = video_dir / ".b.mp4.0123456789ab.download"
    loose.write_bytes(b"half")
    _age(loose, 3600)
    recent = _scratch(video_dir, "c.mp4")
    finished = video_dir / "d.mp4"
    finished.write_bytes(b"whole")
    _age(finished, 3600)

    outcome = vs.clean_interrupted_downloads(video_dir)

    assert not old.exists() and not loose.exists()
    assert recent.is_dir(), "a download a minute old may still be running"
    assert finished.is_file()
    assert outcome == {"stopped": 0, "removed": 2}


@pytest.mark.skipif(os.name != "posix", reason="process command lines are read with ps")
def test_a_yt_dlp_still_writing_into_an_old_scratch_folder_is_stopped(tmp_path):
    video_dir = tmp_path / "video_sources"
    scratch = _scratch(video_dir)
    proc = _sleeper("yt_dlp", "-o", str(scratch / "media.%(ext)s"))
    try:
        (scratch / vs.DOWNLOAD_PID_FILE).write_text(str(proc.pid))
        _age(scratch, 3600)

        outcome = vs.clean_interrupted_downloads(video_dir)

        assert _gone(proc), "the orphaned download is stopped"
        assert not scratch.exists()
        assert outcome == {"stopped": 1, "removed": 1}
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.skipif(os.name != "posix", reason="process command lines are read with ps")
def test_a_pid_that_is_not_that_yt_dlp_is_never_signalled(tmp_path):
    """The pid in the file may have been reused by anything since."""
    video_dir = tmp_path / "video_sources"
    scratch = _scratch(video_dir)
    unrelated = _sleeper("some-other-program")
    other_download = _sleeper("yt_dlp", "-o", str(tmp_path / "elsewhere" / "media.%(ext)s"))
    try:
        for proc in (unrelated, other_download):
            (scratch / vs.DOWNLOAD_PID_FILE).write_text(str(proc.pid))
            _age(scratch, 3600)
            vs.clean_interrupted_downloads(video_dir)
            assert proc.poll() is None, "still running"
            scratch = _scratch(video_dir)
    finally:
        for proc in (unrelated, other_download):
            proc.kill()
            proc.wait()


@pytest.mark.skipif(sys.platform != "darwin", reason="relies on orphans being adopted by launchd (pid 1)")
def test_a_fresh_scratch_folder_whose_writer_lost_its_service_is_cleaned_at_once(tmp_path):
    """A restart within seconds of the kill must not leave the download
    running until the next restart."""
    video_dir = tmp_path / "video_sources"
    scratch = _scratch(video_dir)
    started = subprocess.run(
        ["sh", "-c", f'"{sys.executable}" -c "import time; time.sleep(60)" yt_dlp "{scratch}" '
                     ">/dev/null 2>&1 & echo $!"],
        capture_output=True, text=True, check=True,
    )
    pid = int(started.stdout.strip())
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and (vs._read_process(pid) or (0,))[0] != 1:
            time.sleep(0.05)
        if (vs._read_process(pid) or (0,))[0] != 1:
            pytest.skip("the orphan was not adopted by pid 1 here")
        (scratch / vs.DOWNLOAD_PID_FILE).write_text(str(pid))

        outcome = vs.clean_interrupted_downloads(video_dir)

        assert outcome["stopped"] == 1 and not scratch.exists()
        assert vs._orphan_download_process(scratch) is None
    finally:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


@pytest.mark.skipif(os.name != "posix", reason="process command lines are read with ps")
def test_a_running_download_records_the_pid_of_its_yt_dlp(tmp_path):
    """The record the next start reads: the pid of the yt-dlp writing into the
    scratch folder, there while it runs."""
    seen: dict = {}
    pid_file = tmp_path / vs.DOWNLOAD_PID_FILE

    def line(_text: str) -> None:
        seen.setdefault("pid", pid_file.read_text())

    result = vs._run_process(
        [sys.executable, "-c", "print('progress')"], timeout=10, on_output_line=line, pid_file=pid_file,
    )

    assert result.returncode == 0
    assert seen["pid"].isdigit()


def test_startup_removes_what_a_killed_service_left(monkeypatch):
    from fastapi.testclient import TestClient

    import backend.local_main as local_main

    async def no_resume(_task_ids):
        return 0

    monkeypatch.setattr(local_main, "resume_interrupted_jobs", no_resume)
    video_dir = _video_source_storage_dir()
    scratch = _scratch(video_dir, "startup.mp4")
    _age(scratch, 3600)

    with TestClient(local_main.create_local_app()):
        pass

    assert not scratch.exists()
