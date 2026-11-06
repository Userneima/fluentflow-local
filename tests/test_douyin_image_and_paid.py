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

# Taken at import, before the autouse stand-in in conftest replaces it.
_REAL_SHORT_LINK_LOOKUP = vs.douyin_short_link_target


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


def test_a_douyin_file_whose_sound_and_picture_do_not_decode_is_called_a_paid_video(monkeypatch):
    unreadable = MediaPreflightError("media_audio_unreadable", "媒体中的音频无法读取，可能已损坏。")
    douyin = "https://www.douyin.com/video/7670928280886807834"
    monkeypatch.setattr(link_submission, "video_undecodable", lambda path: True)
    error, code = link_submission.downloaded_file_refusal(unreadable, douyin, "f.mp4")
    assert code == "douyin_paid" and "付费视频" in str(error)
    diagnosis = diagnose_error(str(error))
    assert diagnosis["code"] == "douyin_paid" and "损坏" not in diagnosis["detail"]

    # Elsewhere, or another fault, it stays what the check found.
    assert link_submission.downloaded_file_refusal(
        unreadable, "https://www.bilibili.com/video/BV1xx411c7mD", "f.mp4")[1] == "media_audio_unreadable"
    empty = MediaPreflightError("media_file_empty", "媒体文件为空")
    assert link_submission.downloaded_file_refusal(empty, douyin, "f.mp4")[1] == "media_file_empty"
    # Sound alone failing while the picture decodes: a damaged file, not a
    # paid one (Codex push review, 2026-11-06).
    monkeypatch.setattr(link_submission, "video_undecodable", lambda path: False)
    assert link_submission.downloaded_file_refusal(unreadable, douyin, "f.mp4")[1] == "media_audio_unreadable"


def test_the_picture_check_tells_an_encrypted_file_from_a_good_one(tmp_path):
    """On real files: a clean one decodes, one with its frames scrambled does
    not (the paid download's shape: the container reads, the data does not)."""
    import shutil
    import subprocess

    from backend.core.media_preflight import video_undecodable

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("no ffmpeg")
    good = tmp_path / "good.mp4"
    made = subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc=d=2:s=320x240", "-pix_fmt", "yuv420p",
                           "-c:v", "libx264", "-y", str(good)], check=False)
    if made.returncode != 0:
        pytest.skip("this ffmpeg cannot write H.264")
    assert not video_undecodable(good)
    data = bytearray(good.read_bytes())
    # Only the media data; the index (moov) stays readable, as in the paid file.
    mdat = data.find(b"mdat")
    size = int.from_bytes(data[mdat - 4:mdat], "big")
    for i in range(mdat + 8, mdat - 4 + size, 7):
        data[i] ^= 0x5A
    scrambled = tmp_path / "scrambled.mp4"
    scrambled.write_bytes(bytes(data))
    assert video_undecodable(scrambled)


def test_a_short_link_that_cannot_be_looked_up_is_not_known(monkeypatch):
    """Codex push review, 2026-11-06: a name that did not resolve (network
    down) raised out of the submission instead of letting it through."""
    def no_dns(url, **kwargs):
        raise ValueError("无法解析链接域名")

    monkeypatch.setattr(vs, "validate_public_http_url", no_dns)
    assert _REAL_SHORT_LINK_LOOKUP("https://v.douyin.com/AbCdEf/") is None
