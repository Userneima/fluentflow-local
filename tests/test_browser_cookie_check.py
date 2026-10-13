"""The settings page's 「检测登录态」 says what is actually wrong.

Requirements (owner, 2026-10-14):
1. When macOS refuses to let FluentFlow read the browser's data, the message
   says so and names the fix: Full Disk Access for the program that starts
   FluentFlow, then restart FluentFlow. It does not send the user to check the
   browser is installed.
2. When the browser has no data folder at all, the message says it was not
   found on this computer.
3. A readable browser is still checked as before.
"""

from __future__ import annotations

import sys
import types

import pytest

from backend.core import video_source


@pytest.fixture(autouse=True)
def on_mac(monkeypatch):
    monkeypatch.setattr(video_source.sys, "platform", "darwin")


def _edge_folder(home):
    return home / "Library/Application Support/Microsoft Edge"


def test_refused_by_macos_names_the_permission_and_restart(monkeypatch, tmp_path):
    folder = _edge_folder(tmp_path)
    real_listdir = video_source.os.listdir

    def listdir(path):
        if str(path) == str(folder):
            raise PermissionError(1, "Operation not permitted")
        return real_listdir(path)

    monkeypatch.setattr(video_source.os, "listdir", listdir)
    result = video_source.check_browser_cookies("edge", home=tmp_path)
    assert result["ok"] is False
    assert result["code"] == "permission_denied"
    assert "完全磁盘访问权限" in result["message"]
    assert "终端" in result["message"]
    assert "退出 FluentFlow 再打开" in result["message"]
    assert "安装" not in result["message"]


def test_missing_browser_says_not_found(tmp_path):
    result = video_source.check_browser_cookies("edge", home=tmp_path)
    assert result["ok"] is False
    assert result["code"] == "browser_not_found"
    assert "没找到 Microsoft Edge" in result["message"]


def test_readable_browser_is_checked(monkeypatch, tmp_path):
    _edge_folder(tmp_path).mkdir(parents=True)
    cookie = types.SimpleNamespace(name="SESSDATA", domain=".bilibili.com")
    fake = types.ModuleType("yt_dlp.cookies")
    fake.extract_cookies_from_browser = lambda browser: [cookie]
    monkeypatch.setitem(sys.modules, "yt_dlp.cookies", fake)
    result = video_source.check_browser_cookies("edge", home=tmp_path)
    assert result == {"ok": True, "cookie_count": 1, "bilibili_logged_in": True}
