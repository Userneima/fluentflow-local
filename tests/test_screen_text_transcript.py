"""A video in which no one speaks gets its note from the on-screen text
(owner, 2026-11-19; evaluation in docs/方案/2026-11-19-没有人声的视频只看画面写笔记.md).
In the user's terms:

- A video with sound but no speech (music, sound effects) is not a failure:
  its on-screen text, read every couple of seconds, becomes the transcript,
  headed by a line saying what it is, and each line seen once.
- A nearly silent one (a muted microphone, a recorder that took no system
  sound: the case decision 4.19 was made for) still fails, and the page offers
  「照画面写笔记」.
- FluentFlow writing the note of a video over ten minutes does not happen
  unasked; the user's agent writing it costs nothing, so its tasks have no limit.
- Asking for it (the retry with picture_note) skips both checks, and the
  request is carried by a later retry.
- No text on screen, or an audio file: the task fails as before.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest

from backend.core import screen_text_transcript as stt
from backend.core import task_submission
from backend.core.frame_text import FrameText
from backend.core.local_error_diagnostics import (
    NO_SPEECH,
    NO_SPEECH_LONG,
    NO_SPEECH_NO_TEXT,
    NO_SPEECH_QUIET,
    diagnose_error,
    is_no_speech,
)

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                  reason="ffmpeg not installed")


def test_which_videos_get_a_picture_note_unasked():
    loud, quiet = -18.0, -44.0
    assert stt.refusal(mean_db=loud, duration_seconds=40, agent_writes=False, asked=False) is None
    assert stt.refusal(mean_db=quiet, duration_seconds=40, agent_writes=False, asked=False) == NO_SPEECH_QUIET
    assert stt.refusal(mean_db=None, duration_seconds=40, agent_writes=True, asked=False) == NO_SPEECH_QUIET
    assert stt.refusal(mean_db=loud, duration_seconds=1200, agent_writes=False, asked=False) == NO_SPEECH_LONG
    assert stt.refusal(mean_db=loud, duration_seconds=1200, agent_writes=True, asked=False) is None
    assert stt.refusal(mean_db=quiet, duration_seconds=1200, agent_writes=False, asked=True) is None


def test_every_way_of_not_writing_it_still_skips_the_later_steps_and_says_why():
    for reason, code, offered in (
        (NO_SPEECH_QUIET, "no_speech_quiet", True),
        (NO_SPEECH_LONG, "no_speech_long", True),
        (NO_SPEECH_NO_TEXT, "no_speech_no_text", False),
        (NO_SPEECH, "no_speech", False),
    ):
        assert is_no_speech(reason)
        diagnosis = diagnose_error(reason)
        assert diagnosis["code"] == code
        assert diagnosis["retryable"] is False
        assert diagnosis.get("picture_note", False) is offered


def _video(path, *, audio: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=5:duration=7",
         "-f", "lavfi", "-i", audio, "-shortest", "-c:v", "libx264", "-c:a", "aac", str(path)],
        check=True,
    )


@pytest.fixture()
def screen(monkeypatch):
    """Text recognition answering from a script, one entry per look."""
    script: list[list[str]] = []
    looks = iter(range(10_000))

    def read(_path):
        index = next(looks)
        return FrameText(lines=script[index] if index < len(script) else [])

    monkeypatch.setattr(stt.frame_text, "read_frame", read)
    monkeypatch.setattr(stt.frame_text, "available", lambda: True)
    return script


@needs_ffmpeg
def test_a_music_video_becomes_a_transcript_of_its_on_screen_text(tmp_path, screen):
    video = tmp_path / "clip.mp4"
    _video(video, audio="sine=frequency=440:duration=7")
    screen.extend([["五连招", "游戏科普 EP.08"], ["五连招", "顿帧：命中瞬间停住"], [], ["游戏科普 EP.08", "闪白"]])

    segments = stt.transcript_or_refuse(video, duration_seconds=7.0, agent_writes=False, asked=False)

    assert segments[0]["text"] == stt.HEADER
    rest = segments[1:]
    assert [segment["text"] for segment in rest] == ["五连招 游戏科普 EP.08", "顿帧：命中瞬间停住", "闪白"]
    assert [segment["start"] for segment in rest] == [0.0, 2.0, 6.0]
    assert rest[0]["end"] == 2.0 and rest[1]["end"] == 4.0 and rest[-1]["end"] >= 7.0


@needs_ffmpeg
def test_a_nearly_silent_video_still_fails_unless_asked(tmp_path, screen):
    video = tmp_path / "muted.mp4"
    _video(video, audio="anullsrc=r=44100:cl=mono")
    screen.extend([["屏幕上的字"]] * 4)

    with pytest.raises(RuntimeError, match=NO_SPEECH_QUIET):
        stt.transcript_or_refuse(video, duration_seconds=7.0, agent_writes=True, asked=False)
    segments = stt.transcript_or_refuse(video, duration_seconds=7.0, agent_writes=True, asked=True)
    assert segments[1]["text"] == "屏幕上的字"


@needs_ffmpeg
def test_no_text_on_screen_or_no_picture_fails(tmp_path, screen):
    video = tmp_path / "blank.mp4"
    _video(video, audio="sine=frequency=440:duration=7")
    with pytest.raises(RuntimeError, match=NO_SPEECH_NO_TEXT):
        stt.transcript_or_refuse(video, duration_seconds=7.0, agent_writes=True, asked=False)

    audio = tmp_path / "talk.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-c:a", "aac", str(audio)], check=True)
    with pytest.raises(RuntimeError) as raised:
        stt.transcript_or_refuse(audio, duration_seconds=3.0, agent_writes=True, asked=True)
    assert str(raised.value) == NO_SPEECH


def test_asking_for_it_is_recorded_beside_cut_and_carried_by_a_retry(monkeypatch):
    assert stt.picture_note_wanted({"metadata": {"picture_note": True}})
    assert stt.picture_note_wanted({"metadata": {"folder_intake": {"picture_note": True}}})
    assert not stt.picture_note_wanted({"metadata": {}})

    job = {"task_id": "t-pic", "status": "failed", "source_type": "video_link",
           "metadata": {"video_source_url": "https://example.com/v/1"}}
    monkeypatch.setattr(task_submission, "get_job", lambda *_a, **_k: job)
    monkeypatch.setattr(task_submission, "_in_place_origin", lambda _job: None)
    monkeypatch.setattr(task_submission, "find_source_file", lambda _task_id: None)
    seen: list[dict] = []

    async def link_retry(**kwargs):
        seen.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(task_submission, "_retry_link_task", link_retry)
    asyncio.run(task_submission.retry_task("t-pic", client_id=None, local_caller=True))
    assert "picture_note" not in (seen[-1]["carry"] or {})
    asyncio.run(task_submission.retry_task("t-pic", client_id=None, local_caller=True, picture_note=True))
    assert seen[-1]["carry"]["picture_note"] is True
    job["metadata"]["picture_note"] = True
    asyncio.run(task_submission.retry_task("t-pic", client_id=None, local_caller=True))
    assert seen[-1]["carry"]["picture_note"] is True


def test_the_mcp_retry_tool_passes_picture_note(monkeypatch):
    import scripts.fluentflow_mcp_server as mcp

    sent = []
    monkeypatch.setattr(mcp, "_agent_request", lambda *_a, **k: sent.append(k["payload"]) or {"ok": True})
    mcp.retry_task("t1", picture_note=True)
    mcp.retry_task("t1")
    assert sent == [{"picture_note": True}, None]
    assert "picture_note" in mcp._TOOLS["retry_task"][1]["inputSchema"]["properties"]


def test_the_stored_message_is_diagnosed_the_same_way_again():
    from backend.core.local_entry_guards import friendly_error

    for reason in (NO_SPEECH_QUIET, NO_SPEECH_LONG, NO_SPEECH_NO_TEXT, NO_SPEECH):
        stored = friendly_error(RuntimeError(reason))
        assert diagnose_error(stored)["code"] == diagnose_error(reason)["code"]
        assert reason in stored


@needs_ffmpeg
def test_a_caption_that_changes_only_its_numbers_is_kept_and_a_misread_repeat_is_not(tmp_path, screen):
    video = tmp_path / "price.mp4"
    _video(video, audio="sine=frequency=440:duration=7")
    screen.extend([["本次价格为100元", "游戏科普系列 EP.08"], ["本次价格为200元", "游戏科音系列 EP.08"],
                   ["本次价格为100元"]])

    segments = stt.transcript_or_refuse(video, duration_seconds=7.0, agent_writes=True, asked=False)

    assert [segment["text"] for segment in segments[1:]] == [
        "本次价格为100元 游戏科普系列 EP.08", "本次价格为200元",
    ]


@needs_ffmpeg
def test_a_digitally_silent_video_fails_as_nearly_silent_so_the_picture_note_is_offered(tmp_path):
    video = tmp_path / "silent.mp4"
    _video(video, audio="anullsrc=r=44100:cl=mono")

    def rejects():
        raise RuntimeError(NO_SPEECH)

    with pytest.raises(RuntimeError) as raised:
        stt.guard_silence(video, rejects)
    assert str(raised.value) == NO_SPEECH_QUIET

    audio = tmp_path / "silent.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-t", "2", "-c:a", "aac", str(audio)], check=True)
    with pytest.raises(RuntimeError) as raised:
        stt.guard_silence(audio, rejects)
    assert str(raised.value) == NO_SPEECH
