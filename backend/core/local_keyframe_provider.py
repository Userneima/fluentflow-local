"""Local FFmpeg keyframe extraction policy."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend.core.frame_extractor import extract_candidate_frames


LocalKeyframeProviderName = Literal["local_ffmpeg", "disabled"]


@dataclass(frozen=True)
class KeyframeExtractionResult:
    provider: str
    frames: list[dict[str, Any]]
    skipped_reason: str | None = None

    @property
    def enabled(self) -> bool:
        return self.provider != "disabled" and not self.skipped_reason


def keyframe_extraction_enabled() -> bool:
    value = os.environ.get("FLUENTFLOW_KEYFRAME_EXTRACTION", "1").strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}


def configured_keyframe_provider() -> LocalKeyframeProviderName:
    if not keyframe_extraction_enabled():
        return "disabled"
    value = os.environ.get("FLUENTFLOW_KEYFRAME_PROVIDER", "local_ffmpeg").strip().lower()
    if value in {"", "local", "ffmpeg", "local_ffmpeg"}:
        return "local_ffmpeg"
    return "disabled"


def extract_keyframes(
    video_path: str,
    output_dir: Path,
    segments: list[dict[str, Any]] | None = None,
    *,
    provider: LocalKeyframeProviderName | None = None,
    scene_threshold: float = 0.3,
    max_scene_frames: int = 30,
    min_gap_seconds: float = 2.0,
    anchor_seconds: list[float] | None = None,
) -> KeyframeExtractionResult:
    selected = provider or configured_keyframe_provider()
    if selected == "disabled":
        return KeyframeExtractionResult(
            provider="disabled", frames=[], skipped_reason="disabled"
        )
    frames = extract_candidate_frames(
        video_path,
        output_dir,
        segments=segments,
        scene_threshold=scene_threshold,
        max_scene_frames=max_scene_frames,
        min_gap_seconds=min_gap_seconds,
        anchor_seconds=anchor_seconds,
    )
    for frame in frames:
        frame.setdefault("provider", "local_ffmpeg")
    return KeyframeExtractionResult(provider="local_ffmpeg", frames=frames)


__all__ = [
    "KeyframeExtractionResult",
    "configured_keyframe_provider",
    "extract_keyframes",
    "keyframe_extraction_enabled",
]
