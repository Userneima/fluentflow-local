"""Douyin links are fetched through the third-party resolver, always.

Requirements, in the user's terms (decided 2026-10-12):

- Sharing a public link to be processed is the request to process it. The
  resolver gets only that link, so nobody is asked and there is no switch.
- yt-dlp is not tried first: it never fetched a Douyin link on this machine.
- An old "off" choice, remembered by the service or sent by an older client or
  agent, no longer stops a Douyin link; the service drops it on the next save.
- The MCP ``submit_video_link`` tool has no parameter for it.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

import backend.core.video_source as vs
import backend.core.link_submission as lvs
from backend.core.local_config import get_preference, load_preferences, save_preferences

DOUYIN = "https://www.douyin.com/video/7693194858911108367"
VIDEO = vs.ResolvedVideo(provider="miuistore", source_url=DOUYIN, download_url="https://v5.douyinvod.com/y")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.delenv("YT_DLP_COOKIES_FROM_BROWSER", raising=False)
    monkeypatch.setattr(vs, "resolve_direct_video", lambda _u: None)
    monkeypatch.setattr(
        vs, "_resolve_with_yt_dlp_attempt", lambda *_a, **_k: pytest.fail("yt-dlp was asked for a Douyin link")
    )


def test_a_douyin_link_goes_to_the_resolver_without_asking(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(vs, "_resolve_with_miuistore_attempt", lambda url, **_k: sent.append(url) or (VIDEO, None))

    resolved = vs.resolve_video(f"复制打开抖音 {DOUYIN} 看看")

    assert resolved.provider == "miuistore"
    assert sent == [DOUYIN]  # the link alone, not the share text around it
    assert "allow_miuistore" not in inspect.signature(vs.resolve_video).parameters
    assert "allow_miuistore" not in inspect.signature(vs.download_video_source).parameters


def test_an_old_off_choice_does_not_stop_a_submission(monkeypatch):
    """A remembered "off" and an older client's explicit "false" are both ignored."""
    monkeypatch.setattr(lvs, "get_preference", lambda name: False if name == "allow_miuistore" else None)
    monkeypatch.setattr(lvs, "_refuse_or_reuse_duplicate", lambda *_a, **_k: None)
    monkeypatch.setattr(lvs, "claim_task_id", lambda *a, **k: "douyin-1")
    monkeypatch.setattr(lvs, "log_event", lambda **v: None)
    monkeypatch.setattr(lvs, "upsert_job", lambda **v: None)
    handed: dict = {}

    async def run_job(**kwargs):
        handed.update(kwargs)

    async def start(**kwargs):
        await kwargs["chained_worker"](None, asyncio.Event())

    monkeypatch.setattr(lvs, "_run_local_video_source_job", run_job)
    monkeypatch.setattr(lvs, "_start_behind_queue", start)

    job = asyncio.run(lvs.submit_video_source_job(
        input_text=DOUYIN, title="", raw_options={"allow_miuistore": "false"}, client_id="c",
    ))

    assert "allow_miuistore" not in handed
    assert "video_source_allow_miuistore" not in job["metadata"]


def test_the_retired_preference_is_dropped_on_the_next_save(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"preferences": {"allow_miuistore": false, "video_cookies_browser": "chrome"}}')

    saved = save_preferences({"allow_miuistore": True, "lark_export_route": "auto"}, path)

    assert saved == {"video_cookies_browser": "chrome", "lark_export_route": "auto"}
    assert get_preference("allow_miuistore", path) is None
    assert load_preferences(path) == saved


def _mcp():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "fluentflow_mcp_server.py"
    spec = importlib.util.spec_from_file_location("fluentflow_mcp_server_douyin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_mcp_tool_has_no_switch_and_ignores_an_old_agents_one(monkeypatch):
    mcp = _mcp()
    [tool] = [t for t in mcp.TOOL_DEFINITIONS if t["name"] == "submit_video_link"]
    assert "allow_miuistore" not in tool["inputSchema"]["properties"]
    assert "allow_miuistore" not in inspect.signature(mcp.submit_video_link).parameters

    calls: list[dict] = []
    monkeypatch.setattr(mcp, "_agent_request", lambda method, path, **kw: calls.append(kw) or {"ok": True})
    mcp._call_tool("submit_video_link", {"input_text": DOUYIN, "allow_miuistore": False})

    assert "allow_miuistore" not in calls[0]["payload"]["options"]
