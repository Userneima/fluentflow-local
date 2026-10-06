"""The persisted queue-options vocabulary: which keys a stored job's
``queue_options`` may carry, so upload, retry and video-link entries cannot
drift apart."""

from __future__ import annotations

from typing import Any


def _queue_options_from_mapping(payload: dict[str, Any] | None) -> dict[str, str]:
    allowed = {
        "export_to_lark",
        "lark_export_route",
        "lark_via_cli",
        "title",
        "folder_token",
        "ai_provider",
        "ai_model",
        "note_mode",
        "skip_summary",
        "generate_visuals",
        "stt_model",
        "stt_speed",
        "stt_language",
        "stt_provider",
        "speaker_diarization",
        "voice_enhance",
        "system_prompt",
        "prompt_preset",
        "prompt_preset_label",
        "duration_limit_seconds",
        "cookies_from_browser",
    }
    result: dict[str, str] = {}
    for key, value in (payload or {}).items():
        if key not in allowed or value is None:
            continue
        text = str(value).strip()
        if text:
            result[key] = text
    return result
