"""The fake model: every exit answers with fixed content, sends nothing.

For test runs that check the flow (restart, stop, export, carry on) rather
than what a model writes (decision 8.11). Turned on for the whole service by
``FLUENTFLOW_FAKE_MODEL=1``. Each exit (``model_usage.call``'s callers) asks
``Call.fake`` before sending; when it is set the exit builds its provider's
reply from ``reply`` instead of making the request, and no command line is
started. The call is still recorded, under the provider ``fake``, at no cost.

``reply`` answers in the shape each request asks for, picked by the part of
the step it works for (``model_usage.item``), so the flow goes on as with a
real model: the cut keeps the loudness plan (the cut video is rendered), the
ending is not trimmed, nothing in the transcript is corrected, the note is a
short fixed text with two sections that can be stored, put in the output
folder and printed. It is not a model and is never used for real work.
"""

from __future__ import annotations

import json
import os
import re
from types import SimpleNamespace
from typing import Any

ENV = "FLUENTFLOW_FAKE_MODEL"
PROVIDER = "fake"
LABEL = "假模型"
MODEL_NOTE = "假模型（测试用），没有看截图，内容是固定的。"


def enabled() -> bool:
    return (os.environ.get(ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


# ── the replies ────────────────────────────────────────────────────────────

NOTE = """# 假模型写的笔记（测试用）

> 这份笔记由 FluentFlow 的假模型生成，用来检查处理流程能不能走完：存笔记、放进产出文件夹、导出 PDF。内容是固定的，和这条录像讲了什么无关。正式使用前要关掉假模型（去掉启动时的 FLUENTFLOW_FAKE_MODEL）。

## 第一部分：流程检查

假模型不发任何网络请求，也不启动 claude 命令行。每次调用照样记进模型用量，服务商写成假模型，不算钱。

- 剪辑：保留按音量找出的停顿，照样导出剪后视频。
- 修正转录稿：不改任何一句。

## 第二部分：怎么关掉

停掉这次启动的服务，启动命令里不带 FLUENTFLOW_FAKE_MODEL，再启动一次。设置页最上面「需要你处理」那一句消失，就是关掉了。
"""

REVIEW_NOTE = """# 假模型写的复习笔记（测试用）

> 假模型生成的固定内容，只用来检查复习笔记能不能写进文件夹。

## 主线

假模型不读底稿，这里没有真的主线。

## 术语表

- 假模型：测试流程用的固定回答。

## 自测题

1. 假模型会发网络请求吗？不会。

## 还没弄清的

无。

## 出处信息

假模型生成，没有出处。
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _report(user: str) -> str:
    refs = re.findall(r"^\[([^\]\s]+)\]", user or "", re.M)
    items = [{"ref": ref, "text": "假模型：照实记下，没有整理。", "unsure": False, "doubt": ""} for ref in refs]
    return _json({"summary": "假模型整理的处理报告（测试用）。",
                  "sections": [{"title": "处理记录（假模型）", "folded": False, "items": items}]})


def _translation(user: str) -> str:
    try:
        chunk = json.loads(user)
    except (TypeError, ValueError):
        return "[]"
    answer = []
    for entry in chunk if isinstance(chunk, list) else []:
        if isinstance(entry, dict) and "index" in entry:
            answer.append({"start_index": entry["index"], "end_index": entry["index"],
                           "text_en": str(entry.get("text") or "").strip() or "(fake)", "text_zh": "（假模型译文）"})
    return _json(answer)


_CLOCK = re.compile(r"^\[(\d+:\d{2}(?::\d{2})?)\]", re.M)


def _section_times(user: str) -> str:
    ids = list(dict.fromkeys(re.findall(r"〔(S\d+)〕", user or "")))
    clocks = _CLOCK.findall(user or "")
    start, end = (clocks[0], clocks[-1]) if clocks else ("0:00:00", "0:00:00")
    return _json({"sections": [{"id": tag, "primary": {"start": start, "end": end}, "others": []} for tag in ids]})


def _revised_note(user: str) -> str:
    found = re.search(r"--- 笔记 ---\s*\n(.*?)\n--- 没写到的知识点 ---", user or "", re.S)
    return found.group(1).strip() if found and found.group(1).strip() else NOTE


def _note(system: str) -> str:
    if "note_markdown" in system:
        return _json({"note_markdown": NOTE, "basis_note": MODEL_NOTE, "people": ""})
    return NOTE


def _shaped(system: str) -> str:
    """A request whose part is not named: by the shape its prompt asks for."""
    if "note_markdown" in system:
        return _note(system)
    if "JSON 数组" in system or "JSON array" in system:
        return "[]"
    if "JSON" in system:
        return "{}"
    return NOTE


def reply(system: str, user: str) -> str:
    """What the fake model answers this request, as text."""
    from backend.core import model_usage

    system, user = system or "", user or ""
    part = model_usage.current().item or ""
    if part == "判断剪哪里":
        return _json({"keep_gaps": [], "remove": []})
    if part == "判断结尾":
        return _json({"ends_at": None, "reason": "假模型：不判断结尾。"})
    if part == "整理处理报告":
        return _report(user)
    if part in ("词表纠错", "画面字幕修正"):
        return _json({"fixes": []})
    if part == "翻译":
        return _translation(user)
    if part == "查漏":
        if "missing_ids" in system:
            return _json({"missing_ids": []})
        return _json({"points": [{"point": "假模型：笔记写到了。", "in_note": True}]})
    if part == "查漏后补写":
        return _revised_note(user)
    if part == "对齐分段时间":
        return _section_times(user)
    if part == "复习笔记":
        return REVIEW_NOTE
    if part == "挑截图":
        return _json({"requests": [], "selections": []})
    if part in ("写正文", "按候选规则试写"):
        return _note(system)
    return _shaped(system)


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(block.get("text") or "") for block in content
                         if isinstance(block, dict) and block.get("type") == "text")
    return ""


def reply_to_messages(messages: Any, system: str = "") -> str:
    """``reply`` for a chat request's messages (OpenAI or Anthropic shape)."""
    users = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "system":
            system = system or _text(message.get("content"))
        elif message.get("role") == "user":
            users.append(_text(message.get("content")))
    return reply(system, "\n".join(users))


# ── each provider's reply, built around it ────────────────────────────────

def openai_response(text: str) -> Any:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
                           usage=None)


def anthropic_message(text: str) -> Any:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", usage=None)


def command_line_result(text: str) -> dict[str, Any]:
    """The subscription command line's result event."""
    return {"type": "result", "subtype": "success", "is_error": False, "result": text}


def command_line_system(command: list[str]) -> str:
    return command[command.index("--system-prompt") + 1] if "--system-prompt" in command[:-1] else ""


def command_line_user(prompt: str) -> str:
    """The prompt the command line would read: plain text, or one JSON user
    message with its content blocks (pictures attached)."""
    try:
        event = json.loads(prompt)
    except (TypeError, ValueError):
        return prompt or ""
    message = event.get("message") if isinstance(event, dict) else None
    return _text(message.get("content")) if isinstance(message, dict) else (prompt or "")


__all__ = [
    "ENV", "LABEL", "PROVIDER", "anthropic_message", "command_line_result", "command_line_system",
    "command_line_user", "enabled", "openai_response", "reply", "reply_to_messages",
]
