"""Queue-option dict builders for /queue/process, extracted from
server_helpers.py. Pure leaf helpers (typing only) — re-imported by
server_helpers so H._queue_options_* keep working."""

from __future__ import annotations

from typing import Any, Optional


def _queue_options_from_form(
    *,
    export_to_lark: Optional[str],
    lark_export_route: Optional[str],
    lark_via_cli: Optional[str],
    title: Optional[str],
    folder_token: Optional[str],
    deepseek_api_key: Optional[str],
    openai_api_key: Optional[str],
    ai_provider: Optional[str],
    ai_model: Optional[str],
    note_mode: Optional[str],
    skip_summary: Optional[str],
    generate_visuals: Optional[str] = None,
    stt_model: Optional[str],
    stt_speed: Optional[str],
    stt_language: Optional[str],
    stt_provider: Optional[str],
    elevenlabs_api_key: Optional[str],
    speaker_diarization: Optional[str],
    lark_app_id: Optional[str],
    lark_app_secret: Optional[str],
    system_prompt: Optional[str],
    prompt_preset: Optional[str] = None,
    prompt_preset_label: Optional[str] = None,
) -> dict[str, str]:
    raw: dict[str, Optional[str]] = {
        "export_to_lark": export_to_lark,
        "lark_export_route": lark_export_route,
        "lark_via_cli": lark_via_cli,
        "title": title,
        "folder_token": folder_token,
        "ai_provider": ai_provider,
        "ai_model": ai_model,
        "note_mode": note_mode,
        "skip_summary": skip_summary,
        "generate_visuals": generate_visuals,
        "stt_model": stt_model,
        "stt_speed": stt_speed,
        "stt_language": stt_language,
        "stt_provider": stt_provider,
        "speaker_diarization": speaker_diarization,
        "system_prompt": system_prompt,
        "prompt_preset": prompt_preset,
        "prompt_preset_label": prompt_preset_label,
    }
    return {key: value.strip() for key, value in raw.items() if isinstance(value, str) and value.strip()}


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
