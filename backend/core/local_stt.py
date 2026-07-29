"""Local speech-to-text with faster-whisper (timestamped segments)."""

from __future__ import annotations

import logging
import re
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from faster_whisper import WhisperModel

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

DEFAULT_MODEL_SIZE = "medium"

_SIZE_ALIASES: dict[str, str] = {
    "tiny": DEFAULT_MODEL_SIZE,
    "base": DEFAULT_MODEL_SIZE,
    "small": DEFAULT_MODEL_SIZE,
    "medium": "medium",
    "large-v2": DEFAULT_MODEL_SIZE,
    "large-v3": DEFAULT_MODEL_SIZE,
}


def _resolve_model(model_size: str) -> str:
    """Return a local path if a pre-downloaded model exists, else the name for HF."""
    alias = _SIZE_ALIASES.get((model_size or DEFAULT_MODEL_SIZE).strip(), DEFAULT_MODEL_SIZE)
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


def get_or_load_model_with_stats(
    model_size: str = DEFAULT_MODEL_SIZE,
    compute_type: str = "int8",
    device: str = "auto",
    cpu_threads: int = 0,
    num_workers: int = 1,
) -> tuple[WhisperModel, dict[str, Any]]:
    """Return a cached WhisperModel plus coarse cache/load metadata."""
    resolved = _resolve_model(model_size)
    key = f"{resolved}|{compute_type}|{device}|{cpu_threads}|{num_workers}"
    with _model_lock:
        cache_hit = key in _model_cache
        load_seconds = 0.0
        if not cache_hit:
            logger.info("Loading Whisper model: %s (device=%s, compute=%s)…", resolved, device, compute_type)
            started_at = time.perf_counter()
            _model_cache[key] = WhisperModel(
                resolved,
                device=device,
                compute_type=compute_type,
                cpu_threads=cpu_threads,
                num_workers=num_workers,
            )
            load_seconds = time.perf_counter() - started_at
            logger.info("Whisper model loaded.")
        return _model_cache[key], {
            "model_cache_hit": cache_hit,
            "model_load_seconds": round(load_seconds, 3),
            "model_source": "local_cache" if Path(resolved).is_absolute() else "model_name",
            "compute_type": compute_type,
            "device_requested": device,
            "device_resolved": getattr(_model_cache[key], "device", None),
            "cpu_threads": cpu_threads,
            "num_workers": num_workers,
        }


def get_or_load_model(model_size: str = DEFAULT_MODEL_SIZE, compute_type: str = "int8") -> WhisperModel:
    """Return a cached WhisperModel, loading it once on first call."""
    model, _ = get_or_load_model_with_stats(model_size, compute_type)
    return model


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    speaker: str | None = None


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
) -> tuple[tuple[TranscriptSegment, ...], str]:
    """Iterate the lazy segment generator, optionally reporting progress."""
    normalized: list[TranscriptSegment] = []
    parts: list[str] = []
    first_segment_seen = False
    for seg in segments:
        if _looks_like_low_confidence_hallucination(seg):
            continue
        t = (seg.text or "").strip()
        if not t:
            continue
        if not first_segment_seen:
            first_segment_seen = True
            if on_status:
                on_status("transcribing_segments")
        normalized.append(
            TranscriptSegment(start=float(seg.start), end=float(seg.end), text=t)
        )
        parts.append(t)
        if on_progress and total_duration and total_duration > 0:
            on_progress(min(float(seg.end) / total_duration, 1.0))
    return tuple(normalized), " ".join(parts)


def _simplify_segments(
    segments: tuple[TranscriptSegment, ...],
) -> tuple[TranscriptSegment, ...]:
    return tuple(
        TranscriptSegment(start=s.start, end=s.end, text=_to_simplified_chinese(s.text))
        for s in segments
    )


def _looks_like_low_confidence_hallucination(seg: Any) -> bool:
    """Use Whisper metadata to suppress common silence/noise hallucinations."""
    no_speech_prob = getattr(seg, "no_speech_prob", None)
    avg_logprob = getattr(seg, "avg_logprob", None)
    compression_ratio = getattr(seg, "compression_ratio", None)

    if (
        no_speech_prob is not None
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
) -> dict[str, Any]:
    transcribe_defaults = _transcribe_profile_defaults(speed_profile)
    prompt_parts: list[str] = []
    if language == "zh":
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
    compute_type: str = "int8",
    device: str = "auto",
    cpu_threads: int = 0,
    num_workers: int = 1,
    vad_filter: bool = True,
    language: str | None = None,
    speed_profile: str | None = None,
    hotwords: str | None = None,
    initial_prompt: str | None = None,
    on_progress: Callable[[float], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    **transcribe_kwargs: Any,
) -> TranscriptionResult:
    """
    Transcribe audio with faster-whisper.

    Args:
        on_progress: Optional callback receiving a float 0.0–1.0 during transcription.
    """
    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Audio not found: {path}")

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
    transcribe_defaults = _build_transcribe_defaults(
        language=language,
        speed_profile=speed_profile,
        hotwords=hotwords,
        initial_prompt=initial_prompt,
        extra_kwargs=transcribe_kwargs,
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
    )
    segments = _simplify_segments(segments)
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
    )


def transcribe_audio_chunked(
    audio_path: str | Path,
    *,
    model_size: str = DEFAULT_MODEL_SIZE,
    compute_type: str = "int8",
    device: str = "auto",
    cpu_threads: int = 0,
    num_workers: int = 1,
    vad_filter: bool = True,
    language: str | None = None,
    speed_profile: str | None = None,
    hotwords: str | None = None,
    initial_prompt: str | None = None,
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
                model_size=model_size,
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
    )
