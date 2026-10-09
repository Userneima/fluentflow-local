"""The Douyin third-party fallback runs only with the user's consent.

Requirements, in the user's terms:

- The fallback sends the link (only the link) to a third party, so it runs only
  after the user said yes: in the request, or remembered in settings
  (``allow_miuistore`` true). A remembered no (false) keeps it off.
- When the user has never decided and the request does not say, the fallback is
  not used. A Douyin link that needed it fails with a Chinese message saying the
  direct download failed and the link can be handed to the third-party resolver
  (only the link is sent) if the user allows it in 设置 → 抖音备用解析, or with
  allow_miuistore for AI tools. The diagnosis is ``douyin_fallback_not_allowed``
  and it is retryable (allowing it and retrying is the fix).
- The request's own answer wins over the remembered one.
- A retry follows the current setting, so a never-decided user is still asked.
- The MCP ``submit_video_link`` tool carries the agent's answer when given and
  leaves the decision to the setting otherwise.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

import backend.core.video_source as vs
import backend.routers.local_video_sources as lvs
from backend.core.local_error_diagnostics import diagnose_error

DOUYIN = "https://www.douyin.com/video/7693194858911108367"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.delenv("YT_DLP_COOKIES_FROM_BROWSER", raising=False)
    monkeypatch.setattr(vs, "resolve_direct_video", lambda _u: None)
    monkeypatch.setattr(vs, "_pause_before_retry", lambda *_a, **_k: None)


def _yt_dlp_cannot_resolve(monkeypatch):
    monkeypatch.setattr(
        vs, "_resolve_with_yt_dlp_attempt", lambda *_a, **_k: (None, "fresh_cookies_required", None)
    )


def _fallback_must_not_run(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("the link must not be sent to the third party")

    monkeypatch.setattr(vs, "_resolve_with_miuistore_attempt", refuse)


def _assert_consent_message(message: str) -> None:
    assert "抖音直接下载没有成功" in message
    assert "第三方解析" in message and "只发送链接本身" in message
    assert "设置 → 抖音备用解析" in message
    assert "allow_miuistore" in message


# ── what the user sees when they never decided ─────────────────────────────

def test_never_decided_does_not_send_the_link_and_says_how_to_allow_it(monkeypatch):
    _yt_dlp_cannot_resolve(monkeypatch)
    _fallback_must_not_run(monkeypatch)

    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video(DOUYIN, allow_miuistore=None)

    _assert_consent_message(str(caught.value))
    assert caught.value.resolution_trace[-1] == {
        "provider": "miuistore", "status": "skipped", "reason": "not_allowed_yet",
    }


def test_never_decided_also_holds_when_the_download_step_fails(monkeypatch, tmp_path):
    resolved = vs.ResolvedVideo(provider="yt-dlp", source_url=DOUYIN, download_url="x")
    monkeypatch.setattr(
        vs, "download_yt_dlp_media", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("ERROR: 403")),
    )
    _fallback_must_not_run(monkeypatch)

    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs._download_media(resolved, tmp_path / "v.mp4", None, cookies_from_browser=None,
                           cookie_use=vs.CookieUse(), allow_miuistore=None, cancellation_event=None)

    _assert_consent_message(str(caught.value))


def test_the_consent_failure_has_its_own_retryable_diagnosis():
    diagnosis = diagnose_error(vs.DOUYIN_FALLBACK_NOT_ALLOWED_MESSAGE)

    assert diagnosis["code"] == "douyin_fallback_not_allowed"
    assert diagnosis["retryable"] is True
    assert "抖音备用解析" in diagnosis["next_action"]


def test_a_stale_login_hint_does_not_hide_the_consent_question(monkeypatch):
    _yt_dlp_cannot_resolve(monkeypatch)
    _fallback_must_not_run(monkeypatch)
    monkeypatch.setattr(vs, "_yt_dlp_cookies_args", lambda _b: ["--cookies-from-browser", "chrome"])

    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video(DOUYIN, cookies_from_browser="chrome", allow_miuistore=None)

    assert diagnose_error(str(caught.value))["code"] == "douyin_fallback_not_allowed"


# ── a decision, once made, is followed ─────────────────────────────────────

def test_a_yes_uses_the_fallback(monkeypatch):
    _yt_dlp_cannot_resolve(monkeypatch)
    video = vs.ResolvedVideo(provider="miuistore", source_url=DOUYIN, download_url="https://v5.douyinvod.com/y")
    monkeypatch.setattr(vs, "_resolve_with_miuistore_attempt", lambda *_a, **_k: (video, None))

    assert vs.resolve_video(DOUYIN, allow_miuistore=True).provider == "miuistore"


def test_a_no_keeps_the_fallback_off_without_asking_again(monkeypatch):
    _yt_dlp_cannot_resolve(monkeypatch)
    _fallback_must_not_run(monkeypatch)

    with pytest.raises(vs.VideoSourceResolutionError) as caught:
        vs.resolve_video(DOUYIN, allow_miuistore=False)

    assert "抖音备用解析" not in str(caught.value)


@pytest.mark.parametrize("request_options,remembered,expected", [
    ({}, None, None),                              # never decided → not used
    ({}, True, True),                              # remembered yes
    ({}, False, False),                            # remembered no
    ({"allow_miuistore": "true"}, None, True),     # the request answers
    ({"allow_miuistore": True}, False, True),      # the request wins over a remembered no
    ({"allow_miuistore": "false"}, True, False),   # and over a remembered yes
    ({"allow_miuistore": ""}, True, True),         # an empty value says nothing
])
def test_the_request_answer_wins_then_the_setting(monkeypatch, request_options, remembered, expected):
    monkeypatch.setattr(lvs, "get_preference", lambda name: remembered if name == "allow_miuistore" else None)

    assert lvs._allow_miuistore(request_options) is expected


def test_a_submission_without_a_decision_records_none_and_hands_it_down(monkeypatch):
    monkeypatch.setattr(lvs, "get_preference", lambda name: None)
    monkeypatch.setattr(lvs, "_refuse_or_reuse_duplicate", lambda *_a, **_k: None)
    monkeypatch.setattr(lvs, "claim_task_id", lambda *a, **k: "consent-1")
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
        input_text=DOUYIN, title="", raw_options={}, client_id="c",
    ))

    assert job["metadata"].get("video_source_allow_miuistore") is None
    assert handed["allow_miuistore"] is None


# ── the MCP tool ───────────────────────────────────────────────────────────

def _mcp():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "fluentflow_mcp_server.py"
    spec = importlib.util.spec_from_file_location("fluentflow_mcp_server_consent", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("answer,sent", [(None, None), (True, "true"), (False, "false")])
def test_the_mcp_tool_sends_the_agents_answer_only_when_given(monkeypatch, answer, sent):
    mcp = _mcp()
    calls: list[dict] = []
    monkeypatch.setattr(mcp, "_agent_request", lambda method, path, **kw: calls.append(kw) or {"ok": True})

    kwargs = {} if answer is None else {"allow_miuistore": answer}
    mcp.submit_video_link(DOUYIN, **kwargs)

    assert calls[0]["payload"]["options"].get("allow_miuistore") == sent


def test_the_mcp_tool_documents_the_parameter():
    mcp = _mcp()
    [tool] = [t for t in mcp.TOOL_DEFINITIONS if t["name"] == "submit_video_link"]
    described = tool["inputSchema"]["properties"]["allow_miuistore"]["description"]

    assert described and "douyin_fallback_not_allowed" in described
    assert "allow_miuistore" in inspect.signature(mcp.submit_video_link).parameters
