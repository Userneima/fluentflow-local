"""Douyin image posts and paid videos are named for what they are, early.

Requirement (owner, 2026-11-06, after clearing his Douyin favourites on
10-28): an image post (图文, a link with /note/) is refused when it is
submitted, with that reason, not after the queue and the resolver; a paid
video, which comes down as an encrypted file, is reported as a paid video
right after its download, not as "the file may be damaged".
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from backend.core import job_store, link_submission
from backend.core import video_source as vs
from backend.core.local_error_diagnostics import diagnose_error
from backend.core.media_preflight import MediaPreflightError


def test_an_image_post_is_known_from_its_link():
    assert vs.is_douyin_image_post("https://www.douyin.com/note/7560000000000000000")
    assert vs.is_douyin_image_post("看看 https://www.iesdouyin.com/share/note/7560000000000000000/ 复制此链接")
    assert not vs.is_douyin_image_post("https://www.douyin.com/video/7560000000000000000")
    assert not vs.is_douyin_image_post("https://www.bilibili.com/video/BV1xx411c7mD")


def test_a_short_link_is_looked_up_once(monkeypatch):
    asked: list[str] = []

    def target(url, timeout=5.0):
        asked.append(url)
        return "https://www.iesdouyin.com/share/note/7560000000000000000/?region=CN"

    monkeypatch.setattr(vs, "douyin_short_link_target", target)
    assert vs.is_douyin_image_post("https://v.douyin.com/AbCdEf/ 复制此链接")
    assert asked == ["https://v.douyin.com/AbCdEf/"]

    monkeypatch.setattr(vs, "douyin_short_link_target", lambda url, timeout=5.0: None)
    assert not vs.is_douyin_image_post("https://v.douyin.com/AbCdEf/"), "not known: the download step decides"


def test_submitting_an_image_post_is_refused_at_once_and_makes_no_task():
    before = len(job_store.list_jobs_by_statuses(("queued", "running")))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(link_submission.submit_video_source_job(
            input_text="https://www.douyin.com/note/7560000000000000000", title="", raw_options={}, client_id=None,
        ))
    assert caught.value.status_code == 400
    assert "图文帖" in caught.value.detail and "没有视频" in caught.value.detail
    assert len(job_store.list_jobs_by_statuses(("queued", "running"))) == before


def test_a_douyin_file_whose_sound_does_not_decode_is_called_a_paid_video():
    unreadable = MediaPreflightError("media_audio_unreadable", "媒体中的音频无法读取，可能已损坏。")
    error, code = link_submission.downloaded_file_refusal(unreadable, "https://www.douyin.com/video/7670928280886807834")
    assert code == "douyin_paid" and "付费视频" in str(error)
    diagnosis = diagnose_error(str(error))
    assert diagnosis["code"] == "douyin_paid" and "损坏" not in diagnosis["detail"]

    # Elsewhere, or another fault, it stays what the check found.
    error, code = link_submission.downloaded_file_refusal(unreadable, "https://www.bilibili.com/video/BV1xx411c7mD")
    assert code == "media_audio_unreadable"
    empty = MediaPreflightError("media_file_empty", "媒体文件为空")
    assert link_submission.downloaded_file_refusal(empty, "https://www.douyin.com/video/1")[1] == "media_file_empty"
