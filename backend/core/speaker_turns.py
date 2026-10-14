"""The one shape every speaker-diarization backend returns."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: str
