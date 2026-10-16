"""A site in China that refuses a proxied request is asked again directly.

Found 2026-10-16 on a Windows machine whose VPN client is the system proxy:
yt-dlp got HTTP 412 from Bilibili for every proxied request and listed the
video at once without the proxy, while the user was told "B 站没有返回可下载的
视频，请在 B 站里打开确认", which nothing they could do would fix.
"""

from __future__ import annotations

import subprocess

import pytest

import backend.core.video_source as vs

BILIBILI = "https://www.bilibili.com/video/BV1GJ411x7h7"
REFUSED = "ERROR: [BiliBili] 1GJ411x7h7: Unable to download webpage: HTTP Error 412: Precondition Failed"


@pytest.fixture
def runs(monkeypatch):
    calls: list[list[str]] = []
    answers: list[tuple[int, str]] = []

    def run(args, **_kwargs):
        calls.append(list(args))
        code, stderr = answers.pop(0) if answers else (0, "")
        return subprocess.CompletedProcess(args, code, "{}" if code == 0 else "", stderr)

    monkeypatch.setattr(vs, "_run_process", run)
    monkeypatch.setattr(vs, "_yt_dlp_cookies_args", lambda *_a: [])
    return calls, answers


def _step(url, use):
    return vs._run_yt_dlp_step(
        lambda cookies: ["python", "-m", "yt_dlp", *cookies, url],
        url=url, cookies_from_browser=None, cookie_use=use, timeout=10,
    )


def test_a_refused_proxied_request_is_tried_once_directly(runs, monkeypatch):
    calls, answers = runs
    monkeypatch.setattr(vs, "_system_proxy_configured", lambda: True)
    answers.extend([(1, REFUSED), (0, "")])
    use = vs.CookieUse()

    result = _step(BILIBILI, use)

    assert result.returncode == 0
    assert "--proxy" not in calls[0]
    assert calls[1][3:5] == ["--proxy", ""]
    assert use.direct is True and use.notes[-1]["reason"] == "refused_through_proxy"


def test_later_steps_of_the_link_connect_directly_too(runs, monkeypatch):
    calls, _answers = runs
    monkeypatch.setattr(vs, "_system_proxy_configured", lambda: True)
    use = vs.CookieUse(direct=True)

    _step(BILIBILI, use)

    assert len(calls) == 1 and calls[0][3:5] == ["--proxy", ""]


def test_without_a_proxy_a_412_is_not_retried(runs, monkeypatch):
    calls, answers = runs
    monkeypatch.setattr(vs, "_system_proxy_configured", lambda: False)
    answers.append((1, REFUSED))

    assert _step(BILIBILI, vs.CookieUse()).returncode == 1
    assert len(calls) == 1


def test_a_site_abroad_keeps_the_proxy(runs, monkeypatch):
    calls, answers = runs
    monkeypatch.setattr(vs, "_system_proxy_configured", lambda: True)
    answers.append((1, "ERROR: [youtube] abc: HTTP Error 412: Precondition Failed"))

    assert _step("https://www.youtube.com/watch?v=abc", vs.CookieUse()).returncode == 1
    assert len(calls) == 1, "YouTube is reached through the proxy on purpose"
