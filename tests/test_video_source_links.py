"""Getting a video from a pasted link, as the user expects it to work.

Each test states the requirement it checks (found in live tests on
2026-10-08). Only the edges are faked: the yt-dlp child process, HTTP
responses, DNS, ffprobe and the third-party resolver.
"""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest

import backend.core.video_source as vs
import backend.routers.local_video_sources as lvs
from backend.core.local_error_diagnostics import diagnose_error

BILIBILI = "https://www.bilibili.com/video/BV1N2pc6gErK"
YOUTUBE = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
DOUYIN = "https://www.douyin.com/video/7693194858911108367"
COOKIE_DB_ERROR = 'ERROR: could not find chrome cookies database in "/Users/x/Library/Application Support/Google/Chrome"'


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.delenv("YT_DLP_COOKIES_FROM_BROWSER", raising=False)
    monkeypatch.delenv("YT_DLP_COOKIES_FILE", raising=False)
    monkeypatch.delenv("VIDEO_SOURCE_MAX_BYTES", raising=False)
    monkeypatch.setattr(vs, "browser_cookie_spec", lambda browser, _url: browser or None)
    monkeypatch.setattr(vs, "_pause_before_retry", lambda *_a, **_k: None)


def _info(url: str, **extra) -> str:
    info = {
        "id": "abc", "title": "演讲", "webpage_url": url, "duration": 120,
        "formats": [{"url": "https://media.example.com/v.mp4", "vcodec": "h264", "acodec": "aac", "ext": "mp4"}],
    }
    info.update(extra)
    return json.dumps(info)


class FakeYtDlp:
    """A yt-dlp stand-in: refuses any call carrying browser cookies when the
    store is unreadable, writes a file for downloads, prints JSON for dumps."""

    def __init__(self, *, cookies_unreadable=False, download_bytes=b"video", stdout_lines=(), fail_with=None):
        self.calls: list[list[str]] = []
        self.cookies_unreadable = cookies_unreadable
        self.download_bytes = download_bytes
        self.stdout_lines = stdout_lines
        self.fail_with = fail_with

    def __call__(self, args, *, timeout, cancellation_event=None, on_output_line=None, **_k):
        self.calls.append(list(args))
        if self.cookies_unreadable and "--cookies-from-browser" in args:
            return subprocess.CompletedProcess(args, 1, "", COOKIE_DB_ERROR)
        if self.fail_with:
            return subprocess.CompletedProcess(args, 1, "", self.fail_with)
        url = args[-1]
        if "--dump-single-json" in args:
            return subprocess.CompletedProcess(args, 0, _info(url), "")
        if "--write-subs" in args:
            template = Path(args[args.index("-o") + 1])
            (template.parent / "captions.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
            return subprocess.CompletedProcess(args, 0, "", "")
        for line in self.stdout_lines:
            if on_output_line:
                on_output_line(line)
        if self.download_bytes:
            template = Path(args[args.index("-o") + 1])
            (template.parent / "media.mp4").write_bytes(self.download_bytes)
        return subprocess.CompletedProcess(args, 0, "\n".join(self.stdout_lines), "")


# ── 1. an unreadable browser login never breaks a platform ──────────────────

@pytest.mark.parametrize("url", [BILIBILI, YOUTUBE])
def test_an_unreadable_browser_login_is_skipped_and_the_link_still_resolves(monkeypatch, url):
    """Requirement: with a browser login configured that macOS will not let us
    read, Bilibili and YouTube links still work, and the trace says the login
    was unreadable and the step ran without it."""
    fake = FakeYtDlp(cookies_unreadable=True)
    monkeypatch.setattr(vs, "_run_process", fake)

    resolved = vs.resolve_video(url, cookies_from_browser="chrome")

    assert resolved.provider == "yt-dlp"
    assert resolved.cookies_unreadable is True
    assert resolved.resolution_trace[0]["status"] == "unreadable"
    assert "--cookies-from-browser" in fake.calls[0] and "--cookies-from-browser" not in fake.calls[1]


def test_an_unreadable_browser_login_does_not_stop_the_media_download(monkeypatch, tmp_path):
    """Requirement: the download step also falls back to no login."""
    fake = FakeYtDlp(cookies_unreadable=True)
    monkeypatch.setattr(vs, "_run_process", fake)
    use = vs.CookieUse()

    size = vs.download_yt_dlp_media(BILIBILI, tmp_path / "v.mp4", cookies_from_browser="chrome", cookie_use=use)

    assert size == len(b"video") and (tmp_path / "v.mp4").is_file()
    assert use.unreadable and use.notes


def test_an_unreadable_browser_login_does_not_stop_captions(monkeypatch, tmp_path):
    fake = FakeYtDlp(cookies_unreadable=True)
    monkeypatch.setattr(vs, "_run_process", fake)

    vs.download_source_captions(YOUTUBE, tmp_path / "c.srt", cookies_from_browser="chrome")

    assert (tmp_path / "c.srt").read_text().startswith("1")


# ── 2. Bilibili and YouTube media come through yt-dlp, capped at 720p ──────

@pytest.mark.parametrize("url", [BILIBILI, YOUTUBE])
def test_bilibili_and_youtube_media_are_downloaded_by_yt_dlp_at_720p(monkeypatch, tmp_path, url):
    """Requirement: the media is fetched by yt-dlp with a transcription-sized
    format and merged by it, never by our own downloader on PCDN addresses."""
    fake = FakeYtDlp()
    monkeypatch.setattr(vs, "_run_process", fake)
    monkeypatch.setattr(vs, "captions_first_provider", lambda *_a, **_k: None)
    monkeypatch.setattr(vs, "_reusable_download", lambda path: False)
    monkeypatch.setattr(vs, "download_file", lambda *_a, **_k: pytest.fail("own downloader used"))

    saved = vs.download_video_source(url, video_dir=tmp_path)

    download_call = fake.calls[-1]
    assert download_call[download_call.index("-f") + 1] == vs.CAPPED_FORMAT
    assert "--merge-output-format" in download_call
    assert Path(saved.file_path).read_bytes() == b"video"


def test_progress_text_never_names_bilibili_for_youtube_nor_sends_a_douyin_referer():
    assert vs.download_referer_for_url("https://rr5---sn-a5.googlevideo.com/videoplayback") is None
    assert vs.download_referer_for_url("https://example.com/talk.mp4") is None
    assert vs.download_referer_for_url("https://v5.douyinvod.com/x") == "https://www.douyin.com/"
    assert not hasattr(vs, "merge_media_parts")


# ── 3. Douyin's CDN may hand the file to another public host ────────────────

def _dns(monkeypatch, address):
    monkeypatch.setattr(vs.socket, "getaddrinfo", lambda *_a, **_k: [(None, None, None, None, (address, 0))])


def test_a_douyin_media_redirect_to_a_partner_cdn_is_followed(monkeypatch):
    """Requirement: a download that starts on a Douyin media host follows a
    redirect to any public host (seen: *.pkoplink.com)."""
    _dns(monkeypatch, "8.8.8.8")
    handler = vs.PublicHTTPRedirectHandler(vs.DOUYIN_MEDIA_HOST_SUFFIXES, any_public_host=True)
    req = urllib.request.Request("https://v5-se.douyinvod.com/x")

    new = handler.redirect_request(req, None, 302, "Found", {}, "https://1rjsj6o7ry5oc.v1d.pkoplink.com/a/video")

    assert new is not None and "pkoplink.com" in new.full_url


def test_a_douyin_media_redirect_still_refuses_private_addresses_and_odd_ports(monkeypatch):
    _dns(monkeypatch, "10.0.0.5")
    handler = vs.PublicHTTPRedirectHandler(vs.DOUYIN_MEDIA_HOST_SUFFIXES, any_public_host=True)
    req = urllib.request.Request("https://v5-se.douyinvod.com/x")
    with pytest.raises(ValueError):
        handler.redirect_request(req, None, 302, "Found", {}, "https://cdn.pkoplink.com/a")
    _dns(monkeypatch, "8.8.8.8")
    with pytest.raises(ValueError):
        handler.redirect_request(req, None, 302, "Found", {}, "https://cdn.pkoplink.com:8443/a")


def test_a_douyin_download_asks_for_the_open_redirect_policy(monkeypatch, tmp_path):
    seen = {}

    class Response(io.BytesIO):
        headers = {"content-length": "5"}

    def fake_open(request, **kwargs):
        seen.update(kwargs)
        return Response(b"video")

    monkeypatch.setattr(vs, "open_public_http_url", fake_open)
    vs.download_file("https://v5.douyinvod.com/x", tmp_path / "v.mp4")
    assert seen["redirect_to_any_public_host"] is True


# ── 4. a Bilibili preview is not a success ──────────────────────────────────

def test_a_bilibili_members_only_video_that_yields_a_preview_fails_clearly(monkeypatch, tmp_path):
    """Requirement: when only a few minutes of a paid video come down, the task
    fails with 会员/付费内容，只能拿到试看片段, and nothing is kept."""
    monkeypatch.setattr(vs, "_run_process", FakeYtDlp())
    monkeypatch.setattr(vs, "_probe_duration_seconds", lambda _p: 180.0)

    with pytest.raises(RuntimeError) as caught:
        vs.download_yt_dlp_media(BILIBILI, tmp_path / "v.mp4", duration_seconds=628.0)

    assert "会员/付费内容，只能拿到试看片段" in str(caught.value)
    assert list(tmp_path.iterdir()) == []
    assert diagnose_error(caught.value)["retryable"] is False


def test_a_bilibili_full_download_is_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(vs, "_run_process", FakeYtDlp())
    monkeypatch.setattr(vs, "_probe_duration_seconds", lambda _p: 627.0)
    vs.download_yt_dlp_media(BILIBILI, tmp_path / "v.mp4", duration_seconds=628.0)
    assert (tmp_path / "v.mp4").is_file()


# ── 5. the message names the actual cause ──────────────────────────────────

def test_a_douyin_image_post_link_is_refused_before_any_download(monkeypatch):
    fake = FakeYtDlp()
    monkeypatch.setattr(vs, "_run_process", fake)
    with pytest.raises(vs.VideoSourceResolutionError, match="图文作品没有可转写的音视频"):
        vs.resolve_video("https://www.douyin.com/note/7542136767090117942")
    assert fake.calls == []


def test_douyin_with_no_media_says_image_post_deleted_or_private_not_login(monkeypatch):
    """Requirement: an unreadable login is not blamed when the third-party
    resolver (which needs no login) also finds no video."""
    monkeypatch.setattr(vs, "_run_process", FakeYtDlp(cookies_unreadable=True, fail_with="ERROR: Fresh cookies are needed"))
    monkeypatch.setattr(vs, "_resolve_with_miuistore_attempt", lambda *_a, **_k: (None, "no_downloadable_media"))

    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video(DOUYIN, cookies_from_browser="chrome")

    message = str(caught.value)
    assert "图文" in message and "删除" in message and "私密" in message
    assert "读不到" not in message


@pytest.mark.parametrize("stderr,expected", [
    ("ERROR: [youtube] x: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies", "不是机器人"),
    ("ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate for some users.", "年龄限制"),
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access to this video", "私享"),
    ("ERROR: [youtube] x: Video unavailable. This video has been removed by the uploader", "已删除"),
])
def test_youtube_failures_name_their_cause(monkeypatch, stderr, expected):
    monkeypatch.setattr(vs, "_run_process", FakeYtDlp(fail_with=stderr))
    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video(YOUTUBE)
    assert expected in str(caught.value)
    assert "重试一次" not in diagnose_error(caught.value)["next_action"]


def test_bilibili_only_asks_for_a_login_when_the_login_is_the_problem(monkeypatch):
    monkeypatch.setattr(vs, "_run_process", FakeYtDlp(fail_with="ERROR: [BiliBili] x: This video may be deleted or geo-restricted."))
    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video(BILIBILI)
    assert "需要登录" not in str(caught.value) and "删除" in str(caught.value)


def test_bilibili_with_an_unreadable_login_is_not_told_to_turn_the_login_on(monkeypatch):
    monkeypatch.setattr(vs, "_run_process", FakeYtDlp(cookies_unreadable=True, fail_with="ERROR: HTTP Error 403: Forbidden"))
    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video(BILIBILI, cookies_from_browser="chrome")
    assert "读不到 Chrome" in str(caught.value)


@pytest.mark.parametrize("raw", [
    "ERROR: [Douyin] 1: Fresh cookies (not necessarily logged in) are needed",
    "视频链接只允许使用公网 HTTP/HTTPS 端口",
    vs.link_too_large_message(900 * 1024 * 1024, 600 * 1024 * 1024),
])
def test_failures_that_a_retry_cannot_fix_do_not_suggest_one(raw):
    diagnosis = diagnose_error(raw)
    assert diagnosis["code"] != "unknown_error"
    assert "重试一次" not in diagnosis["next_action"] and diagnosis["retryable"] is False


# ── 6. finding the link in share text ───────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("看看【作品】 https://v.douyin.com/XRII7O_AcDI/复制此链接，打开Dou音搜索", "https://v.douyin.com/XRII7O_AcDI/"),
    ("复制打开抖音 v.douyin.com/W84jeWwrjEw/ Mww:/", "https://v.douyin.com/W84jeWwrjEw/"),
    ("【标题】 b23.tv/abc123", "https://b23.tv/abc123"),
    ("https://bili2233.cn/xyz。", "https://bili2233.cn/xyz"),
    ("店铺 https://shop.example.com/a 视频 https://www.bilibili.com/video/BV1x", "https://www.bilibili.com/video/BV1x"),
])
def test_the_link_is_found_whole_and_alone(text, expected):
    assert vs.extract_first_url(text) == expected


def test_iesdouyin_and_bili2233_are_recognised():
    assert vs.is_douyin_url("https://www.iesdouyin.com/share/video/7693842253651518287/")
    assert vs.normalize_douyin_url("https://www.iesdouyin.com/share/video/7693842253651518287/") == (
        "https://www.douyin.com/video/7693842253651518287"
    )
    assert vs.is_bilibili_url("https://bili2233.cn/xyz")


def _submit(monkeypatch, text):
    monkeypatch.setattr(lvs, "claim_task_id", lambda *a, **k: "t-1")
    monkeypatch.setattr(lvs, "log_event", lambda **v: None)
    monkeypatch.setattr(lvs, "upsert_job", lambda **v: None)
    monkeypatch.setattr(lvs, "get_preference", lambda name: None)

    async def fake_start(**_k):
        return None

    monkeypatch.setattr(lvs, "_start_behind_queue", fake_start)
    return asyncio.run(lvs.submit_video_source_job(input_text=text, title="", raw_options={}, client_id="c"))


def test_several_links_are_flagged_and_the_full_link_is_stored(monkeypatch):
    """Requirement: one link is processed, the response says the others were
    ignored, and the whole link is stored for retries."""
    long_text = "x" * 300 + " https://www.youtube.com/watch?v=a https://www.youtube.com/watch?v=b"
    job = _submit(monkeypatch, long_text)
    assert job["extra_urls_ignored"] is True and job["warning"]
    assert job["metadata"]["video_source_url"] == "https://www.youtube.com/watch?v=a"

    job = _submit(monkeypatch, "https://www.youtube.com/watch?v=a")
    assert job["extra_urls_ignored"] is False and "warning" not in job


# ── 7. half-written files are never reused, concurrent copies never clash ──

def test_an_interrupted_download_leaves_nothing_behind(monkeypatch, tmp_path):
    class Broken(io.BytesIO):
        headers = {"content-length": "100"}

    monkeypatch.setattr(vs, "open_public_http_url", lambda *_a, **_k: Broken(b"short"))
    with pytest.raises(RuntimeError):
        vs.download_file("https://example.com/talk.mp4", tmp_path / "talk.mp4")
    assert list(tmp_path.iterdir()) == []


def test_a_damaged_earlier_download_is_fetched_again(monkeypatch, tmp_path):
    from backend.core import media_preflight

    target = tmp_path / "v.mp4"
    target.write_bytes(b"truncated")

    def reject(_path):
        raise media_preflight.MediaPreflightError("media_container_unreadable", "媒体文件无法读取")

    monkeypatch.setattr(media_preflight, "preflight_media_file", reject)
    assert vs._reusable_download(target) is False
    assert not target.exists()


def test_two_submissions_of_one_link_download_it_once(monkeypatch, tmp_path):
    resolved = vs.ResolvedVideo(provider="direct", source_url="https://example.com/talk.mp4",
                                download_url="https://example.com/talk.mp4", video_id="talk1")
    monkeypatch.setattr(vs, "resolve_video", lambda *_a, **_k: vs.ResolvedVideo(**vars(resolved)))
    downloads = []

    def slow_download(_resolved, file_path, *_a, **_k):
        downloads.append(file_path)
        time.sleep(0.3)
        file_path.write_bytes(b"video")
        return 5

    monkeypatch.setattr(vs, "_download_media", slow_download)
    monkeypatch.setattr(vs, "_reusable_download", lambda path: path.is_file())
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        vs.download_video_source("https://example.com/talk.mp4", video_dir=tmp_path))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(downloads) == 1 and len(results) == 2
    assert results[0].file_path == results[1].file_path


# ── 8. Douyin resolved by yt-dlp but failing to download ────────────────────

def test_a_douyin_download_failure_falls_back_to_the_third_party_resolver(monkeypatch, tmp_path):
    resolved = vs.ResolvedVideo(provider="yt-dlp", source_url=DOUYIN, download_url="https://v5.douyinvod.com/x",
                                resolution_trace=[{"provider": "yt-dlp", "status": "selected"}])

    def failing(*_a, **_k):
        raise RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")

    monkeypatch.setattr(vs, "download_yt_dlp_media", failing)
    fallback = vs.ResolvedVideo(provider="miuistore", source_url=DOUYIN, download_url="https://v5.douyinvod.com/y")
    monkeypatch.setattr(vs, "_resolve_with_miuistore_attempt", lambda *_a, **_k: (fallback, None))
    monkeypatch.setattr(vs, "download_file", lambda url, path, *_a, **_k: path.write_bytes(b"v") or 1)

    size = vs._download_media(resolved, tmp_path / "v.mp4", None, cookies_from_browser=None,
                              cookie_use=vs.CookieUse(), allow_miuistore=True, cancellation_event=None)

    assert size == 1
    assert [s["provider"] for s in resolved.resolution_trace] == ["yt-dlp", "yt-dlp-download", "miuistore"]


def test_a_douyin_download_failure_without_fallback_is_explained_in_chinese(monkeypatch, tmp_path):
    resolved = vs.ResolvedVideo(provider="yt-dlp", source_url=DOUYIN, download_url="x")
    monkeypatch.setattr(vs, "download_yt_dlp_media", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("ERROR: boom")))
    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs._download_media(resolved, tmp_path / "v.mp4", None, cookies_from_browser=None,
                           cookie_use=vs.CookieUse(), allow_miuistore=False, cancellation_event=None)
    assert "ERROR" not in str(caught.value) and "抖音" in str(caught.value)


# ── 9. size and length are checked before downloading ──────────────────────

def test_the_link_limit_is_the_upload_limit(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_MAX_UPLOAD_MB", "2048")
    assert vs.max_video_bytes() == 2048 * 1024 * 1024


def test_a_link_known_to_be_too_large_is_refused_before_downloading(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_MAX_UPLOAD_MB", "100")
    monkeypatch.setattr(vs, "resolve_video", lambda *_a, **_k: vs.ResolvedVideo(
        provider="yt-dlp", source_url=YOUTUBE, download_url="x", estimated_size_bytes=300 * 1024 * 1024))
    monkeypatch.setattr(vs, "captions_first_provider", lambda *_a, **_k: None)
    monkeypatch.setattr(vs, "_download_media", lambda *_a, **_k: pytest.fail("downloaded anyway"))

    with pytest.raises(RuntimeError) as caught:
        vs.download_video_source(YOUTUBE, video_dir=tmp_path)

    message = str(caught.value)
    assert "300 MB" in message and "100 MB" in message and "先下载到本机" in message


def test_a_link_known_to_be_too_long_is_refused_before_downloading(monkeypatch, tmp_path):
    monkeypatch.setattr(vs, "resolve_video", lambda *_a, **_k: vs.ResolvedVideo(
        provider="yt-dlp", source_url=YOUTUBE, download_url="x", duration_seconds=7200))
    monkeypatch.setattr(vs, "captions_first_provider", lambda *_a, **_k: None)
    monkeypatch.setattr(vs, "_download_media", lambda *_a, **_k: pytest.fail("downloaded anyway"))
    with pytest.raises(RuntimeError, match="时长过长"):
        vs.download_video_source(YOUTUBE, video_dir=tmp_path, duration_limit_seconds=3600)


def test_yt_dlp_refusing_a_large_file_reports_the_size_not_a_missing_file(monkeypatch, tmp_path):
    line = "[download] File is larger than max-filesize (900000000 bytes > 600000000 bytes). Aborting."
    monkeypatch.setattr(vs, "_run_process", FakeYtDlp(download_bytes=b"", stdout_lines=[line]))
    with pytest.raises(RuntimeError) as caught:
        vs.download_yt_dlp_media(YOUTUBE, tmp_path / "v.mp4")
    assert "链接视频过大" in str(caught.value) and "没有写出" not in str(caught.value)


# ── 10. at most two link downloads at a time ────────────────────────────────

def test_no_more_than_two_links_download_at_once(monkeypatch):
    state = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def download(*_a, **_k):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.2)
        with lock:
            state["now"] -= 1
        raise RuntimeError("stop here")

    monkeypatch.setattr(lvs, "download_video_source", download)
    monkeypatch.setattr(lvs, "upsert_job", lambda **_k: None)
    monkeypatch.setattr(lvs, "log_event", lambda **_k: None)
    monkeypatch.setattr(lvs, "get_preference", lambda _n: None)

    async def publish(*_a, **_k):
        return None

    monkeypatch.setattr(lvs.JOB_EVENTS, "publish", publish)

    async def scenario():
        from backend.core.local_entry_guards import CancellationGate

        await asyncio.gather(*(
            lvs._download_then_process(
                task_id=f"t{i}", input_text=YOUTUBE, title=None, options={}, allow_miuistore=True,
                client_id="c", gate=CancellationGate(), route="/r", previous=None, done=asyncio.Event(),
            )
            for i in range(5)
        ))

    asyncio.run(scenario())
    assert state["peak"] == 2


# ── 11. download progress moves ─────────────────────────────────────────────

def test_yt_dlp_download_reports_percent(monkeypatch, tmp_path):
    lines = [f"{vs._PROGRESS_PREFIX} {n} 1000 NA" for n in (100, 500, 1000)]
    fake = FakeYtDlp(stdout_lines=lines)
    monkeypatch.setattr(vs, "_run_process", fake)
    reports = []

    vs.download_yt_dlp_media(YOUTUBE, tmp_path / "v.mp4", reports.append)

    percents = [r.percent for r in reports if r.percent is not None]
    assert percents[:3] == [10, 50, 99] and percents[-1] == 100
    assert "--progress-template" in fake.calls[0]


# ── 12. retrying a link task fetches the link again ─────────────────────────

# A link no other test submits: the retry goes through the real duplicate check,
# and a task some other test left queued for a shared link would answer instead.
_RETRIED_LINK = "https://www.douyin.com/video/7600000000000000001"


def _link_job_submitted_with_old_settings() -> dict:
    """A Douyin task first submitted with the third-party resolver on and
    Chrome's login, which then failed while downloading."""
    return {"task_id": "old", "status": "failed", "source_type": "video_link", "metadata": {
        "video_source_url": _RETRIED_LINK,
        "video_source_input_preview": "cut",
        "queue_options": {"cookies_from_browser": "chrome", "note_mode": "auto"},
        "video_source_allow_miuistore": True,
    }}


def _retry_with_preferences(monkeypatch, preferences: dict) -> dict:
    """Retry the old link task through the real submit path and answer how the
    download would have been made."""
    import backend.routers.local_processing as lp
    from backend.core import job_store

    job = _link_job_submitted_with_old_settings()
    monkeypatch.setattr(lp, "get_job", lambda task_id, client_id=None: job if task_id == "old" else job_store.get_job(task_id, client_id=client_id))
    monkeypatch.setattr(lp, "find_source_file", lambda _t: None)
    monkeypatch.setattr(lvs, "get_preference", lambda name: preferences.get(name))
    fetched: dict = {}

    async def run_job(**kwargs):
        fetched.update(kwargs)
        fetched["cookies_browser"] = lvs._video_cookies_browser(kwargs["options"])

    async def start(**kwargs):
        await kwargs["chained_worker"](None, asyncio.Event())

    monkeypatch.setattr(lvs, "_run_local_video_source_job", run_job)
    monkeypatch.setattr(lvs, "_start_behind_queue", start)

    result = asyncio.run(lp.retry_task("old", client_id="c", local_caller=True))
    assert result["source_task_id"] == "old" and result["task_id"] != "old"
    # Nothing ran it, so it would stay queued and answer the next retry as an
    # active duplicate of the same link.
    job_store.delete_jobs([result["task_id"]], client_id="c")
    return fetched


def test_retrying_a_link_task_that_never_downloaded_submits_the_link_again(monkeypatch):
    fetched = _retry_with_preferences(monkeypatch, {})

    assert fetched["input_text"] == _RETRIED_LINK
    assert fetched["options"]["note_mode"] == "auto", "the note settings still carry over"


def test_a_retry_does_not_use_the_third_party_resolver_the_user_has_since_turned_off(monkeypatch):
    """Requirement: the user submitted with the Douyin fallback on, then turned
    it off in settings. A retry (button, MCP retry_task, startup recovery) must
    not send the link to the third party."""
    fetched = _retry_with_preferences(monkeypatch, {"allow_miuistore": False})

    assert fetched["allow_miuistore"] is False


def test_a_retry_by_a_user_who_never_decided_does_not_use_the_third_party_resolver(monkeypatch):
    """The first submission had the fallback on; the user has never answered the
    consent question since it became one, so the retry must not assume yes."""
    fetched = _retry_with_preferences(monkeypatch, {})

    assert fetched["allow_miuistore"] is None


def test_a_retry_reads_the_browser_login_the_user_chose_now(monkeypatch):
    fetched = _retry_with_preferences(monkeypatch, {"video_cookies_browser": "safari"})

    assert "cookies_from_browser" not in fetched["options"]
    assert fetched["cookies_browser"] == "safari"


# ── 13. small things ────────────────────────────────────────────────────────

def test_douyin_titles_lose_their_hashtag_tail():
    assert vs.strip_hashtags("周末做饭 #美食 #日常vlog") == "周末做饭"
    assert vs.strip_hashtags("#美食 #日常") == "美食 日常"


def test_a_direct_link_is_titled_by_its_file_name():
    resolved = vs.resolve_direct_video("https://example.com/files/%E8%AE%B2%E5%BA%A7.mp4")
    assert resolved.title == "讲座"


@pytest.mark.parametrize("cookies,expected_calls", [(None, 1), ("chrome", 4)])
def test_douyin_is_retried_only_when_a_login_was_sent(monkeypatch, cookies, expected_calls):
    fake = FakeYtDlp(fail_with="ERROR: [Douyin] 1: Fresh cookies (not necessarily logged in) are needed")
    monkeypatch.setattr(vs, "_run_process", fake)
    monkeypatch.setattr(vs, "_resolve_with_miuistore_attempt", lambda *_a, **_k: (None, "unavailable"))
    with pytest.raises(vs.VideoSourceResolutionError):
        vs.resolve_video(DOUYIN, cookies_from_browser=cookies)
    assert len(fake.calls) == expected_calls


def test_the_browser_login_preference_round_trips_as_a_name(monkeypatch, tmp_path):
    """Requirement: POST /preferences {"video_cookies_browser": "chrome"} is
    stored as the browser name, while allow_miuistore stays a boolean, and a
    link task without its own choice uses the remembered browser."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.routers.local_system as local_system
    from backend.core.local_config import load_preferences, save_preferences

    path = tmp_path / "config.json"
    monkeypatch.setattr(local_system, "load_preferences", lambda: load_preferences(path))
    monkeypatch.setattr(local_system, "save_preferences", lambda patch: save_preferences(patch, path))
    app = FastAPI()
    app.include_router(local_system.router)
    client = TestClient(app)

    saved = client.post("/preferences", json={"video_cookies_browser": "Chrome", "allow_miuistore": "true"})
    assert saved.json()["preferences"] == {"video_cookies_browser": "chrome", "allow_miuistore": True}
    assert client.post("/preferences", json={"video_cookies_browser": "netscape"}).status_code == 400
    cleared = client.post("/preferences", json={"video_cookies_browser": ""})
    assert "video_cookies_browser" not in cleared.json()["preferences"]

    monkeypatch.setattr(lvs, "get_preference", lambda name: "safari" if name == "video_cookies_browser" else None)
    assert lvs._video_cookies_browser({}) == "safari"
    assert lvs._video_cookies_browser({"cookies_from_browser": "firefox"}) == "firefox"
