"""Conservative model-backed transcript correction helpers."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Final

from backend.core.ai_summarizer import (
    DEFAULT_DEEPSEEK_MODEL,
    _chat,
    _extract_json_object,
    _get_client,
    _normalize_model,
    _normalize_provider,
)
from backend.core.result_schema import sanitize_raw_segments


TRANSCRIPT_CORRECTION_VERSION: Final[str] = "1"
MIN_CORRECTION_CONFIDENCE: Final[float] = 0.85
MAX_CORRECTION_CHUNK_CHARS: Final[int] = 9_000

_TRANSCRIPT_CORRECTION_SYSTEM: Final[str] = """# Role: FluentFlow 保守字幕校对员

你会收到课程/讲座字幕片段。你的任务不是润色，也不是重写字幕，而是找出“高置信”的明显转录错误。

必须遵守：
- 只修正高度确定的常识性/上下文转录错误，例如同音错词、术语错听、明显断裂的专有名词。
- 不确定就不要改。
- 不要补充原文没有的信息，不要扩写，不要改变说话人的意思。
- 不要为了更顺、更书面、更完整而改写。
- 每条 correction 的 original_text 必须是输入中对应 segment 的完整 text；corrected_text 也必须是完整 segment text，只做最小必要替换。
- confidence 必须是 0 到 1 的数字；只有非常确定才给 0.85 以上。

只输出 JSON 对象，不要解释：
{
  "corrections": [
    {
      "segment_index": 0,
      "original_text": "输入片段完整原文",
      "corrected_text": "最小修正后的完整片段",
      "reason": "简短说明为什么这是明显转录错误",
      "confidence": 0.93
    }
  ]
}

如果没有高置信修正，输出 {"corrections": []}。"""


def transcript_correction_enabled() -> bool:
    raw = (
        os.environ.get("FLUENTFLOW_TRANSCRIPT_CORRECTION_ENABLED")
        or os.environ.get("FLUENTFLOW_TRANSCRIPT_CORRECTION")
        or ""
    )
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


@dataclass(frozen=True)
class TranscriptCorrectionResult:
    status: str
    corrections: list[dict[str, Any]]
    corrected_segments: list[dict[str, Any]]
    corrected_text: str
    provider: str | None
    model: str | None
    segment_count: int
    rejected_count: int = 0
    error: str | None = None

    @property
    def applied_count(self) -> int:
        return len(self.corrections)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _indexed_chunks(segments: list[dict[str, Any]], max_chars: int) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for index, segment in enumerate(segments):
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        item = {
            "segment_index": index,
            "start": segment.get("start"),
            "end": segment.get("end"),
            "text": text,
        }
        size = len(text) + 80
        if current and current_chars + size > max_chars:
            chunks.append(current)
            current = [item]
            current_chars = size
        else:
            current.append(item)
            current_chars += size
    if current:
        chunks.append(current)
    return chunks


def _proposal_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("corrections")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _apply_proposals(
    segments: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    *,
    min_confidence: float,
    provider: str,
    model: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    corrected_segments = [dict(segment) for segment in segments]
    corrections: list[dict[str, Any]] = []
    rejected_count = 0
    seen_indices: set[int] = set()
    for proposal in proposals:
        try:
            index = int(proposal.get("segment_index"))
        except (TypeError, ValueError):
            rejected_count += 1
            continue
        if index < 0 or index >= len(segments) or index in seen_indices:
            rejected_count += 1
            continue
        confidence = _confidence(proposal.get("confidence"))
        if confidence < min_confidence:
            rejected_count += 1
            continue
        original_text = str(proposal.get("original_text") or "").strip()
        corrected_text = str(proposal.get("corrected_text") or "").strip()
        segment_text = str(segments[index].get("text") or "").strip()
        if not corrected_text or _normalize_text(corrected_text) == _normalize_text(segment_text):
            rejected_count += 1
            continue
        if _normalize_text(original_text) != _normalize_text(segment_text):
            rejected_count += 1
            continue
        if len(corrected_text) > max(240, int(len(segment_text) * 1.8) + 40):
            rejected_count += 1
            continue
        corrected_segments[index]["text"] = corrected_text
        correction = {
            "segment_index": index,
            "start": segments[index].get("start"),
            "end": segments[index].get("end"),
            "original_text": segment_text,
            "corrected_text": corrected_text,
            "reason": str(proposal.get("reason") or "").strip(),
            "confidence": round(confidence, 3),
            "provider": provider,
            "model": model,
        }
        corrections.append({key: value for key, value in correction.items() if value not in (None, "")})
        seen_indices.add(index)
    return corrected_segments, corrections, rejected_count


def correct_transcript_segments(
    segments: list[dict[str, Any]],
    *,
    api_key: str | None,
    model: str | None = None,
    provider: str | None = "deepseek",
    min_confidence: float = MIN_CORRECTION_CONFIDENCE,
    max_chunk_chars: int = MAX_CORRECTION_CHUNK_CHARS,
) -> TranscriptCorrectionResult:
    source_segments = sanitize_raw_segments(segments)
    provider_name = _normalize_provider(provider or "deepseek")
    model_name = _normalize_model(provider_name, model or DEFAULT_DEEPSEEK_MODEL)
    if not source_segments:
        return TranscriptCorrectionResult(
            status="unavailable",
            corrections=[],
            corrected_segments=[],
            corrected_text="",
            provider=provider_name,
            model=model_name,
            segment_count=0,
            error="No transcript segments available for correction.",
        )
    if not (api_key or "").strip():
        return TranscriptCorrectionResult(
            status="unavailable",
            corrections=[],
            corrected_segments=[],
            corrected_text="",
            provider=provider_name,
            model=model_name,
            segment_count=len(source_segments),
            error="DeepSeek API Key is not configured for transcript correction.",
        )
    try:
        client = _get_client(provider=provider_name, api_key=api_key)
        proposals: list[dict[str, Any]] = []
        for chunk in _indexed_chunks(source_segments, max_chunk_chars):
            user = json.dumps(
                {
                    "task": "conservative_transcript_correction",
                    "material_type": "course_or_lecture",
                    "segments": chunk,
                },
                ensure_ascii=False,
            )
            payload = _extract_json_object(_chat(client, model_name, _TRANSCRIPT_CORRECTION_SYSTEM, user, temperature=0.1))
            proposals.extend(_proposal_items(payload))
        corrected_segments, corrections, rejected_count = _apply_proposals(
            source_segments,
            proposals,
            min_confidence=min_confidence,
            provider=provider_name,
            model=model_name,
        )
        corrected_text = "\n".join(str(segment.get("text") or "").strip() for segment in corrected_segments if str(segment.get("text") or "").strip())
        status = "completed" if corrections else "no_changes"
        return TranscriptCorrectionResult(
            status=status,
            corrections=corrections,
            corrected_segments=corrected_segments if corrections else [],
            corrected_text=corrected_text if corrections else "",
            provider=provider_name,
            model=model_name,
            segment_count=len(source_segments),
            rejected_count=rejected_count,
        )
    except Exception as exc:
        return TranscriptCorrectionResult(
            status="failed",
            corrections=[],
            corrected_segments=[],
            corrected_text="",
            provider=provider_name,
            model=model_name,
            segment_count=len(source_segments),
            error=str(exc),
        )


def correction_result_fields(result: TranscriptCorrectionResult, *, note_input_applied: bool = False) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "transcript_correction_version": TRANSCRIPT_CORRECTION_VERSION,
        "status": result.status,
        "provider": result.provider,
        "model": result.model,
        "applied_count": result.applied_count,
        "rejected_count": result.rejected_count,
        "segment_count": result.segment_count,
        "min_confidence": MIN_CORRECTION_CONFIDENCE,
        "note_input_applied": bool(note_input_applied and result.applied_count > 0),
    }
    if result.error:
        meta["error"] = result.error
    fields: dict[str, Any] = {
        "transcript_correction_status": result.status,
        "transcript_correction": {key: value for key, value in meta.items() if value not in (None, "")},
    }
    if result.corrections:
        fields["transcript_corrections"] = result.corrections
        fields["corrected_segments"] = result.corrected_segments
        fields["corrected_transcript_text"] = result.corrected_text
    return fields
