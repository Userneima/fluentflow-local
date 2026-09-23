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


def test_a_stale_douyin_login_is_reported_as_a_login_to_renew(monkeypatch):
    monkeypatch.setattr(vs, "resolve_direct_video", lambda _u: None)
    monkeypatch.setattr(
        vs, "_resolve_with_yt_dlp_attempt", lambda *_a, **_k: (None, "fresh_cookies_required")
    )

    with pytest.raises(vs.VideoSourceResolutionError, match="登录信息过期了") as caught:
        vs.resolve_video("https://v.douyin.com/abc/", cookies_from_browser="chrome")

    assert "Chrome" in str(caught.value)
