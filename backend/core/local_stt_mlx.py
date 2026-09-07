"""Apple-Silicon fast path for local transcription.

faster-whisper cannot reach the GPU on a Mac: its CTranslate2 backend has no
Metal path, so it runs int8 on the CPU cores. On this machine that measures
4.7x realtime against 15.6x for the same `medium` model under MLX, Apple's own
framework — a 3.3x difference on identical audio.

MLX only runs on Apple Silicon, so this is an extra lane rather than a
replacement: `local_stt` picks it when it is available and falls back to
faster-whisper everywhere else, and on any failure here.

The one thing MLX does not bring with it is silence handling, and that matters
more than the speed does. Whisper slices audio into fixed 30-second windows and
runs the model on every one, silent or not; on a silent window it invents text,
and with `condition_on_previous_text` left on that invention becomes context for
the next window, so a single hallucinated line can repeat for minutes. A
recording measured while building this was 47% silence and produced 70
consecutive copies of one word that way. faster-whisper avoids this with a VAD
filter, so the product has never shown the failure — shipping a faster lane
without an equivalent would have traded quality for speed silently.

So the speech regions are found first, with faster-whisper's own VAD (already a
dependency, so both lanes agree on what counts as silence), and only those
regions reach the model, each decoded with a clean context.
"""

from __future__ import annotations

import logging
import platform
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# The product exposes one local model size; these are its MLX equivalents.
_MLX_REPOS: dict[str, str] = {
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
}
_DEFAULT_REPO = _MLX_REPOS["large-v3"]

# Keep a little silence on each side of a speech run so word onsets and trailing
# consonants are not clipped by the cut itself.
_PAD_SECONDS = 0.4
# Runs closer together than this are decoded as one, so a natural pause inside a
# sentence does not become a decode boundary.
_MERGE_GAP_SECONDS = 0.35
# Below this a "run" is a click or a chair, not speech worth a model pass.
_MIN_RUN_SECONDS = 0.5


@dataclass(frozen=True)
class RawSegment:
    """Shaped like a faster-whisper segment so `_collect_segments` can consume it.

    The metadata fields are carried through rather than dropped because
    `local_stt._looks_like_low_confidence_hallucination` reads them; without
    them the fast lane would silently lose a guard the CPU lane has.
    """

    start: float
    end: float
    text: str
    no_speech_prob: float | None = None
    avg_logprob: float | None = None
    compression_ratio: float | None = None
    words: tuple[dict[str, Any], ...] | None = None


@dataclass(frozen=True)
class MlxInfo:
    duration: float | None
    language: str | None
    language_probability: float | None


def unavailable_reason() -> str | None:
    """Return why this lane cannot run here, or None when it can."""
    if platform.system() != "Darwin":
        return f"MLX 只在 macOS 上可用（当前 {platform.system()}）"
    if platform.machine() != "arm64":
        return f"MLX 需要 Apple 芯片（当前 {platform.machine()}）"
    try:
        import mlx_whisper  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - any import failure disqualifies the lane
        return f"mlx-whisper 未安装：{exc}"
    return None


def is_available() -> bool:
    return unavailable_reason() is None


def resolve_repo(model_size: str | None) -> str:
    return _MLX_REPOS.get((model_size or "").strip(), _DEFAULT_REPO)


def _speech_runs(audio, vad_parameters: dict[str, Any] | None) -> list[tuple[float, float]]:
    """Speech regions in seconds, via the same VAD the faster-whisper lane uses."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    options = VadOptions(**(vad_parameters or {}))
    stamps = get_speech_timestamps(audio, options, sampling_rate=SAMPLE_RATE)

    total = len(audio) / SAMPLE_RATE
    # `voiced` tracks the un-padded speech inside each run, and the minimum
    # length is judged on that. Judging the padded span instead let a 0.05s
    # click through: 0.4s of padding on each side lifted it over a 0.5s floor,
    # so the filter passed exactly the input it existed to reject.
    runs: list[list[float]] = []
    for stamp in stamps:
        speech_start = stamp["start"] / SAMPLE_RATE
        speech_end = stamp["end"] / SAMPLE_RATE
        start = max(0.0, speech_start - _PAD_SECONDS)
        end = min(total, speech_end + _PAD_SECONDS)
        if runs and start - runs[-1][1] <= _MERGE_GAP_SECONDS:
            runs[-1][1] = end
            runs[-1][2] += speech_end - speech_start
        else:
            runs.append([start, end, speech_end - speech_start])
    return [(start, end) for start, end, voiced in runs if voiced >= _MIN_RUN_SECONDS]


def transcribe(
    audio_path: str | Path,
    *,
    model_size: str = "large-v3",
    language: str | None = None,
    initial_prompt: str | None = None,
    vad_filter: bool = True,
    vad_parameters: dict[str, Any] | None = None,
    word_timestamps: bool = False,
    on_progress: Callable[[float], None] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> tuple[tuple[RawSegment, ...], MlxInfo, dict[str, Any]]:
    """Transcribe on the Apple GPU, returning faster-whisper-shaped segments.

    `word_timestamps` is off by default because the alignment pass costs real
    time — repeated runs over the same 120s of audio measured 18%, 30% and 40%
    over the same run without it — and nothing in the product reads word timings
    yet. It is a parameter rather than a deletion because the timings cannot be
    recovered later without transcribing again, and callers that want them
    should not have to re-implement this lane to get them.
    """
    import mlx_whisper
    from faster_whisper.audio import decode_audio

    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Audio not found: {path}")

    repo = resolve_repo(model_size)
    if on_status:
        on_status("preparing_audio")
    audio = decode_audio(str(path), sampling_rate=SAMPLE_RATE)
    total_duration = len(audio) / SAMPLE_RATE

    if vad_filter:
        runs = _speech_runs(audio, vad_parameters)
    else:
        runs = [(0.0, total_duration)]
    if not runs:
        logger.info("MLX lane found no speech in %s", path.name)
        return (), MlxInfo(duration=total_duration, language=language, language_probability=None), {
            "engine": "mlx",
            "mlx_repo": repo,
            "speech_seconds": 0.0,
            "speech_runs": 0,
            "model_load_seconds": 0.0,
        }

    voiced = sum(b - a for a, b in runs)
    logger.info(
        "MLX lane: %.1fs of speech in %.1fs of audio across %d runs (%s)",
        voiced, total_duration, len(runs), repo,
    )

    segments: list[RawSegment] = []
    detected_language = language
    started_at = time.perf_counter()
    load_seconds = 0.0
    done = 0.0
    first_seen = False

    for index, (start, end) in enumerate(runs):
        clip = audio[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)]
        result = mlx_whisper.transcribe(
            clip,
            path_or_hf_repo=repo,
            language=language,
            initial_prompt=initial_prompt,
            word_timestamps=word_timestamps,
            # Each run decodes from a clean slate. Carrying context across runs is
            # what lets one bad line repeat for minutes.
            condition_on_previous_text=False,
        )
        if index == 0:
            load_seconds = round(time.perf_counter() - started_at, 3)
        detected_language = detected_language or result.get("language")

        for raw in result.get("segments") or []:
            text = (raw.get("text") or "").strip()
            if not text:
                continue
            if not first_seen:
                first_seen = True
                if on_status:
                    on_status("transcribing_segments")
            words = tuple(
                {
                    "start": round(float(w["start"]) + start, 3),
                    "end": round(float(w["end"]) + start, 3),
                    "text": str(w.get("word") or "").strip(),
                    "conf": w.get("probability"),
                }
                for w in (raw.get("words") or [])
                if w.get("start") is not None and w.get("end") is not None
            )
            segments.append(
                RawSegment(
                    start=round(float(raw["start"]) + start, 3),
                    end=round(float(raw["end"]) + start, 3),
                    text=text,
                    no_speech_prob=raw.get("no_speech_prob"),
                    avg_logprob=raw.get("avg_logprob"),
                    compression_ratio=raw.get("compression_ratio"),
                    words=words or None,
                )
            )

        done += end - start
        if on_progress and voiced > 0:
            on_progress(min(done / voiced, 1.0))

    segments.sort(key=lambda s: s.start)
    info = MlxInfo(
        duration=total_duration,
        language=detected_language,
        language_probability=None,
    )
    stats = {
        "engine": "mlx",
        "mlx_repo": repo,
        "speech_seconds": round(voiced, 2),
        "speech_runs": len(runs),
        "model_load_seconds": load_seconds,
    }
    return tuple(segments), info, stats
