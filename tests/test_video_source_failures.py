"""When a link cannot be fetched, the user is told what happened and what to do.

The tool behind the link (yt-dlp) fails in a few recurring ways: the video is
private, it is not available in this country, the platform answers 403, or the
site is one nobody supports. Whatever it said, the task card must carry a
Chinese sentence that explains the failure and suggests a way forward (another
link, logging in, or uploading the file), and the task record must keep the
tool's own words so the failure can be diagnosed afterwards.

``tests/test_video_source_download.py`` covers the download step (partial
files, timeouts, cancellation, process groups); this file covers what the
user reads when resolving the link fails.
"""

from __future__ import annotations

import subprocess

import pytest

import backend.core.video_source as vs
import backend.routers.local_video_sources as lvs
from backend.core.local_error_diagnostics import diagnose_error

YOUTUBE = "https://www.youtube.com/watch?v=abc123"
BILIBILI = "https://www.bilibili.com/video/BV1xx411c7mD"

PRIVATE = "ERROR: [youtube] abc123: Private video. Sign in if you've been granted access to this video"
GEO_BLOCKED = "ERROR: [youtube] abc123: The uploader has not made this video available in your country"
FORBIDDEN = "ERROR: unable to download video data: HTTP Error 403: Forbidden"
UNSUPPORTED = "ERROR: Unsupported URL: https://www.youtube.com/watch?v=abc123"


def _has_chinese(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


@pytest.fixture(autouse=True)
def no_browser_cookies(monkeypatch):
    monkeypatch.delenv("YT_DLP_COOKIES_FROM_BROWSER", raising=False)


def _yt_dlp_fails_with(monkeypatch, stderr: str) -> list[list[str]]:
    """yt-dlp exits non-zero with this on stderr; the commands run are kept."""
    calls: list[list[str]] = []

    def run(args, *, timeout, cancellation_event=None, **_k):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 1, "", stderr)

    monkeypatch.setattr(vs, "_run_process", run)
    return calls


def _what_the_user_reads(error: Exception) -> tuple[str, str]:
    """The card shows the friendly detail; the diagnosis adds the next step."""
    diagnosis = diagnose_error(error)
    return lvs._friendly_error(error), str(diagnosis.get("next_action") or "")


# ── the failures a link runs into ───────────────────────────────────────────

@pytest.mark.parametrize("stderr", [PRIVATE, GEO_BLOCKED, FORBIDDEN, UNSUPPORTED], ids=["private", "geo", "403", "unsupported"])
def test_a_youtube_link_that_cannot_be_fetched_is_explained_in_chinese_with_a_way_forward(monkeypatch, stderr):
    calls = _yt_dlp_fails_with(monkeypatch, stderr)

    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video(YOUTUBE)

    detail, next_action = _what_the_user_reads(caught.value)
    assert _has_chinese(detail) and "ERROR" not in detail, "the card never shows yt-dlp's own line"
    assert "链接" in detail, "it says the link is the thing that failed"
    assert "上传" in next_action or "上传" in detail, "uploading the file is always a way forward"
    assert len(calls) == 1, "a permanent refusal is not retried"
    failed = caught.value.resolution_trace[0]
    assert failed["provider"] == "yt-dlp" and failed["status"] == "failed"
    assert stderr.split("ERROR: ", 1)[1][:40] in failed["detail"], "the tool's own words are kept for diagnosis"


def test_a_private_video_is_logged_with_its_reason_in_the_trace(monkeypatch):
    _yt_dlp_fails_with(monkeypatch, PRIVATE)

    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video(YOUTUBE)

    assert "Private video" in caught.value.resolution_trace[0]["detail"]


def test_a_platform_403_tells_the_user_the_platform_refused_not_that_the_link_is_unreadable(monkeypatch):
    _yt_dlp_fails_with(monkeypatch, FORBIDDEN)

    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video(YOUTUBE)

    detail, next_action = _what_the_user_reads(caught.value)
    assert diagnose_error(caught.value)["code"] == "platform_forbidden"
    assert "拒绝" in detail
    assert "cookies" in next_action.lower() or "登录" in next_action


def test_a_platform_403_is_classified_as_forbidden_where_the_reason_is_recorded(monkeypatch):
    """The category is right in the record even though the card does not show it."""
    _yt_dlp_fails_with(monkeypatch, FORBIDDEN)

    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video(YOUTUBE)

    assert caught.value.resolution_trace[0]["reason"] == "forbidden"


def test_a_site_nobody_supports_is_named_as_such_and_never_reaches_the_tool(monkeypatch):
    calls = _yt_dlp_fails_with(monkeypatch, UNSUPPORTED)

    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video("https://vimeo.com/123456")

    detail, next_action = _what_the_user_reads(caught.value)
    assert diagnose_error(caught.value)["code"] == "unsupported_video_source"
    assert _has_chinese(detail) and "支持" in detail
    assert "上传" in next_action
    assert calls == []
    assert caught.value.resolution_trace == [
        {"provider": "source-policy", "status": "failed", "reason": "unsupported_source"}
    ]


def test_a_bilibili_link_that_is_refused_is_told_to_log_in(monkeypatch):
    """B 站 answers 403 to anonymous requests for most videos; the fix is the
    browser login the settings page offers, and the message says so."""
    _yt_dlp_fails_with(monkeypatch, FORBIDDEN)

    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video(BILIBILI)

    detail, _ = _what_the_user_reads(caught.value)
    assert _has_chinese(detail)
    assert "B 站" in detail and "登录" in detail and "上传" in detail
    assert caught.value.resolution_trace[0]["reason"] == "forbidden"


def test_text_with_no_link_in_it_is_told_so(monkeypatch):
    calls = _yt_dlp_fails_with(monkeypatch, UNSUPPORTED)

    with pytest.raises(ValueError) as caught:
        vs.resolve_video("今天的讲座很好，推荐大家看看")

    detail, next_action = _what_the_user_reads(caught.value)
    assert diagnose_error(caught.value)["code"] == "video_link_missing"
    assert _has_chinese(detail) and "链接" in detail or "URL" in detail
    assert "粘贴" in next_action
    assert calls == []


def test_the_missing_tool_itself_is_reported_as_a_setup_problem_not_a_bad_link(monkeypatch):
    _yt_dlp_fails_with(monkeypatch, "/usr/bin/python3: No module named yt_dlp")

    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video(YOUTUBE)

    assert caught.value.resolution_trace[0]["reason"] == "dependency_missing"
    diagnosis = diagnose_error(caught.value.resolution_trace[0]["detail"])
    assert diagnosis["code"] == "video_parser_config_missing" and diagnosis["retryable"] is False
