"""Local speech-to-text with faster-whisper (timestamped segments)."""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from backend.core.local_stt_policy import DEFAULT_LOCAL_STT_MODEL
from backend.core.windows_gpu_runtime import configure_windows_gpu_runtime

# Before the import below, not after: CTranslate2 loads its CUDA libraries when
# faster_whisper imports it, and on Windows those libraries live in this venv,
# in a directory Windows does not search unless it is registered first.
configure_windows_gpu_runtime()

from faster_whisper import WhisperModel  # noqa: E402 - must follow the DLL registration

from backend.core import local_stt_mlx  # noqa: E402

logger = logging.getLogger(__name__)

_LOCAL_MODEL_DIR = Path.home() / ".cache" / "faster-whisper-models"
_ZH_INITIAL_PROMPT = (
    "以下是普通话中文语音转录。请使用简体中文输出，保留必要的英文术语、数字和专有名词。"
)
_MAX_INITIAL_PROMPT_CHARS = 300
_MAX_HOTWORDS_CHARS = 240
_SEGMENT_NORM_RE = re.compile(r"[\s，。！？、,.!?;；:：'\"“”‘’（）()【】\[\]《》<>-]+")
_STT_SPEED_PROFILES: dict[str, dict[str, Any]] = {
    "fast": {
        "beam_size": 1,
        "best_of": 1,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "vad_parameters": {"min_silence_duration_ms": 350},
    },
    "balanced": {
        "beam_size": 3,
        "best_of": 3,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "vad_parameters": {"min_silence_duration_ms": 500},
    },
    "accurate": {
        "beam_size": 5,
        "best_of": 5,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "vad_parameters": {"min_silence_duration_ms": 500},
    },
}

DEFAULT_MODEL_SIZE = DEFAULT_LOCAL_STT_MODEL

# Which local engine runs. "auto" takes the Apple GPU when this machine has it
# and faster-whisper everywhere else; the explicit values exist so a support
# question ("is it actually using the GPU?") can be answered by forcing one lane
# and comparing, and so a bad MLX release can be switched off without a deploy.
LOCAL_STT_ENGINES = ("auto", "mlx", "faster_whisper")
DEFAULT_LOCAL_STT_ENGINE = "auto"
_ENGINE_ALIASES: dict[str, str] = {
    "": "auto",
    "auto": "auto",
    "default": "auto",
    "mlx": "mlx",
    "gpu": "mlx",
    "metal": "mlx",
    "apple": "mlx",
    "cpu": "faster_whisper",
    "faster_whisper": "faster_whisper",
    "faster-whisper": "faster_whisper",
    "ctranslate2": "faster_whisper",
}

# Only the faster-whisper lane reads this: sizes the product no longer offers
# collapse onto ones it does. `large-v3` survives as itself here, and
# `_model_for_device` is what decides whether this lane may actually load it.
_SIZE_ALIASES: dict[str, str] = {
    "tiny": "medium",
    "base": "medium",
    "small": "medium",
    "medium": "medium",
    "large-v2": "large-v3",
    "large-v3": "large-v3",
}


def _canonical_engine(value: str | None) -> str:
    return _ENGINE_ALIASES.get((value or "").strip().lower(), DEFAULT_LOCAL_STT_ENGINE)


def _requested_engine(engine: str | None) -> str:
    """Caller wins, then the environment, then auto."""
    if engine:
        return _canonical_engine(engine)
    return _canonical_engine(os.environ.get("FLUENTFLOW_LOCAL_STT_ENGINE"))


def resolve_local_stt_engine(engine: str | None = None) -> tuple[str, str | None]:
    """Return the lane that will actually run, plus why the fast one was skipped.

    Reported rather than inferred: `device_resolved` from faster-whisper has been
    None on this machine, so "am I on the GPU?" could not be answered from the
    existing metrics at all.
    """
    requested = _requested_engine(engine)
    if requested == "faster_whisper":
        return "faster_whisper", None

    reason = local_stt_mlx.unavailable_reason()
    if reason is None:
        return "mlx", None
    if requested == "mlx":
        # Asked for explicitly: fall back rather than fail the job, but say so.
        logger.warning("MLX 引擎不可用，回退到 faster-whisper：%s", reason)
    return "faster_whisper", reason


def _cuda_is_usable() -> tuple[bool, str | None]:
    """Whether this process can actually reach an NVIDIA GPU, and why not.

    Asked before a model is chosen rather than after it fails to load, because
    the model size, the compute type and the prompt all depend on the answer.
    Two things have to hold and they fail in different ways: the wheel has to
    be built with CUDA (the macOS wheel is not, so a Mac answers no here and
    takes the MLX lane instead), and on Windows the CUDA libraries this venv
    owns have to be findable — missing ones otherwise surface much later as a
    DLL load error from inside CTranslate2.
    """
    runtime = configure_windows_gpu_runtime()
    if runtime.supported_platform and not runtime.ready:
        return False, (
            "Windows NVIDIA runtime is missing "
            f"({', '.join(runtime.missing_dlls)}); using CPU. Run "
            f"{runtime.install_hint} in the FluentFlow virtual environment to "
            "enable GPU transcription."
        )
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return True, None
    except Exception as exc:  # noqa: BLE001 - any failure here means "no GPU"
        return False, f"CUDA support is not available in this build ({exc}); using CPU."
    return False, None


def _resolve_stt_device(device: str) -> tuple[str, str | None]:
    """Turn `auto` into a concrete device so the rest of the choices can key off it."""
    requested = (device or "auto").strip().lower()
    if requested != "auto":
        return requested, None
    usable, reason = _cuda_is_usable()
    return ("cuda" if usable else "cpu"), reason


def _compute_type_for(device: str) -> str:
    """int8 was picked for CPU; on an NVIDIA GPU float16 is the matching choice."""
    return "float16" if device.startswith("cuda") else "int8"


def _model_for_device(model_size: str, device: str) -> str:
    """Which faster-whisper model this lane may load on this device.

    large-v3 belongs on a GPU. On CPU it is several times slower than medium
    for a gain this lane cannot show, and it is another 3GB from a different
    repo (Systran) that a CPU-only machine has no reason to download, so a CPU
    request lands on medium whatever was asked for.
    """
    alias = _SIZE_ALIASES.get((model_size or DEFAULT_MODEL_SIZE).strip(), DEFAULT_MODEL_SIZE)
    if alias == "large-v3" and not device.startswith("cuda"):
        return "medium"
    return alias


# faster-whisper publishes its CTranslate2 conversions under this prefix, and
# its own `download_model` resolves a size to the same repository. The tests
# assert that against the library's table, so an upstream rename fails there
# rather than turning into an installer that downloads nothing.
_FASTER_WHISPER_REPO_PREFIX = "Systran/faster-whisper-"
_FASTER_WHISPER_WEIGHTS_FILENAME = "model.bin"


@dataclass(frozen=True)
class PlannedModel:
    """Which weights a transcription started right now would load.

    The installer and the readiness report both need this, and both have to
    read it from here rather than from the size the user asked for. On a
    CPU-only machine `_model_for_device` sends the run to medium, so an
    installer that pre-downloaded large-v3 would have spent 3GB on a file no
    run ever opens — and the readiness report would call the machine ready
    while the model it will really load is still missing.
    """

    engine: str
    model_size: str
    repo_id: str
    weights_filename: str
    requested_size: str
    local_dir: Path | None = None
    note: str | None = None

    @property
    def downgraded(self) -> bool:
        return self.model_size != self.requested_size


def planned_model(
    model_size: str = DEFAULT_MODEL_SIZE,
    *,
    engine: str | None = None,
    device: str = "auto",
) -> PlannedModel:
    """Resolve the lane, the device and the size the way a real run resolves them."""
    requested = (model_size or DEFAULT_MODEL_SIZE).strip()
    lane, lane_reason = resolve_local_stt_engine(engine)

    if lane == "mlx":
        return PlannedModel(
            engine="mlx",
            model_size=local_stt_mlx.resolve_size(requested),
            repo_id=local_stt_mlx.resolve_repo(requested),
            weights_filename=local_stt_mlx.WEIGHTS_FILENAME,
            requested_size=requested,
            note=lane_reason,
        )

    effective_device, device_reason = _resolve_stt_device(device)
    size = _model_for_device(requested, effective_device)
    local = _LOCAL_MODEL_DIR / size
    notes = [note for note in (lane_reason, device_reason) if note]
    return PlannedModel(
        engine="faster_whisper",
        model_size=size,
        repo_id=f"{_FASTER_WHISPER_REPO_PREFIX}{size}",
        weights_filename=_FASTER_WHISPER_WEIGHTS_FILENAME,
        requested_size=requested,
        local_dir=local if (local / _FASTER_WHISPER_WEIGHTS_FILENAME).is_file() else None,
        note="；".join(notes) if notes else None,
    )


def _is_large_v3(model_size: str | None) -> bool:
    """Whether the model in play needs the settings medium was tuned against off.

    Keyed on the model rather than on the lane: both lanes can run large-v3
    (MLX on Apple silicon, faster-whisper on CUDA) and both would hit the same
    two failures — see `_build_transcribe_defaults` and
    `_looks_like_low_confidence_hallucination`.
    """
    return (model_size or "").strip() == "large-v3"


def _active_model_size(model_stats: dict[str, Any], requested: str) -> str:
    """Which model actually loaded, so the guards below key off reality.

    `_model_for_device` can downgrade large-v3 to medium, and a caller may hand
    in its own model object, so the request is not enough to go on.
    """
    resolved = model_stats.get("model_resolved")
    if resolved:
        name = Path(str(resolved)).name
        if name in {"medium", "large-v3"}:
            return name
    return _SIZE_ALIASES.get((requested or DEFAULT_MODEL_SIZE).strip(), DEFAULT_MODEL_SIZE)


def _resolve_model(model_size: str, device: str = "cpu") -> str:
    """Return a local path if a pre-downloaded model exists, else the name for HF."""
    alias = _model_for_device(model_size, device)
    local = _LOCAL_MODEL_DIR / alias
    if local.is_dir() and (local / "model.bin").is_file():
        return str(local)
    return alias


# ── Singleton model cache ────────────────────────────────────────────
_model_cache: dict[str, WhisperModel] = {}
_model_lock = threading.Lock()
_opencc_converter: Any | None = None
_opencc_checked = False


def _to_simplified_chinese(text: str) -> str:
    """Convert Traditional Chinese output to Simplified when OpenCC is available."""
    global _opencc_converter, _opencc_checked
    if not text:
        return text
    if not _opencc_checked:
        _opencc_checked = True
        try:
            from opencc import OpenCC

            _opencc_converter = OpenCC("t2s")
        except Exception as exc:
            logger.info("OpenCC not available; STT text will not be converted: %s", exc)
            _opencc_converter = None
    if _opencc_converter is None:
        return text
    return _opencc_converter.convert(text)


def _is_cuda_runtime_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(token in message for token in ("cublas", "cudnn", "cuda", "nvcuda"))


def get_or_load_model_with_stats(
    model_size: str = DEFAULT_MODEL_SIZE,
    compute_type: str | None = None,
    device: str = "auto",
    cpu_threads: int = 0,
    num_workers: int = 1,
) -> tuple[WhisperModel, dict[str, Any]]:
    """Return a cached WhisperModel plus coarse cache/load metadata.

    `compute_type=None` means "whatever suits the device this resolves to".
    """
    effective_device, fallback_reason = _resolve_stt_device(device)
    if fallback_reason:
        logger.warning(fallback_reason)
    effective_compute = compute_type or _compute_type_for(effective_device)
    resolved = _resolve_model(model_size, effective_device)

    def _cache_key() -> str:
        return f"{resolved}|{effective_compute}|{effective_device}|{cpu_threads}|{num_workers}"

    with _model_lock:
        key = _cache_key()
        cache_hit = key in _model_cache
        load_seconds = 0.0
        if not cache_hit:
            logger.info(
                "Loading Whisper model: %s (device=%s, compute=%s)…",
                resolved, effective_device, effective_compute,
            )
            started_at = time.perf_counter()
            try:
                _model_cache[key] = WhisperModel(
                    resolved,
                    device=effective_device,
                    compute_type=effective_compute,
                    cpu_threads=cpu_threads,
                    num_workers=num_workers,
                )
            except (OSError, RuntimeError) as exc:
                # A GPU that answered yes and then failed to start still has to
                # produce a transcript. Only automatic selection retries: a
                # caller that asked for cuda by name wants to hear that it broke.
                if (
                    (device or "auto").strip().lower() != "auto"
                    or not effective_device.startswith("cuda")
                    or not _is_cuda_runtime_error(exc)
                ):
                    raise
                fallback_reason = f"GPU runtime could not start ({exc}); using CPU."
                logger.warning(fallback_reason)
                effective_device = "cpu"
                effective_compute = compute_type or _compute_type_for(effective_device)
                resolved = _resolve_model(model_size, effective_device)
                key = _cache_key()
                cache_hit = key in _model_cache
                if not cache_hit:
                    _model_cache[key] = WhisperModel(
                        resolved,
                        device=effective_device,
                        compute_type=effective_compute,
                        cpu_threads=cpu_threads,
                        num_workers=num_workers,
                    )
            load_seconds = time.perf_counter() - started_at
            logger.info("Whisper model loaded.")
        return _model_cache[key], {
            "model_cache_hit": cache_hit,
            "model_load_seconds": round(load_seconds, 3),
            "model_source": "local_cache" if Path(resolved).is_absolute() else "model_name",
            "model_resolved": resolved,
            "compute_type": effective_compute,
            "device_requested": device,
            "device_resolved": getattr(_model_cache[key], "device", None),
            "device_fallback_reason": fallback_reason,
            "cpu_threads": cpu_threads,
            "num_workers": num_workers,
        }


def get_or_load_model(
    model_size: str = DEFAULT_MODEL_SIZE, compute_type: str | None = None
) -> WhisperModel:
    """Return a cached WhisperModel, loading it once on first call."""
    model, _ = get_or_load_model_with_stats(model_size, compute_type)
    return model


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    speaker: str | None = None
    # Per-word timings when the provider supplies them. Optional because the
    # local engine does not, and every existing caller predates this field.
    #
    # Kept because throwing it away costs a re-transcription to get back. Cutting
    # a filler word out of the middle of a sentence, word-level subtitles,
    # click-a-word-to-seek, and word search all need it, and none of them are
    # reachable from sentence spans alone — the sentence only says "2450.1s to
    # 2460.8s", not where the 呃 inside it sits.
    words: tuple[dict[str, Any], ...] | None = None


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    segments: tuple[TranscriptSegment, ...]
    language: str | None
    language_probability: float | None
    duration: float | None
    model_cache_hit: bool | None = None
    model_load_seconds: float | None = None
    model_source: str | None = None
    compute_type: str | None = None
    device_requested: str | None = None
    device_resolved: str | None = None
    cpu_threads: int | None = None
    num_workers: int | None = None
    vad_filter: bool | None = None
    diarization_error: str | None = None
    # Which local lane ran, and why the Apple-GPU one did not when it did not.
    # Without this a run gives no way to tell the two apart: faster-whisper
    # reports `device_resolved` as None here.
    engine: str | None = None
    engine_fallback_reason: str | None = None
    # Seconds the VAD judged to be speech. Only the MLX lane reports it, and it
    # is what makes the fast lane's speed comparable to the CPU lane's: a file
    # that is half silence gets half the model passes.
    speech_seconds: float | None = None


@dataclass(frozen=True)
class AudioChunk:
    path: Path
    start: float
    duration: float


def _collect_segments(
    segments: Iterable[Any],
    *,
    total_duration: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    use_no_speech_prob: bool = True,
) -> tuple[tuple[TranscriptSegment, ...], str]:
    """Iterate the lazy segment generator, optionally reporting progress."""
    normalized: list[TranscriptSegment] = []
    parts: list[str] = []
    first_segment_seen = False
    for seg in segments:
        if _looks_like_low_confidence_hallucination(seg, use_no_speech_prob=use_no_speech_prob):
            continue
        t = (seg.text or "").strip()
        if not t:
            continue
        if not first_segment_seen:
            first_segment_seen = True
            if on_status:
                on_status("transcribing_segments")
        # Only the MLX lane supplies word timings, and it supplies them as dicts.
        # faster-whisper exposes a `words` attribute too, holding namedtuples and
        # left at None unless word timestamps were requested, so the shape is
        # checked rather than assumed.
        raw_words = getattr(seg, "words", None)
        words = (
            tuple(raw_words)
            if raw_words and all(isinstance(w, dict) for w in raw_words)
            else None
        )
        normalized.append(
            TranscriptSegment(start=float(seg.start), end=float(seg.end), text=t, words=words)
        )
        parts.append(t)
        if on_progress and total_duration and total_duration > 0:
            on_progress(min(float(seg.end) / total_duration, 1.0))
    return tuple(normalized), " ".join(parts)


def _simplify_segments(
    segments: tuple[TranscriptSegment, ...],
) -> tuple[TranscriptSegment, ...]:
    """Convert segment text, keeping every other field.

    Rebuilding the segment from three fields silently dropped `speaker` and
    `words`. Word timings only exist on the MLX lane and cost a whole
    re-transcription to recover, which is the same way they were lost once
    before, so they are carried through explicitly here.
    """
    return tuple(
        TranscriptSegment(
            start=s.start,
            end=s.end,
            text=_to_simplified_chinese(s.text),
            speaker=s.speaker,
            words=tuple(
                {**w, "text": _to_simplified_chinese(str(w.get("text") or ""))}
                for w in s.words
            )
            if s.words
            else None,
        )
        for s in segments
    )


def _looks_like_low_confidence_hallucination(seg: Any, *, use_no_speech_prob: bool = True) -> bool:
    """Use Whisper metadata to suppress common silence/noise hallucinations.

    `use_no_speech_prob` exists because that signal is only comparable within
    one model. Measured on the same 90s of meeting audio, medium reported
    no_speech_prob 0.13 and large-v3 reported 0.83 — the 0.75 threshold below
    was read off medium, so under large-v3 it fires on ordinary speech. On a
    10m31s recording it dropped 18 segments and opened a 28-second hole over
    material large-v3 had transcribed correctly. The compression-ratio rule
    does not have that problem, so callers on a model this was not calibrated
    against turn the first rule off and keep the second.
    """
    no_speech_prob = getattr(seg, "no_speech_prob", None)
    avg_logprob = getattr(seg, "avg_logprob", None)
    compression_ratio = getattr(seg, "compression_ratio", None)

    if (
        use_no_speech_prob
        and no_speech_prob is not None
        and avg_logprob is not None
        and float(no_speech_prob) >= 0.75
        and float(avg_logprob) <= -0.6
    ):
        return True
    if (
        compression_ratio is not None
        and avg_logprob is not None
        and float(compression_ratio) >= 2.6
        and float(avg_logprob) <= -0.5
    ):
        return True
    return False


def _normalize_for_repeat_filter(text: str) -> str:
    return _SEGMENT_NORM_RE.sub("", text or "").lower()


def _is_stuck_repetition(text: str) -> bool:
    """True when one segment is a single short unit repeated to fill silence.

    Different shape from `_filter_repeated_hallucination_segments`, which looks
    for the same sentence across consecutive segments. This is the loop that
    happens inside one segment: a meeting recording measured today returned a
    single segment containing 「跟着」 seventy times, and another containing
    「从此」 over a hundred times, both across stretches where the audio was
    digitally silent.

    That across-segments filter caught none of it, and on a 2h54m lecture it
    dropped nothing at all — the repeats there were scattered rather than
    consecutive. The two filters cover different failures; neither replaces the
    other.

    Deliberately mechanical: a unit of 1-4 characters covering more than 70% of
    a segment of at least 20 characters is not something a person says. Nothing
    here judges whether the content is worth keeping.
    """
    body = _normalize_for_repeat_filter(text)
    if len(body) < 20:
        return False
    for size in (1, 2, 3, 4):
        unit = body[:size]
        if unit and body.count(unit) * size > len(body) * 0.7:
            return True
    return False


def _drop_stuck_repetitions(
    segments: tuple[TranscriptSegment, ...],
) -> tuple[TranscriptSegment, ...]:
    kept = tuple(s for s in segments if not _is_stuck_repetition(s.text))
    if len(kept) != len(segments):
        logger.info(
            "Dropped %s segment(s) stuck repeating one phrase",
            len(segments) - len(kept),
        )
    return kept


def _char_bigrams(text: str) -> set[str]:
    stripped = _SEGMENT_NORM_RE.sub("", text or "")
    return {stripped[i:i + 2] for i in range(len(stripped) - 1)}


def _looks_like_prompt_echo(text: str, prompt: str | None) -> bool:
    """True when a segment is the initial prompt coming back as transcribed speech.

    Whisper treats `initial_prompt` as preceding text, and on a window with
    little real speech it continues that text instead of the audio — so the
    instruction the product injected gets written down as if someone had said
    it. A 2h54m lecture produced 95 of them: "请使用简体中文输出，保留必要的
    英文术语。" 53 times, plus recombinations like "请使用简体中文语音转录。"
    that no substring match would catch, because the model recombines fragments
    rather than quoting.

    So the test is character-bigram overlap with the prompt actually used, and
    the margin was measured rather than assumed: across that lecture, the
    echoes scored 0.62-1.00 while the highest-scoring real sentence reached
    0.20. The threshold sits in the middle of a 3x gap.

    The length floor protects the speaker's verbal tics — "对吧" appears 37
    times and shares bigrams with nothing, but short strings make the ratio
    unstable, so anything under 8 characters is left alone.
    """
    if not prompt or not text:
        return False
    body = _SEGMENT_NORM_RE.sub("", text)
    if len(body) < 8:
        return False
    segment_bigrams = _char_bigrams(text)
    if not segment_bigrams:
        return False
    overlap = len(segment_bigrams & _char_bigrams(prompt)) / len(segment_bigrams)
    return overlap >= 0.5


def _drop_prompt_echoes(
    segments: tuple[TranscriptSegment, ...],
    prompt: str | None,
) -> tuple[TranscriptSegment, ...]:
    kept = tuple(s for s in segments if not _looks_like_prompt_echo(s.text, prompt))
    if len(kept) != len(segments):
        logger.info(
            "Dropped %s segment(s) that echoed the initial prompt",
            len(segments) - len(kept),
        )
    return kept


def _filter_repeated_hallucination_segments(
    segments: tuple[TranscriptSegment, ...],
) -> tuple[TranscriptSegment, ...]:
    """Drop runs like "大学生 课题" repeated through silent/noisy spans."""
    kept: list[TranscriptSegment] = []
    i = 0
    while i < len(segments):
        seg = segments[i]
        norm = _normalize_for_repeat_filter(seg.text)
        if not norm:
            i += 1
            continue

        j = i + 1
        while j < len(segments) and _normalize_for_repeat_filter(segments[j].text) == norm:
            j += 1

        run = segments[i:j]
        run_count = len(run)
        span = run[-1].end - run[0].start if run else 0
        short_phrase = len(norm) <= 18
        repeated_noise = short_phrase and (run_count >= 4 or (run_count >= 3 and span >= 8))
        if repeated_noise:
            logger.info(
                "Dropping repeated STT hallucination run: %r x%s",
                run[0].text,
                run_count,
            )
        else:
            kept.extend(run)
        i = j
    return tuple(kept)


def _write_wav_chunks(
    audio_path: str | Path,
    output_dir: str | Path,
    *,
    chunk_seconds: float,
) -> tuple[AudioChunk, ...]:
    """Split a PCM WAV into fixed-duration chunks and return their offsets."""
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be greater than 0")

    src = Path(audio_path).expanduser().resolve()
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[AudioChunk] = []

    with wave.open(str(src), "rb") as reader:
        params = reader.getparams()
        frame_rate = reader.getframerate()
        total_frames = reader.getnframes()
        frames_per_chunk = max(1, int(frame_rate * chunk_seconds))

        start_frame = 0
        index = 0
        while start_frame < total_frames:
            frame_count = min(frames_per_chunk, total_frames - start_frame)
            frames = reader.readframes(frame_count)
            chunk_path = out_dir / f"chunk_{index:04d}.wav"
            with wave.open(str(chunk_path), "wb") as writer:
                writer.setparams(params)
                writer.writeframes(frames)
            chunks.append(
                AudioChunk(
                    path=chunk_path,
                    start=start_frame / frame_rate,
                    duration=frame_count / frame_rate,
                )
            )
            start_frame += frame_count
            index += 1

    return tuple(chunks)


def _transcribe_profile_defaults(speed_profile: str | None) -> dict[str, Any]:
    profile = (speed_profile or "balanced").strip().lower()
    if profile not in _STT_SPEED_PROFILES:
        profile = "balanced"
    return {
        key: (value.copy() if isinstance(value, dict) else value)
        for key, value in _STT_SPEED_PROFILES[profile].items()
    }


def _limit_text(value: str | None, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip(" 、,，;；:")


def _build_transcribe_defaults(
    *,
    language: str | None,
    speed_profile: str | None,
    hotwords: str | None,
    initial_prompt: str | None,
    extra_kwargs: dict[str, Any] | None = None,
    model_size: str | None = None,
) -> dict[str, Any]:
    """Decoding options, minus the Chinese prompt when the model cannot take it.

    That prompt makes large-v3 skip the opening of a recording: same 90s clip,
    same VAD, 175 characters starting at 57s with it against 478 starting at 0s
    without. medium barely notices (486 against 506), which is why it went
    unseen while medium was the default. `_simplify_segments` produces the
    simplified output the prompt was asking for anyway, so dropping it costs
    nothing; a prompt the caller passed in is still honoured.
    """
    transcribe_defaults = _transcribe_profile_defaults(speed_profile)
    prompt_parts: list[str] = []
    if language == "zh" and not _is_large_v3(model_size):
        prompt_parts.append(_ZH_INITIAL_PROMPT)
    if initial_prompt:
        prompt_parts.append(initial_prompt.strip())
    if hotwords:
        transcribe_defaults["hotwords"] = _limit_text(
            " ".join(
                part for part in (str(transcribe_defaults.get("hotwords") or ""), hotwords.strip()) if part
            ),
            _MAX_HOTWORDS_CHARS,
        )
    if prompt_parts:
        transcribe_defaults["initial_prompt"] = _limit_text(
            "\n".join(prompt_parts),
            _MAX_INITIAL_PROMPT_CHARS,
        )
    transcribe_defaults.update(extra_kwargs or {})
    return transcribe_defaults


def _normalize_language(language: str | None) -> str | None:
    value = (language or "auto").strip().lower()
    aliases = {
        "auto": None,
        "detect": None,
        "": None,
        "zh-cn": "zh",
        "zh_hans": "zh",
        "chinese": "zh",
        "cn": "zh",
        "english": "en",
    }
    return aliases.get(value, value)


def transcribe_audio(
    audio_path: str | Path,
    *,
    model: WhisperModel | None = None,
    model_size: str = DEFAULT_MODEL_SIZE,
    compute_type: str | None = None,
    device: str = "auto",
    cpu_threads: int = 0,
    num_workers: int = 1,
    vad_filter: bool = True,
    language: str | None = None,
    speed_profile: str | None = None,
    hotwords: str | None = None,
    initial_prompt: str | None = None,
    engine: str | None = None,
    word_timestamps: bool = False,
    on_progress: Callable[[float], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    **transcribe_kwargs: Any,
) -> TranscriptionResult:
    """
    Transcribe audio locally, on the Apple GPU when this machine has one.

    Args:
        engine: "auto" (default), "mlx" to demand the Apple GPU lane, or
            "faster_whisper" to demand the CPU one. See `resolve_local_stt_engine`.
        on_progress: Optional callback receiving a float 0.0–1.0 during transcription.
    """
    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Audio not found: {path}")

    # An explicitly supplied model object is a caller holding a faster-whisper
    # handle; honouring it matters more than the speed, so the fast lane is
    # skipped rather than quietly ignoring what was passed in.
    lane, fallback_reason = ("faster_whisper", None) if model is not None else resolve_local_stt_engine(engine)
    if lane == "mlx":
        result = _transcribe_with_mlx(
            path,
            model_size=model_size,
            vad_filter=vad_filter,
            language=language,
            speed_profile=speed_profile,
            hotwords=hotwords,
            initial_prompt=initial_prompt,
            transcribe_kwargs=transcribe_kwargs,
            word_timestamps=word_timestamps,
            on_progress=on_progress,
            on_status=on_status,
        )
        if result is not None:
            return result
        fallback_reason = "MLX 转录失败，已回退到 faster-whisper"

    model_stats: dict[str, Any] = {
        "model_cache_hit": None,
        "model_load_seconds": None,
        "model_source": None,
        "compute_type": compute_type,
        "device_requested": device,
        "device_resolved": None,
        "cpu_threads": cpu_threads,
        "num_workers": num_workers,
    }
    if model is None:
        if on_status:
            on_status("loading_model")
        model, model_stats = get_or_load_model_with_stats(
            model_size,
            compute_type,
            device=device,
            cpu_threads=cpu_threads,
            num_workers=num_workers,
        )
    if on_status:
        on_status("preparing_audio")

    language = _normalize_language(language)
    active_size = _active_model_size(model_stats, model_size)
    transcribe_defaults = _build_transcribe_defaults(
        language=language,
        speed_profile=speed_profile,
        hotwords=hotwords,
        initial_prompt=initial_prompt,
        extra_kwargs=transcribe_kwargs,
        model_size=active_size,
    )

    segments_iter, info = model.transcribe(
        str(path),
        vad_filter=vad_filter,
        language=language,
        **transcribe_defaults,
    )
    if on_status:
        on_status("waiting_first_segment")
    duration = getattr(info, "duration", None)
    segments, text = _collect_segments(
        segments_iter,
        total_duration=duration,
        on_progress=on_progress,
        on_status=on_status,
        use_no_speech_prob=not _is_large_v3(active_size),
    )
    segments = _simplify_segments(segments)
    segments = _drop_prompt_echoes(segments, transcribe_defaults.get("initial_prompt"))
    segments = _drop_stuck_repetitions(segments)
    segments = _filter_repeated_hallucination_segments(segments)
    text = " ".join(s.text for s in segments)
    return TranscriptionResult(
        text=text,
        segments=segments,
        language=getattr(info, "language", None),
        language_probability=getattr(info, "language_probability", None),
        duration=duration,
        model_cache_hit=model_stats.get("model_cache_hit"),
        model_load_seconds=model_stats.get("model_load_seconds"),
        model_source=model_stats.get("model_source"),
        compute_type=model_stats.get("compute_type"),
        device_requested=model_stats.get("device_requested"),
        device_resolved=model_stats.get("device_resolved"),
        cpu_threads=model_stats.get("cpu_threads"),
        num_workers=model_stats.get("num_workers"),
        vad_filter=vad_filter,
        engine="faster_whisper",
        engine_fallback_reason=fallback_reason,
    )


def _transcribe_with_mlx(
    path: Path,
    *,
    model_size: str,
    vad_filter: bool,
    language: str | None,
    speed_profile: str | None,
    hotwords: str | None,
    initial_prompt: str | None,
    transcribe_kwargs: dict[str, Any],
    word_timestamps: bool,
    on_progress: Callable[[float], None] | None,
    on_status: Callable[[str], None] | None,
) -> TranscriptionResult | None:
    """Run the Apple-GPU lane, or return None so the caller falls back.

    Returning None instead of raising is deliberate: this lane is an
    optimisation, and a user's transcription should never fail because the fast
    path broke. Any failure here costs time, not the job.
    """
    if on_status:
        on_status("loading_model")
    language = _normalize_language(language)
    defaults = _build_transcribe_defaults(
        language=language,
        speed_profile=speed_profile,
        hotwords=hotwords,
        initial_prompt=initial_prompt,
        extra_kwargs=transcribe_kwargs,
        model_size=model_size,
    )
    started_at = time.perf_counter()
    try:
        raw_segments, info, stats = local_stt_mlx.transcribe(
            path,
            model_size=model_size,
            language=language,
            initial_prompt=defaults.get("initial_prompt"),
            vad_filter=vad_filter,
            vad_parameters=defaults.get("vad_parameters"),
            word_timestamps=word_timestamps,
            on_progress=on_progress,
            on_status=on_status,
        )
    except Exception as exc:  # noqa: BLE001 - the fallback covers every failure mode
        logger.warning("MLX 转录失败，回退到 faster-whisper：%s", exc, exc_info=True)
        return None

    segments, _ = _collect_segments(
        raw_segments, use_no_speech_prob=not _is_large_v3(model_size)
    )
    segments = _simplify_segments(segments)
    segments = _drop_prompt_echoes(segments, defaults.get("initial_prompt"))
    segments = _drop_stuck_repetitions(segments)
    segments = _filter_repeated_hallucination_segments(segments)
    elapsed = time.perf_counter() - started_at
    logger.info(
        "MLX 转录完成：%.1fs 音频用时 %.1fs，句 %d",
        info.duration or 0.0,
        elapsed,
        len(segments),
    )
    return TranscriptionResult(
        text=" ".join(s.text for s in segments),
        segments=segments,
        language=info.language,
        language_probability=info.language_probability,
        duration=info.duration,
        model_cache_hit=stats.get("model_load_seconds") == 0.0,
        model_load_seconds=stats.get("model_load_seconds"),
        model_source=stats.get("mlx_repo"),
        compute_type="float16",
        device_requested="mlx",
        device_resolved="gpu",
        cpu_threads=0,
        num_workers=1,
        vad_filter=vad_filter,
        engine="mlx",
        speech_seconds=stats.get("speech_seconds"),
    )


def transcribe_audio_chunked(
    audio_path: str | Path,
    *,
    model_size: str = DEFAULT_MODEL_SIZE,
    compute_type: str | None = None,
    device: str = "auto",
    cpu_threads: int = 0,
    num_workers: int = 1,
    vad_filter: bool = True,
    language: str | None = None,
    speed_profile: str | None = None,
    hotwords: str | None = None,
    initial_prompt: str | None = None,
    engine: str | None = None,
    chunk_seconds: float = 60.0,
    on_progress: Callable[[float], None] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> TranscriptionResult:
    """
    Transcribe long WAV files chunk by chunk so progress reflects real completed audio.

    faster-whisper only reports segment progress after its lazy generator starts yielding.
    For long recordings that can mean minutes of no visible movement. Chunking creates
    smaller real completion boundaries without inventing synthetic progress.
    """
    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Audio not found: {path}")

    # Chunking exists to work around faster-whisper's lazy generator, which can
    # sit for minutes on a long file before the first segment appears. The MLX
    # lane reports progress per speech run already, so chunking it would only
    # add cuts — and cutting mid-word costs accuracy at every boundary.
    if resolve_local_stt_engine(engine)[0] == "mlx":
        return transcribe_audio(
            path,
            model_size=model_size,
            vad_filter=vad_filter,
            language=language,
            speed_profile=speed_profile,
            hotwords=hotwords,
            initial_prompt=initial_prompt,
            engine=engine,
            on_progress=on_progress,
            on_status=on_status,
        )

    if on_status:
        on_status("loading_model")
    model, model_stats = get_or_load_model_with_stats(
        model_size,
        compute_type,
        device=device,
        cpu_threads=cpu_threads,
        num_workers=num_workers,
    )

    with tempfile.TemporaryDirectory(prefix="fluentflow_stt_chunks_") as tmp:
        if on_status:
            on_status("chunking_audio")
        chunks = _write_wav_chunks(path, tmp, chunk_seconds=chunk_seconds)
        if not chunks:
            return TranscriptionResult(
                text="",
                segments=(),
                language=None,
                language_probability=None,
                duration=0,
                model_cache_hit=model_stats.get("model_cache_hit"),
                model_load_seconds=model_stats.get("model_load_seconds"),
                model_source=model_stats.get("model_source"),
                compute_type=model_stats.get("compute_type"),
                device_requested=model_stats.get("device_requested"),
                device_resolved=model_stats.get("device_resolved"),
                cpu_threads=model_stats.get("cpu_threads"),
                num_workers=model_stats.get("num_workers"),
                vad_filter=vad_filter,
            )

        total_duration = sum(chunk.duration for chunk in chunks)
        all_segments: list[TranscriptSegment] = []
        language_seen: str | None = None
        language_probability: float | None = None

        for chunk in chunks:
            if on_status:
                on_status("transcribing_chunks" if len(chunks) > 1 else "waiting_first_segment")

            def chunk_progress(frac: float, *, current_chunk: AudioChunk = chunk) -> None:
                if on_progress and total_duration > 0:
                    safe_frac = max(0.0, min(float(frac or 0), 1.0))
                    completed = current_chunk.start + safe_frac * current_chunk.duration
                    on_progress(min(completed / total_duration, 1.0))

            result = transcribe_audio(
                chunk.path,
                model=model,
                # The loaded model, not the requested one: this hands over a
                # model object, so the callee cannot work out for itself
                # whether `_model_for_device` downgraded large-v3 to medium,
                # and the prompt and filter rules turn on that answer.
                model_size=_active_model_size(model_stats, model_size),
                compute_type=compute_type,
                device=device,
                cpu_threads=cpu_threads,
                num_workers=num_workers,
                vad_filter=vad_filter,
                language=language,
                speed_profile=speed_profile,
                hotwords=hotwords,
                initial_prompt=initial_prompt,
                on_progress=chunk_progress,
                on_status=on_status,
            )
            if language_seen is None:
                language_seen = result.language
                language_probability = result.language_probability

            all_segments.extend(
                TranscriptSegment(
                    start=segment.start + chunk.start,
                    end=segment.end + chunk.start,
                    text=segment.text,
                    speaker=segment.speaker,
                    words=tuple(
                        {**word,
                         "start": float(word["start"]) + chunk.start,
                         "end": float(word["end"]) + chunk.start}
                        for word in segment.words
                    )
                    if segment.words
                    else None,
                )
                for segment in result.segments
            )
            if on_progress and total_duration > 0:
                on_progress(min((chunk.start + chunk.duration) / total_duration, 1.0))

    segments = _filter_repeated_hallucination_segments(tuple(all_segments))
    return TranscriptionResult(
        text=" ".join(segment.text for segment in segments),
        segments=segments,
        language=language_seen,
        language_probability=language_probability,
        duration=total_duration,
        model_cache_hit=model_stats.get("model_cache_hit"),
        model_load_seconds=model_stats.get("model_load_seconds"),
        model_source=model_stats.get("model_source"),
        compute_type=model_stats.get("compute_type"),
        device_requested=model_stats.get("device_requested"),
        device_resolved=model_stats.get("device_resolved"),
        cpu_threads=model_stats.get("cpu_threads"),
        num_workers=model_stats.get("num_workers"),
        vad_filter=vad_filter,
        engine="faster_whisper",
    )
