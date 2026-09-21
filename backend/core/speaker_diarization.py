"""Optional speaker diarization integration.

This module intentionally avoids heuristic speaker guessing. Speaker labels are
only produced when a real diarization backend is installed and configured.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from backend.core.local_config import get_sensitive_setting
from backend.core.runtime_paths import default_diarization_model_dir


_TORCH_LOAD_LOCK = threading.Lock()


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: str


def local_model_config() -> Path | None:
    """The locally staged pipeline config, when a usable one is present.

    Preferred over the Hugging Face repo id: on 2026-09-03 this machine reached
    the Hugging Face API fine but got zero bytes from its model CDN, and
    `from_pretrained` neither failed nor finished. Local files remove the
    download from the request path entirely.

    "Usable" means every checkpoint the config points at is really on disk. The
    config alone is not enough to answer that, and treating it as enough is
    worse than having no local models: this directory takes priority over the
    token route, so a half-populated one would report the feature available and
    then fail every run, on a machine where the token would have worked.
    """
    config = default_diarization_model_dir() / "config.yaml"
    if not config.is_file():
        return None
    return config if _referenced_checkpoints_exist(config) else None


def _referenced_checkpoints_exist(config: Path) -> bool:
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        params = ((loaded.get("pipeline") or {}).get("params") or {})
    except Exception:
        # Unreadable or unparseable: fall back to the token route rather than
        # claim a local pipeline that cannot be checked.
        return False
    for value in (params.get("segmentation"), params.get("embedding")):
        text = str(value or "").strip()
        # A bare repo id ("pyannote/segmentation-3.0") is fetched at run time and
        # is not ours to verify; only a path is.
        if not text or not (text.startswith("/") or text.startswith("~")):
            continue
        if not Path(text).expanduser().is_file():
            return False
    return True


def diarization_status() -> dict[str, Any]:
    try:
        has_pyannote = importlib.util.find_spec("pyannote.audio") is not None
    except ModuleNotFoundError:
        has_pyannote = False
    has_token = bool(get_sensitive_setting("pyannote_auth_token"))
    has_local_models = local_model_config() is not None
    return {
        # Local model files need no token: nothing is fetched at run time.
        "available": has_pyannote and (has_local_models or has_token),
        "backend": "pyannote.audio",
        "dependency_installed": has_pyannote,
        "auth_configured": has_token,
        "models_local": has_local_models,
    }


def diarize_audio(audio_path: str | Path) -> list[SpeakerTurn]:
    status = diarization_status()
    if not status["available"]:
        raise RuntimeError(
            "Speaker diarization needs pyannote.audio plus either local model files "
            "(scripts/fetch_diarization_models.py) or PYANNOTE_AUTH_TOKEN"
        )

    from pyannote.audio import Pipeline  # type: ignore

    local_config = local_model_config()
    if local_config is not None:
        pipeline = _load_local_pyannote_pipeline(Pipeline, local_config)
    else:
        token = get_sensitive_setting("pyannote_auth_token")
        if not token:
            raise RuntimeError("Speaker diarization requires pyannote.audio and PYANNOTE_AUTH_TOKEN")
        pipeline = _load_pyannote_pipeline(Pipeline, token)
    diarization = pipeline(str(audio_path))
    turns: list[SpeakerTurn] = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        turns.append(SpeakerTurn(start=float(turn.start), end=float(turn.end), speaker=str(speaker)))
    return turns


def _load_local_pyannote_pipeline(pipeline_cls: Any, config_path: Path) -> Any:
    with _torch_full_load_compat():
        return pipeline_cls.from_pretrained(str(config_path))


@contextmanager
def _torch_full_load_compat() -> Iterator[None]:
    """Let Lightning read pyannote checkpoints on torch >= 2.6.

    Torch 2.6 flipped `torch.load` to `weights_only=True`, and these
    checkpoints carry pickled objects, so loading them raises
    `UnpicklingError`. Torch's own escape hatch is this environment variable;
    it is set only around the load, and only for checkpoints that came from
    pyannote's own published models.
    """
    key = "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"
    # The variable is process-wide, so two overlapping loads would have the
    # first one to finish undo the relaxation the second still needs, failing a
    # run that succeeds when it is the only one. Serialising the loads also
    # keeps the window in which any other thread's `torch.load` would silently
    # lose its unpickling check down to one load at a time.
    with _TORCH_LOAD_LOCK:
        previous = os.environ.get(key)
        os.environ[key] = "1"
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


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


def speaker_display_map(segments: Iterable[Any]) -> dict[str, str]:
    """Map raw diarization ids to reader-facing labels in first-appearance order.

    pyannote and the cloud providers emit ids like ``SPEAKER_00``. Notes and
    transcripts show ``说话人 A`` instead. The mapping is per material, never a
    real name: identifying who a label is remains out of scope (see
    docs/speaker_attribution_plan.md).
    """
    mapping: dict[str, str] = {}
    for segment in segments:
        raw = _get(segment, "speaker")
        if not raw:
            continue
        key = str(raw)
        if key not in mapping:
            mapping[key] = _display_label(len(mapping))
    return mapping


def _display_label(index: int) -> str:
    if index < 26:
        return f"说话人 {chr(ord('A') + index)}"
    return f"说话人 {index + 1}"


def build_speaker_annotated_transcript(segments: Iterable[Any]) -> str | None:
    """Render the note input as speaker-prefixed lines, or None when pointless.

    The note is written from one flat string, so a speaker that only lives in a
    segment field can never reach the writing model. Returns None when fewer
    than two speakers are present: a single-speaker material gains nothing from
    the prefix, and the caller then keeps the plain transcript.
    """
    rows = [
        (str(_get(segment, "speaker") or ""), str(_get(segment, "text", "") or "").strip())
        for segment in segments
    ]
    rows = [(speaker, text) for speaker, text in rows if text]
    mapping = speaker_display_map({"speaker": speaker} for speaker, _ in rows)
    if len(mapping) < 2:
        return None

    lines: list[str] = []
    current_label: str | None = None
    buffer: list[str] = []

    def _flush() -> None:
        if buffer:
            lines.append(f"{current_label}：{' '.join(buffer)}" if current_label else " ".join(buffer))

    for speaker, text in rows:
        label = mapping.get(speaker)
        if label != current_label:
            _flush()
            buffer = []
            current_label = label
        buffer.append(text)
    _flush()
    return "\n".join(lines) or None
