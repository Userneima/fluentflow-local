"""Speaker diarization on this machine.

This module intentionally avoids heuristic speaker guessing. Speaker labels are
only produced when a real backend is installed with its models:

- ``sherpa`` (default, installed with the app): sherpa-onnx with two small ONNX
  models. Decision log 4.1 measured it against pyannote: the same answers on
  real meetings at clustering threshold 1.0, 6 to 20 times faster, no PyTorch.
- ``pyannote`` (optional, ``FLUENTFLOW_SPEAKER_BACKEND=pyannote``): more
  accurate on hard meetings, slower, about 900 MB more to install; see
  `backend.core.speaker_diarization_pyannote`. When it is chosen but cannot
  run, sherpa runs instead and the status says why.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from backend.core import speaker_diarization_pyannote as pyannote_backend
from backend.core.runtime_paths import default_diarization_model_dir
from backend.core.speaker_turns import SpeakerTurn

BACKEND_ENV = "FLUENTFLOW_SPEAKER_BACKEND"
BACKEND_SHERPA = "sherpa"
BACKEND_PYANNOTE = "pyannote"


SEGMENTATION_MODEL_FILENAME = "segmentation.onnx"
EMBEDDING_MODEL_FILENAME = "embedding.onnx"

SAMPLE_RATE = 16000
# Measured in decision log 4.1: 1.0 matched pyannote across meetings and a
# lecture; 1.05 and up merged distinct people in larger meetings.
CLUSTERING_THRESHOLD = 1.0
MIN_DURATION_ON = 0.3
MIN_DURATION_OFF = 0.5
MAX_THREADS = 4
# A cluster with less speaking time than this is almost always a stray split of
# someone already present, not a new person.
SMALL_SPEAKER_SECONDS = 8.0
# A small speaker is folded into an established one only when their voices are
# at least this similar (cosine of mean embeddings). Measured 2026-10-15 on three
# meetings: a stray piece of an established speaker scored 0.57 and 0.76 against
# that speaker; two real people who only interjected a line scored 0.21 and 0.41
# against their nearest neighbour, and distinct established speakers scored 0.09
# to 0.49 against each other. Folding every small speaker regardless swallowed
# both real interjections.
MERGE_MIN_SIMILARITY = 0.5
# How much audio of one speaker goes into the voice average used to place the
# small ones. Their longest turns are enough to characterise a voice, and
# re-embedding a speaker's whole hour would double the work for nothing.
_REFERENCE_SECONDS_PER_SPEAKER = 120.0
# Turns shorter than this give an embedding too noisy to average in.
_MIN_EMBEDDING_TURN_SECONDS = 0.5


def model_paths() -> tuple[Path, Path]:
    """The segmentation and embedding model files, present or not."""
    model_dir = default_diarization_model_dir()
    return model_dir / SEGMENTATION_MODEL_FILENAME, model_dir / EMBEDDING_MODEL_FILENAME


def models_present() -> bool:
    return all(path.is_file() and path.stat().st_size > 0 for path in model_paths())


def _sherpa_installed() -> bool:
    try:
        return importlib.util.find_spec("sherpa_onnx") is not None
    except (ImportError, ValueError):
        return False


def requested_backend() -> str:
    """The backend the user chose; anything but ``pyannote`` means sherpa."""
    value = (os.environ.get(BACKEND_ENV) or "").strip().lower()
    return BACKEND_PYANNOTE if value == BACKEND_PYANNOTE else BACKEND_SHERPA


def sherpa_status() -> dict[str, Any]:
    installed = _sherpa_installed()
    has_models = models_present()
    return {
        "available": installed and has_models,
        "backend": "sherpa-onnx",
        "dependency_installed": installed,
        "models_local": has_models,
        "model_dir": str(default_diarization_model_dir()),
    }


def diarization_status() -> dict[str, Any]:
    """What will run when speaker separation is asked for, and why.

    ``backend`` is the backend that will actually run. ``requested_backend`` is
    the user's choice; when the two differ, ``fallback_reason`` says why in
    Chinese, for the result and the settings page.
    """
    requested = requested_backend()
    status = sherpa_status()
    fallback_reason = None
    pyannote_detail = None
    if requested == BACKEND_PYANNOTE:
        pyannote_detail = pyannote_backend.pyannote_status()
        if pyannote_detail["available"]:
            status = {**pyannote_detail, "model_dir": str(pyannote_backend.default_pyannote_model_dir())}
        elif not pyannote_detail["dependency_installed"]:
            fallback_reason = "设置里选了 pyannote，但这台电脑没装 pyannote.audio，这次改用默认的 sherpa-onnx。"
        else:
            fallback_reason = (
                "设置里选了 pyannote，但它的模型文件和 Hugging Face 令牌都没有，这次改用默认的 sherpa-onnx。"
            )
    return {
        **status,
        "requested_backend": requested,
        "fallback_reason": fallback_reason,
        "pyannote": pyannote_detail,
    }


def default_on_for_agents() -> bool:
    """Whether a caller with no page (Agent API, MCP, curl) gets speaker
    separation without asking for it: no.

    Off by default everywhere (2026-10-15, product owner): it runs only when the
    user turns it on or the caller passes ``speaker_diarization=true``. The
    bundled agent skill suggests turning it on for a recording that turns out
    to be a conversation among several people.
    """
    return False


def decode_audio(audio_path: str | Path) -> Any:
    """Any audio or video file as 16 kHz mono float32 samples, via ffmpeg."""
    import numpy as np

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH; it is required for speaker diarization")
    completed = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", str(audio_path),
            "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-",
        ],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg could not decode {audio_path}: {detail[-500:]}")
    return np.frombuffer(completed.stdout, dtype=np.float32).copy()


def _thread_count() -> int:
    try:
        from backend.core.device_profile import cpu_thread_budget

        budget = cpu_thread_budget()
    except Exception:  # noqa: BLE001 - a missing profile must not block diarization
        budget = None
    return max(1, min(MAX_THREADS, budget or MAX_THREADS))


def diarize_audio(audio_path: str | Path) -> list[SpeakerTurn]:
    status = diarization_status()
    if status.get("backend") == BACKEND_PYANNOTE:
        return pyannote_backend.diarize_with_pyannote(audio_path)
    return diarize_with_sherpa(audio_path)


def diarize_with_sherpa(audio_path: str | Path) -> list[SpeakerTurn]:
    status = sherpa_status()
    if not status["available"]:
        if not status["dependency_installed"]:
            raise RuntimeError("Speaker diarization needs the sherpa-onnx package (pip install sherpa-onnx)")
        raise RuntimeError(
            "Speaker diarization model files are missing; run scripts/fetch_diarization_models.py"
        )

    import sherpa_onnx  # type: ignore

    segmentation_model, embedding_model = model_paths()
    threads = _thread_count()
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(segmentation_model)),
            num_threads=threads,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(embedding_model), num_threads=threads),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=CLUSTERING_THRESHOLD),
        min_duration_on=MIN_DURATION_ON,
        min_duration_off=MIN_DURATION_OFF,
    )
    if not config.validate():
        raise RuntimeError(f"Speaker diarization model files in {segmentation_model.parent} are not usable")

    samples = decode_audio(audio_path)
    if samples.size == 0:
        return []
    diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    result = diarizer.process(samples).sort_by_start_time()
    turns = [
        SpeakerTurn(start=float(item.start), end=float(item.end), speaker=f"SPEAKER_{int(item.speaker):02d}")
        for item in result
    ]
    if not small_speakers(turns):
        return turns
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(embedding_model), num_threads=threads)
    )
    embeddings = speaker_embeddings(extractor, samples, turns)
    return merge_small_speakers(turns, embeddings)


def speaking_seconds(turns: Iterable[SpeakerTurn]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for turn in turns:
        totals[turn.speaker] = totals.get(turn.speaker, 0.0) + max(0.0, turn.end - turn.start)
    return totals


def small_speakers(turns: Iterable[SpeakerTurn], min_seconds: float = SMALL_SPEAKER_SECONDS) -> set[str]:
    return {speaker for speaker, seconds in speaking_seconds(turns).items() if seconds < min_seconds}


def speaker_embeddings(extractor: Any, samples: Any, turns: Sequence[SpeakerTurn]) -> dict[str, Any]:
    """Mean, length-normalised voice embedding per speaker over its turns.

    Small speakers use every turn they have; the others use their longest turns
    up to ``_REFERENCE_SECONDS_PER_SPEAKER``.
    """
    import numpy as np

    small = small_speakers(turns)
    by_speaker: dict[str, list[SpeakerTurn]] = {}
    for turn in turns:
        by_speaker.setdefault(turn.speaker, []).append(turn)

    output: dict[str, Any] = {}
    for speaker, speaker_turns in by_speaker.items():
        ordered = sorted(speaker_turns, key=lambda t: t.end - t.start, reverse=True)
        usable = [t for t in ordered if t.end - t.start >= _MIN_EMBEDDING_TURN_SECONDS] or ordered[:1]
        if speaker not in small:
            chosen: list[SpeakerTurn] = []
            total = 0.0
            for turn in usable:
                if total >= _REFERENCE_SECONDS_PER_SPEAKER:
                    break
                chosen.append(turn)
                total += turn.end - turn.start
            usable = chosen
        vectors = []
        for turn in usable:
            chunk = samples[int(turn.start * SAMPLE_RATE): int(turn.end * SAMPLE_RATE)]
            if chunk.size == 0:
                continue
            stream = extractor.create_stream()
            stream.accept_waveform(SAMPLE_RATE, chunk)
            stream.input_finished()
            if not extractor.is_ready(stream):
                continue
            vector = np.asarray(extractor.compute(stream), dtype=np.float32)
            norm = float(np.linalg.norm(vector))
            if norm > 0:
                vectors.append(vector / norm)
        if vectors:
            output[speaker] = np.mean(vectors, axis=0)
    return output


def _cosine(a: Any, b: Any) -> float:
    import numpy as np

    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denominator) if denominator > 0 else -1.0


def merge_small_speakers(
    turns: Sequence[SpeakerTurn],
    embeddings: Mapping[str, Any],
    min_seconds: float = SMALL_SPEAKER_SECONDS,
    min_similarity: float = MERGE_MIN_SIMILARITY,
) -> list[SpeakerTurn]:
    """Fold each speaker with under ``min_seconds`` of speech into the
    established speaker whose voice is most similar, when it is similar enough.

    Similarity is the cosine of mean voice embeddings, never position: merging
    a stray cluster into the neighbouring turn's speaker measured lower
    agreement with pyannote (decision log 4.1). A small speaker whose best match
    is under ``min_similarity`` is a person who said little, and stays a
    speaker of its own. When every speaker is small there is nothing
    established to merge into, and a speaker without an embedding stays as it is.
    """
    totals = speaking_seconds(turns)
    small = {speaker for speaker, seconds in totals.items() if seconds < min_seconds}
    established = [speaker for speaker in totals if speaker not in small and speaker in embeddings]
    if not small or not established:
        return list(turns)
    target: dict[str, str] = {}
    for speaker in small:
        if speaker not in embeddings:
            continue
        best = max(established, key=lambda other: _cosine(embeddings[speaker], embeddings[other]))
        if _cosine(embeddings[speaker], embeddings[best]) >= min_similarity:
            target[speaker] = best
    return [
        SpeakerTurn(start=turn.start, end=turn.end, speaker=target.get(turn.speaker, turn.speaker))
        for turn in turns
    ]


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

    The diarizer and the cloud providers emit ids like ``SPEAKER_00``. Notes and
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
