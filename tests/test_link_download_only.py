"""Download-only link tasks (2026-11-20).

Requirements, written before the code:

1. A link submitted with ``download_only`` is fetched and then finished: no
   transcription, cut or note runs, every step after the download reads
   skipped, the task is completed with ``result.download_only`` and
   ``result.downloaded_file`` naming the video in the link's output folder.
2. It never waits in the transcription queue behind other tasks.
3. A source with captions still gives the video: captions are a transcript,
   not the file the user asked for.
4. A finished download-only task does not stop a later full submission of the
   same link (it holds no transcript or note); a download-only submission of a
   link already processed in full is refused as a duplicate like any other.
5. Agents are told nothing is left to do (no note to write), and the MCP tool
   sends the option.
6. The page shows the task as 已下载 — tested in ``frontend/src/lib/taskState.test.js``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import backend.core.link_submission as lvs
import backend.core.video_source as vs
from backend.core import job_store, task_steps
from backend.core.local_agent_package import _local_extra_next_actions
from backend.core.local_entry_guards import CancellationGate
from backend.core.queue_options import _queue_options_from_mapping
from backend.core.storage_paths import find_source_file

LINK = "https://example.com/clip.mp4"
CLIENT = "download-only"


def _fake_download(seen: dict):
    def fake(*_a, deliver=None, video_dir=None, video_only=False, **_k):
        seen["video_only"] = video_only
        video_dir.mkdir(parents=True, exist_ok=True)
        downloaded = video_dir / "clip.mp4"
        downloaded.write_bytes(b"the clip")
        saved = vs.SavedVideoSource(
            ok=True, provider="direct", source_url=LINK, download_url=LINK, video_id="clip1",
            raw_title="试玩", display_title="试玩", title="试玩", filename="clip.mp4",
            file_path=str(downloaded), metadata_path="", size_bytes=8, downloaded_at="now",
            asset_strategy=vs.build_asset_strategy(
                media_type="video", source_url=LINK, file_path=downloaded, filename="clip.mp4",
            ),
        )
        saved.file_path = str(vs._hand_over_download(downloaded, deliver))
        return saved
    return fake


def test_a_download_only_link_ends_with_the_video_and_nothing_else(monkeypatch, tmp_path):
    task_id = "download-only-task"
    link_root = tmp_path / "links"
    monkeypatch.setenv("FLUENTFLOW_LINK_OUTPUT_DIR", str(link_root))
    seen: dict = {}
    monkeypatch.setattr(lvs, "download_video_source", _fake_download(seen))

    def must_not_run(*_a, **_k):
        raise AssertionError("nothing after the download runs for a download-only task")

    monkeypatch.setattr(lvs, "preflight_media_file", must_not_run)
    monkeypatch.setattr(lvs, "run_pipeline_then_note", must_not_run)
    monkeypatch.setattr(lvs, "summarize_transcript_source", must_not_run)
    monkeypatch.setattr(lvs, "log_event", lambda **_k: None)
    monkeypatch.setattr(lvs, "get_preference", lambda _n: None)

    async def publish(*_a, **_k):
        return None

    monkeypatch.setattr(lvs.JOB_EVENTS, "publish", publish)
    options = _queue_options_from_mapping({"download_only": "true"})
    assert options == {"download_only": "true"}, "the option is kept with the task"
    job_store.upsert_job(task_id=task_id, status="queued", client_id=CLIENT, stage="queued",
                         metadata={"video_source_url": LINK, "queue_options": options})

    async def run() -> None:
        # A task ahead of it that never finishes: a download-only task does not wait for it.
        never = asyncio.Event()
        await asyncio.wait_for(lvs._download_then_process(
            task_id=task_id, input_text=LINK, title=None, options=dict(options),
            client_id=CLIENT, gate=CancellationGate(), route="/r", previous=never, done=asyncio.Event(),
        ), timeout=10)

    asyncio.run(run())

    assert seen["video_only"] is True, "captions are never taken in place of the video"
    job = job_store.get_job(task_id, client_id=CLIENT)
    assert job["status"] == "completed" and job["stage"] == "done"
    result = job["result"]
    assert result["download_only"] is True
    downloaded = Path(result["downloaded_file"])
    assert downloaded.read_bytes() == b"the clip"
    assert downloaded.parent.parent == link_root, "in the link's output folder, as for every link"
    assert sorted(p.name for p in downloaded.parent.iterdir() if not p.name.startswith(".")) == [downloaded.name], \
        "the folder holds the video and nothing else"
    assert find_source_file(task_id) is not None
    states = task_steps.statuses(task_steps.steps_of([task_id])[task_id])
    assert states.pop(task_steps.FETCH) == task_steps.DONE
    assert set(states.values()) == {task_steps.SKIPPED}
    assert _local_extra_next_actions(job, {}) == [], "no note for the agent to write"
    row = next(j for j in job_store.list_jobs(client_id=CLIENT, include_result=False) if j["task_id"] == task_id)
    assert row["result"]["download_only"] is True, "the list row carries it, so the card says 已下载"
    job_store.delete_jobs([task_id], client_id=CLIENT)


def test_video_only_skips_captions(monkeypatch, tmp_path):
    resolved = vs.ResolvedVideo(provider="yt-dlp", source_url=LINK, download_url=LINK, video_id="cap1")
    monkeypatch.setattr(vs, "resolve_video", lambda *_a, **_k: vs.ResolvedVideo(**vars(resolved)))

    def captions(*_a, **_k):
        raise AssertionError("captions are not looked for when the video is wanted")

    monkeypatch.setattr(vs, "captions_first_provider", captions)

    def download(_resolved, file_path, *_a, **_k):
        file_path.write_bytes(b"video")
        return 5

    monkeypatch.setattr(vs, "_download_media", download)
    saved = vs.download_video_source(LINK, video_dir=tmp_path / "v", video_only=True)
    assert (saved.media_type or "video") == "video"
    assert saved.filename.endswith(".mp4")


def _finished(task_id: str, *, download_only: bool) -> None:
    options = {"download_only": "true"} if download_only else {}
    job_store.upsert_job(task_id=task_id, status="completed", client_id=CLIENT, stage="done", progress=100,
                         metadata={"video_source_url": LINK, "queue_options": options},
                         result={"download_only": True} if download_only else {"summary_markdown": "x"})


def test_a_download_only_task_does_not_block_a_full_submission():
    _finished("dup-download-only", download_only=True)
    try:
        link = vs.normalize_source_link(LINK)
        assert lvs._same_link_task(link, CLIENT) is None
        assert lvs._same_link_task(link, CLIENT, download_only=True)["task_id"] == "dup-download-only"
    finally:
        job_store.delete_jobs(["dup-download-only"], client_id=CLIENT)


def test_a_full_task_blocks_a_download_only_submission():
    _finished("dup-full", download_only=False)
    try:
        link = vs.normalize_source_link(LINK)
        assert lvs._same_link_task(link, CLIENT, download_only=True)["task_id"] == "dup-full"
    finally:
        job_store.delete_jobs(["dup-full"], client_id=CLIENT)


def test_the_mcp_tool_sends_download_only(monkeypatch):
    from scripts.fluentflow_mcp import core, submit

    sent: dict = {}

    def request(method, path, *, payload=None, **_k):
        sent.update(payload or {})
        return {"ok": True, "task_id": "t"}

    monkeypatch.setattr(core, "_agent_request", request)
    monkeypatch.setattr(core, "_watch", lambda *_a, **_k: None)
    submit.submit_video_link(LINK, download_only=True)
    assert sent["options"]["download_only"] == "true"
    sent.clear()
    submit.submit_video_link(LINK)
    assert "download_only" not in sent["options"]
