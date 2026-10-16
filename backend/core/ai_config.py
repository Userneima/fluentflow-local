"""Shared config constants for the note/summary pipeline (providers,
default models, mode thresholds). No logic — imported by ai_summarizer,
ai_client, and the other extracted ai_* modules to avoid circular imports."""

from __future__ import annotations

from typing import Final


DEEPSEEK_BASE_URL: Final[str] = "https://api.deepseek.com"
OPENAI_BASE_URL: Final[str] = "https://api.openai.com/v1"
QWEN_BASE_URL: Final[str] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# DeepSeek's name for its current Flash model (V4.1 Flash on 2026-09-10),
# thinking on by default. deepseek-chat and deepseek-reasoner were to be
# discontinued on 2026-07-24 (DeepSeek's change log, 2026-04-24), and the
# deepseek-v4-flash names are only "temporarily routed" to V4.1 Flash; a name
# kept from any of them is sent as this one (``RETIRED_DEEPSEEK_MODELS``).
DEFAULT_DEEPSEEK_MODEL: Final[str] = "deepseek-flash"
RETIRED_DEEPSEEK_MODELS: Final[frozenset[str]] = frozenset({
    "deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp",
})
DEFAULT_OPENAI_MODEL: Final[str] = "gpt-5.4-mini"
# Qwen's own name for its current Plus model, so the default moves with it.
DEFAULT_QWEN_MODEL: Final[str] = "qwen-plus"
# The Qwen default above is a TEXT model. Frame selection must use a vision
# (multimodal) Qwen model, or images sent to a text model fail and the whole
# visual-evidence step reports "unavailable". qwen-vl-plus is the cheaper vision
# tier; override with QWEN_VISION_MODEL (e.g. qwen-vl-max) if needed.
DEFAULT_QWEN_VISION_MODEL: Final[str] = "qwen-vl-plus"
DEFAULT_MODEL: Final[str] = DEFAULT_DEEPSEEK_MODEL
SUPPORTED_PROVIDERS: Final[set[str]] = {"deepseek", "openai", "qwen"}
SUPPORTED_NOTE_MODES: Final[set[str]] = {"auto", "direct", "fast", "high_fidelity", "chapter_coverage"}
CHAPTER_COVERAGE_VERSION: Final[str] = "1"
DIRECT_MODE_MAX_CHARS: Final[int] = 20_000
HIGH_FIDELITY_NOTICE_CHARS: Final[int] = 60_000
