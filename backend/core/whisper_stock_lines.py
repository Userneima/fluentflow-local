"""The lines Whisper writes when no one says them, in one place.

Two kinds, with different rules on purpose:

- Channel credits Whisper learnt from subtitled videos (「请不吝点赞 订阅 转发
  打赏支持明镜与点点栏目」, 「中文字幕志愿者 杨栋梁」, amara.org): no speaker says
  these, so a line that is only a credit is dropped in any language
  (``is_credit_line``; seen twice in one Chinese lecture, 张和 Week 2,
  2026-11-17). A speaker who mentions Amara or subtitle volunteers in a
  longer sentence keeps it.
- Stock lines over a jingle, applause or room noise ("Thank you.",
  「谢谢观看」): people do say these, so one is dropped only from a stretch
  whose detected language is not the recording's (``is_stock_outro``, asked
  by ``local_stt_mlx._without_stray_outros``; seen as a "Thank you." opening a
  Chinese lecture over its intro music).

A new line Whisper invents goes into whichever list's rule fits it.
"""

from __future__ import annotations

import re

# Whole lines, after the punctuation and spaces are taken out and the text is
# lowercased (``local_stt._normalize_for_repeat_filter``).
_CREDIT_LINE_RE = re.compile(
    r"(请不吝点赞订阅转发打赏支持明镜与点点栏目|(中文)?字幕志愿者[一-鿿]{0,4}|字幕由amaraorg社区提供)"
)

_STOCK_OUTROS = frozenset({
    "thank you", "thank you.", "thanks for watching", "thanks for watching!",
    "thank you for watching", "thank you for watching.", "you",
    "谢谢观看",
})


def is_credit_line(normalized: str) -> bool:
    """``normalized``: the line without punctuation or spaces, lowercased."""
    return bool(_CREDIT_LINE_RE.fullmatch(normalized))


def is_stock_outro(text: str) -> bool:
    return text.strip().lower() in _STOCK_OUTROS


__all__ = ["is_credit_line", "is_stock_outro"]
