from types import SimpleNamespace

import pytest

import backend.core.video_source as vs


@pytest.fixture()
def chrome(tmp_path, monkeypatch):
    """A Chrome with three profiles; only Profile 2 has visited Douyin."""
    for name in ("Default", "Profile 1", "Profile 2"):
        (tmp_path / name).mkdir()
    jars = {
        "Default": [SimpleNamespace(domain=".bilibili.com")],
        "Profile 1": [],
        "Profile 2": [SimpleNamespace(domain=".douyin.com"), SimpleNamespace(domain="www.douyin.com")],
    }
    import yt_dlp.cookies as ydc

    monkeypatch.setattr(ydc, "_get_chromium_based_browser_settings", lambda _b: {"browser_dir": str(tmp_path)})
    monkeypatch.setattr(ydc, "extract_cookies_from_browser", lambda _b, profile=None: jars[profile])
    monkeypatch.setattr(vs, "_profile_choice_cache", {})
    return tmp_path


def test_the_profile_that_has_visited_the_site_is_the_one_read(chrome):
    assert vs.browser_cookie_spec("chrome", "https://v.douyin.com/abc/") == "chrome:Profile 2"
    assert vs.browser_cookie_spec("chrome", "https://b23.tv/xyz") == "chrome:Default"


def test_a_named_profile_or_an_unknown_site_is_left_as_given(chrome):
    assert vs.browser_cookie_spec("chrome:Profile 1", "https://v.douyin.com/abc/") == "chrome:Profile 1"
    assert vs.browser_cookie_spec("chrome", "https://example.com/v.mp4") == "chrome"
    assert vs.browser_cookie_spec("", "https://v.douyin.com/abc/") is None


# ── Douyin links go straight to the third-party resolver ───────────────────
#
# Requirement (2026-10-12): yt-dlp never fetched a Douyin link on this machine
# (every Douyin task came through the resolver after yt-dlp failed), so a
# Douyin link is not handed to yt-dlp at all, browser login or not.

@pytest.mark.parametrize("cookies", [None, "chrome"])
def test_a_douyin_link_never_reaches_yt_dlp(monkeypatch, cookies):
    monkeypatch.setattr(vs, "resolve_direct_video", lambda _u: None)
    monkeypatch.setattr(vs, "_resolve_with_yt_dlp_attempt",
                        lambda *_a, **_k: pytest.fail("yt-dlp was asked for a Douyin link"))
    monkeypatch.setattr(vs, "download_yt_dlp_media",
                        lambda *_a, **_k: pytest.fail("yt-dlp downloaded a Douyin link"))
    video = vs.ResolvedVideo(provider="miuistore", source_url="https://www.douyin.com/video/1",
                             download_url="https://v5.douyinvod.com/x.mp4")
    monkeypatch.setattr(vs, "_resolve_with_miuistore_attempt", lambda *_a, **_k: (video, None))

    resolved = vs.resolve_video("https://v.douyin.com/abc/", cookies_from_browser=cookies)

    assert resolved.provider == "miuistore"
    assert resolved.resolution_trace == [{"provider": "miuistore", "status": "selected"}]


def test_a_douyin_link_the_resolver_cannot_read_is_explained_without_login_talk(monkeypatch):
    monkeypatch.setattr(vs, "resolve_direct_video", lambda _u: None)
    monkeypatch.setattr(vs, "_resolve_with_miuistore_attempt", lambda *_a, **_k: (None, "unavailable"))

    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video("https://www.douyin.com/video/1", cookies_from_browser="chrome")

    message = str(caught.value)
    assert "本机" in message
    assert "登录" not in message


# ── the resolver's result page loads its result with a script call ─────────

_SHELL_PAGE = """<html><body><div class="main-out">正在查询，可能需要等待2-9秒......</div>
<script>
	$.get("https://apikr.convry.com/sph/public/dy-r?aweme_id=%2BAyd%3D").then(function(r){
		$(".main-out").html(r);
	});
</script></body></html>"""

_INNER_RESULT = """<div class="col-label">视频ID：</div><div class="col-value">7693842253651518287</div>
<div class="col-label">视频标题：</div><div class="col-value">拌饭</div>
<a href="https://p5-ex.douyinpic.com/cover.jpeg">封面</a>
<a href="https://v5-ali-northeast.douyinvod.com/abc/video/tos/cn/x/?mime_type=video_mp4">下载视频</a>"""


def _fake_resolver(pages):
    fetched = []

    def fetch(url, timeout=45, *, allowed_host_suffixes=()):
        import urllib.parse

        host = urllib.parse.urlparse(url).hostname or ""
        assert any(host == s or host.endswith("." + s) for s in allowed_host_suffixes), host
        fetched.append(url)
        for key, body in pages.items():
            if key in url:
                return body
        raise AssertionError(f"unexpected fetch {url}")

    return fetch, fetched


def test_the_fallback_follows_the_script_loaded_result(monkeypatch):
    fetch, fetched = _fake_resolver({
        "dy-check": '{"error":0,"url":"/sph/public/dy-d?url=abc"}',
        "dy-d?url=abc": _SHELL_PAGE,
        "apikr.convry.com": _INNER_RESULT,
    })
    monkeypatch.setattr(vs, "fetch_text", fetch)

    resolved, reason = vs._resolve_with_miuistore_attempt("https://www.douyin.com/video/7693842253651518287")

    assert reason is None
    assert "douyinvod.com" in resolved.download_url
    assert resolved.video_id == "7693842253651518287" and resolved.title == "拌饭"
    assert any("apikr.convry.com" in url for url in fetched)


def test_a_script_call_to_any_other_host_is_not_followed(monkeypatch):
    shell = _SHELL_PAGE.replace("apikr.convry.com", "elsewhere.example.com")
    fetch, fetched = _fake_resolver({
        "dy-check": '{"error":0,"url":"/sph/public/dy-d?url=abc"}',
        "dy-d?url=abc": shell,
    })
    monkeypatch.setattr(vs, "fetch_text", fetch)

    resolved, reason = vs._resolve_with_miuistore_attempt("https://www.douyin.com/video/1")

    assert resolved is None
    assert not any("elsewhere.example.com" in url for url in fetched)
