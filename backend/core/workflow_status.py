"""Who does each step a recording goes through, and what needs the person.

Decision 6.24 (owner, 2026-10-22): Settings opens with this, one line per step,
and the same list answers the start page and an agent asking over MCP, so no
screen works the rules out again on its own. Each line is built from what the
pipeline itself uses to pick: ``cut_judge.choose`` for the cut, the visual note
channel and ``local_ai_kwargs`` for the note, ``device_profile`` for the
effective preset and processor share (an environment variable wins there),
``diarization_status`` for speaker labels, the saved preferences, the agent
access status.

The note's text provider and model, speaker labels and auto export to Feishu
are saved in the service (decision 6.31) and read here like any other
preference, so the page and an agent asking get the same lines. Voice cleanup
and the start page's default source still live in the browser and come in
``choices``; without them (an agent asking) the line describes a job an agent
submits, and the cut is judged by that agent when FluentFlow has no model of
its own.

Every sentence comes in Chinese and English: ``{"zh": ..., "en": ...}``.
"""

from __future__ import annotations

import os
import time
from typing import Any, Mapping

from backend.core import claude_code_note, cut_judge, device_profile, local_intake_flow, visual_note_channel
from backend.core import text_providers
from backend.core.lark_cli_exporter import lark_cli_ready
from backend.core.local_config import get_preference, load_project_env, normalize_cookie_browser, resolve_secret
from backend.core.local_entry_guards import local_ai_kwargs
from backend.core.local_request_scope import agent_access_status
from backend.core.speaker_diarization import diarization_status

_BROWSERS = {
    "chrome": "Chrome", "edge": "Edge", "firefox": "Firefox", "safari": "Safari", "brave": "Brave",
    "chromium": "Chromium", "opera": "Opera", "vivaldi": "Vivaldi",
}
_PRESETS = {"quality": ("画质优先", "picture first"), "speed": ("速度优先", "speed first"), "size": ("省空间", "save space")}
_CPU = {
    "light": ("省着用电脑", "light on the computer"),
    "balanced": ("给电脑留出余量", "leaves the computer room"),
    "full": ("电脑占用不限制", "no limit on the computer"),
}
_LARK_ROUTES = {
    "auto": ("自动选择导出方式", "export route chosen automatically"),
    "local_cli": ("以你本人的飞书身份导出", "exported as yourself"),
    "openapi": ("用你的飞书应用导出", "exported with your Feishu app"),
}


def _t(zh: str, en: str) -> dict[str, str]:
    return {"zh": zh, "en": en}


def _flag(choices: Mapping[str, Any], name: str) -> bool:
    return str(choices.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _provider_label(provider: str) -> str:
    entry = text_providers.PROVIDERS.get(provider)
    return entry.label if entry else provider


def _claude_who(channel_name: str) -> tuple[str, str]:
    if channel_name == visual_note_channel.CHANNEL_SUBSCRIPTION:
        return "Claude（本机已登录的订阅）", "Claude (the subscription signed in on this machine)"
    return "Claude（你的 Anthropic API Key）", "Claude (your Anthropic API key)"


def _download_login() -> str:
    """Whose login a link download carries, as the download decides it: the
    remembered browser (routers/local_video_sources._video_cookies_browser),
    else ``YT_DLP_COOKIES_FROM_BROWSER``, else a cookie file
    (video_source._yt_dlp_cookies_args). '' for none."""
    remembered = normalize_cookie_browser(get_preference("video_cookies_browser"))
    if remembered:
        return _BROWSERS.get(remembered, remembered)
    env_browser = (os.environ.get("YT_DLP_COOKIES_FROM_BROWSER") or "").strip()
    if env_browser:
        name = env_browser.split(":", 1)[0].split("+", 1)[0].strip().lower()
        return _BROWSERS.get(name, name)
    cookie_file = (os.environ.get("YT_DLP_COOKIES_FILE") or "").strip()
    if cookie_file and os.path.isfile(cookie_file):
        return "cookie file"
    return ""


def _intake(choices: Mapping[str, Any], for_agent: bool) -> dict[str, Any]:
    browser = _download_login()
    if browser == "cookie file":
        zh, en = "下载链接时带上 cookie 文件里的登录", "Links download with the login in the cookie file"
    elif browser:
        zh, en = f"下载链接时借用 {browser} 的登录状态", f"Links download with {browser}'s login"
    else:
        zh, en = "下载链接时不借用浏览器登录，B 站最高约 480p", "Links download without a browser login (Bilibili up to about 480p)"
    if not for_agent:
        upload = str(choices.get("default_source") or "") == "upload"
        zh += f"；首页默认停在「{'本机文件' if upload else '链接'}」"
        en += f"; the start page opens on {'local files' if upload else 'a link'}"
    return {"id": "intake", "title": _t("拿到素材", "Getting material"), "summary": _t(zh + "。", en + "."), "level": "ok"}


def _cutting(for_agent: bool) -> dict[str, Any]:
    preset = _PRESETS.get(device_profile.preset(), _PRESETS["quality"])
    cpu = _CPU.get(device_profile.cpu_usage(), _CPU["balanced"])
    tail = (f"；{preset[0]}，{cpu[0]}。", f"; {preset[1]}, {cpu[1]}.")
    # The order processing follows (local_intake_flow): no automatic cut at
    # all, or a cut by loudness before anything reads the recording, or the
    # model's reading of the whole transcript.
    if not local_intake_flow.cut_first_enabled():
        return {"id": "cutting", "title": _t("剪辑", "Cutting"), "level": "ok",
                "judge": "none",
                "summary": _t("不自动剪空白，转录和笔记都用原片" + tail[0],
                              "Gaps are not cut automatically; transcript and note use the original" + tail[1])}
    if not local_intake_flow.survey_first_enabled():
        return {"id": "cutting", "title": _t("剪辑", "Cutting"), "level": "ok",
                "judge": "loudness",
                "summary": _t("转录前先按音量剪掉空白，不用模型判断" + tail[0],
                              "Gaps are cut by loudness before transcription, without a model" + tail[1])}
    judge = cut_judge.choose()
    name = judge.name if judge else ""
    kind = ("claude" if name in (visual_note_channel.CHANNEL_SUBSCRIPTION, visual_note_channel.CHANNEL_ANTHROPIC_KEY)
            else "vision" if name.startswith("vision:") else "text_model" if name == "text_model"
            else "agent" if for_agent else "loudness")
    if name in (visual_note_channel.CHANNEL_SUBSCRIPTION, visual_note_channel.CHANNEL_ANTHROPIC_KEY):
        who_zh, who_en = _claude_who(name)
        who = (f"{who_zh}判断剪哪里，会看截图", f"{who_en} decides the cuts, looking at screenshots")
    elif name.startswith("vision:"):
        model = name.split(":", 1)[1]
        who = (f"看图模型 {model} 判断剪哪里，会看截图", f"The picture model {model} decides the cuts, looking at screenshots")
    elif name == "text_model":
        label = _provider_label(str(local_ai_kwargs().get("provider") or ""))
        who = (f"{label} 只按转录稿判断剪哪里", f"{label} decides the cuts from the transcript only")
    elif for_agent:
        who = ("FluentFlow 没有判断模型，由提交任务的 agent 判断剪哪里", "FluentFlow has no model, so the agent that submits decides the cuts")
    else:
        who = ("没有判断模型，只按音量剪", "No model to decide, so cuts follow loudness only")
    return {
        "id": "cutting",
        "title": _t("剪辑", "Cutting"),
        "summary": _t(who[0] + tail[0], who[1] + tail[1]),
        "level": "ok",
        "judge": kind,
    }


def _transcription(choices: Mapping[str, Any], attention: list[dict[str, Any]]) -> dict[str, Any]:
    wants_speakers = bool(get_preference("speaker_diarization"))
    status = diarization_status()
    ready = bool(status.get("available"))
    broken = wants_speakers and not ready
    if broken:
        attention.append({
            "id": "speakers",
            "target": "transcription",
            "action": "turn_off_speakers",
            "text": _t("开了区分讲话人，但这台电脑上的组件没装好，现在不会标出谁在说话。",
                       "Speaker separation is on, but its component is missing, so speakers are not labelled."),
        })
    speakers = wants_speakers and ready
    voice = _flag(choices, "voice_enhance")
    zh = f"在这台电脑上转录；{'标出谁在说话' if speakers else '不区分讲话人'}{'，先把人声处理清楚' if voice else ''}。"
    en = f"Transcribed on this computer; {'speakers labelled' if speakers else 'no speaker labels'}{', voice cleaned up first' if voice else ''}."
    return {"id": "transcription", "title": _t("转录", "Transcription"), "summary": _t(zh, en),
            "level": "attention" if broken else "ok"}


def _notes(choices: Mapping[str, Any], for_agent: bool, attention: list[dict[str, Any]]) -> dict[str, Any]:
    level = "ok"
    channel = visual_note_channel.resolve_channel(resolve_secret(None, "anthropic_api_key"))
    if channel.name == visual_note_channel.CHANNEL_SUBSCRIPTION and claude_code_note.cli_available() \
            and claude_code_note.login_state() is False:
        level = "attention"
        attention.append({
            "id": "claude-login",
            "target": "notes",
            "text": _t("本机 Claude 的登录已过期，结合画面的笔记暂时写不了：打开终端运行 claude 重新登录。",
                       "Claude on this machine is logged out, so notes from the frames cannot be written: run claude in a terminal to log in."),
        })
    # No provider named: the saved one, as for any task that names none.
    kwargs = local_ai_kwargs()
    provider = str(kwargs.get("provider") or "")
    # Transcript only wins over everything, as in processing: the job records
    # note_declined and no writer is asked (decision 1.1).
    writer = "none"
    if _flag(choices, "skip_ai_summary"):
        writer = "skipped"
        summary = _t("只转录，不写笔记。", "Transcript only; no note.")
    elif local_intake_flow.auto_note_enabled() and channel.available:
        writer = "claude"
        who_zh, who_en = _claude_who(channel.name)
        summary = _t(f"{who_zh}结合画面写，照你的笔记风格。", f"{who_en} writes from the frames, in your note style.")
    elif kwargs.get("api_key"):
        writer = "text_model"
        label = _provider_label(provider)
        model = str(kwargs.get("model") or "")
        name = f"{label}（{model}）" if model else label
        summary = _t(f"{name}只按转录稿写，不看画面。", f"{label}{f' ({model})' if model else ''} writes from the transcript only, without the frames.")
        saved = str(get_preference("ai_provider") or "").strip().lower()
        if saved and saved != provider:
            # The saved provider has no key, so another one writes (decision 6.31).
            summary = _t(
                f"设置里存的是 {_provider_label(saved)}，它没有 Key，现在由{summary['zh']}",
                f"{_provider_label(saved)} is saved but has no key, so {summary['en'][0].lower()}{summary['en'][1:]}",
            )
    else:
        level = "attention"
        summary = _t("没有能写笔记的模型，只出转录稿。", "Nothing can write a note; only the transcript is produced.")
        if not for_agent:
            attention.append({
                "id": "no-writer",
                "target": "accounts",
                "text": _t("现在只出转录稿：把笔记交给你的 agent 写，或在「账号和 Key」里填一个 Key。",
                           "Only transcripts for now: let your agent write notes, or add a key under Accounts and keys."),
            })
    # `writer` and `claude_channel` let Settings show only the options that
    # change something: the frame-note style when Claude writes, the
    # Anthropic key as unused while the subscription is the channel.
    return {"id": "notes", "title": _t("写笔记", "Notes"), "summary": summary, "level": level,
            "writer": writer,
            "claude_channel": ("" if not channel.available
                               else "subscription" if channel.name == visual_note_channel.CHANNEL_SUBSCRIPTION
                               else "anthropic_api_key")}


# `lark-cli auth status` is a subprocess; Settings reads this list every few
# seconds, so its answer is reused for a minute.
_LARK_CLI_TTL = 60.0
_lark_cli_cache: tuple[float, bool] | None = None


def _lark_cli_ready() -> bool:
    global _lark_cli_cache
    now = time.monotonic()
    if _lark_cli_cache is None or now - _lark_cli_cache[0] > _LARK_CLI_TTL:
        _lark_cli_cache = (now, bool(lark_cli_ready()))
    return _lark_cli_cache[1]


def _outputs(choices: Mapping[str, Any], attention: list[dict[str, Any]]) -> dict[str, Any]:
    route_name = str(get_preference("lark_export_route") or "auto").strip().lower()
    route = _LARK_ROUTES.get(route_name, _LARK_ROUTES["auto"])
    auto = bool(get_preference("export_to_lark"))
    zh = f"产出放在原片旁边的同名文件夹；{'处理完自动导出到飞书' if auto else '导出到飞书要手动点'}，{route[0]}。"
    en = f"Outputs go in a folder beside the recording; {'exported to Feishu automatically' if auto else 'Feishu export is manual'}, {route[1]}."
    level = "ok"
    # What the chosen route needs, as the export resolves it
    # (routers/local_feishu_export._local_lark_export_target).
    app_ready = bool(resolve_secret(None, "lark_app_id") and resolve_secret(None, "lark_app_secret"))
    if route_name == "openapi" and not app_ready:
        level = "attention"
        attention.append({"id": "lark-app", "target": "accounts", "text": _t(
            "选了用飞书应用导出，但还没填飞书应用 ID 和密钥，导出会失败。",
            "Feishu export uses your app, but its ID and secret are missing, so exports fail.")})
    elif route_name not in ("openapi", "local_cli") and not app_ready and not _lark_cli_ready():
        level = "attention"
        attention.append({"id": "lark-app", "target": "accounts", "text": _t(
            "导出到飞书还用不了：这台电脑的 lark-cli 没登录，也没填飞书应用 ID 和密钥。",
            "Feishu export cannot work yet: lark-cli is not signed in here and no Feishu app is set up.")})
    return {"id": "outputs", "title": _t("产出和同步", "Outputs"), "summary": _t(zh, en), "level": level}


def _agent() -> dict[str, Any]:
    status = agent_access_status()
    if not status["token_configured"]:
        summary = _t("还连不上：这台电脑没有设访问令牌，重新运行一次安装脚本会写好。",
                     "Cannot connect yet: no access token is set on this computer; running the installer again writes one.")
    elif not status["last_agent_request_at"]:
        summary = _t("这次启动 FluentFlow 后还没有 agent 调用过；已经接入过的不用重新接。",
                     "No agent has called FluentFlow since it started; one already set up needs nothing more.")
    else:
        summary = _t("这次启动后有 agent 调用过。", "An agent has called FluentFlow since it started.")
    return {"id": "agent", "title": _t("你的 agent", "Your agent"), "summary": summary, "level": "ok",
            "last_agent_request_at": status["last_agent_request_at"]}


def workflow_status(choices: Mapping[str, Any] | None = None, *, for_agent: bool = False) -> dict[str, Any]:
    # The service loads .env at startup; a caller that has not (a script, a
    # test) would otherwise see the cut and the note pick different channels.
    load_project_env()
    choices = choices or {}
    attention: list[dict[str, Any]] = []
    steps = [
        _intake(choices, for_agent),
        _cutting(for_agent),
        _transcription(choices, attention),
        _notes(choices, for_agent, attention),
        _outputs(choices, attention),
        _agent(),
    ]
    return {"attention": attention, "steps": steps}


__all__ = ["workflow_status"]
