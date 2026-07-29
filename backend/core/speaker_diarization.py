"""Optional speaker diarization integration.

This module intentionally avoids heuristic speaker guessing. Speaker labels are
only produced when a real diarization backend is installed and configured.
"""

from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from backend.core.local_config import get_sensitive_setting


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: str


def diarization_status() -> dict[str, Any]:
    try:
        has_pyannote = importlib.util.find_spec("pyannote.audio") is not None
    except ModuleNotFoundError:
        has_pyannote = False
    has_token = bool(get_sensitive_setting("pyannote_auth_token"))
    return {
        "available": has_pyannote and has_token,
        "backend": "pyannote.audio",
        "dependency_installed": has_pyannote,
        "auth_configured": has_token,
    }


def diarize_audio(audio_path: str | Path) -> list[SpeakerTurn]:
    status = diarization_status()
    if not status["available"]:
        raise RuntimeError("Speaker diarization requires pyannote.audio and PYANNOTE_AUTH_TOKEN")

    from pyannote.audio import Pipeline  # type: ignore

    token = get_sensitive_setting("pyannote_auth_token")
    if not token:
        raise RuntimeError("Speaker diarization requires pyannote.audio and PYANNOTE_AUTH_TOKEN")
    pipeline = _load_pyannote_pipeline(Pipeline, token)
    diarization = pipeline(str(audio_path))
    turns: list[SpeakerTurn] = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        turns.append(SpeakerTurn(start=float(turn.start), end=float(turn.end), speaker=str(speaker)))
    return turns


def _load_pyannote_pipeline(pipeline_cls: Any, token: str) -> Any:
    model = "pyannote/speaker-diarization-3.1"
    try:
        return pipeline_cls.from_pretrained(model, token=token)
    except TypeError as exc:
        if "token" not in str(exc):
            raise
        with _hf_hub_download_auth_compat():
            return pipeline_cls.from_pretrained(model, use_auth_token=token)


@contextmanager
def _hf_hub_download_auth_compat() -> Iterator[None]:
    try:
        import huggingface_hub  # type: ignore
    except Exception:
        yield
        return

    original = getattr(huggingface_hub, "hf_hub_download", None)
    if original is None:
        yield
        return

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if "use_auth_token" in kwargs and "token" not in kwargs:
            kwargs["token"] = kwargs.pop("use_auth_token")
        else:
            kwargs.pop("use_auth_token", None)
        return original(*args, **kwargs)

    patched: list[tuple[Any, Any]] = []
    for module in list(sys.modules.values()):
        if getattr(module, "hf_hub_download", None) is original:
            patched.append((module, original))
            setattr(module, "hf_hub_download", wrapper)
    try:
        yield
    finally:
        for module, previous in patched:
            try:
                setattr(module, "hf_hub_download", previous)
            except Exception:
                pass


def assign_speakers_to_segments(segments: Iterable[Any], turns: Iterable[SpeakerTurn]) -> list[dict[str, Any]]:
    turn_list = list(turns)
    output: list[dict[str, Any]] = []
    for segment in segments:
        start = float(_get(segment, "start", 0.0) or 0.0)
        end = float(_get(segment, "end", start) or start)
        speaker = _speaker_for_span(start, end, turn_list)
        row = {
            "start": start,
            "end": end,
            "text": str(_get(segment, "text", "") or ""),
        }
        if speaker:
            row["speaker"] = speaker
        output.append(row)
    return output


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _speaker_for_span(start: float, end: float, turns: list[SpeakerTurn]) -> str | None:
    best_speaker: str | None = None
    best_overlap = 0.0
    for turn in turns:
        overlap = max(0.0, min(end, turn.end) - max(start, turn.start))
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = turn.speaker
    return best_speaker
