#!/usr/bin/env python3
"""Benchmark FluentFlow's local STT path on one media file.

This uses the same core functions as the app: FFmpeg extraction via
``extract_stt_wav`` and transcription via ``transcribe_audio``. It does not
write analytics events or store transcript content by default.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.audio_handler import extract_stt_wav  # noqa: E402
from backend.core.local_stt import transcribe_audio  # noqa: E402


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_metadata() -> dict[str, Any]:
    return {
        "runtime_os": platform.system(),
        "runtime_machine": platform.machine(),
        "runtime_cpu_count": os.cpu_count(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "faster_whisper_version": package_version("faster-whisper"),
        "ctranslate2_version": package_version("ctranslate2"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Audio or video file to benchmark")
    parser.add_argument("--model", default="medium", help="faster-whisper model size")
    parser.add_argument("--speed", choices=("fast", "balanced", "accurate"), default="balanced")
    parser.add_argument("--language", default="auto", help="auto, zh, en, etc.")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--cpu-threads", type=int, default=0)
    parser.add_argument("--num-workers", type=int, default=1)
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path")
    parser.add_argument("--include-preview", action="store_true", help="Include a 200-character transcript preview")
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"Source file not found: {source}")

    started_at = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="fluentflow_benchmark_") as td:
        wav_path = Path(td) / f"{source.stem}_stt.wav"
        audio_started_at = time.perf_counter()
        extract_stt_wav(source, output_path=wav_path)
        audio_elapsed = time.perf_counter() - audio_started_at

        stt_started_at = time.perf_counter()
        result = transcribe_audio(
            wav_path,
            model_size=args.model,
            speed_profile=args.speed,
            language=args.language,
            compute_type=args.compute_type,
            device=args.device,
            cpu_threads=args.cpu_threads,
            num_workers=args.num_workers,
        )
        stt_elapsed = time.perf_counter() - stt_started_at

    source_duration = result.duration or (result.segments[-1].end if result.segments else None)
    realtime_factor = (
        round(stt_elapsed / source_duration, 4)
        if source_duration and source_duration > 0
        else None
    )
    payload: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": {
            "filename": source.name,
            "size_mb": round(source.stat().st_size / (1024 * 1024), 3),
            "fingerprint": {
                "algorithm": "sha256",
                "sha256": file_sha256(source),
                "source_size_bytes": source.stat().st_size,
            },
        },
        "config": {
            "stt_model": args.model,
            "stt_speed": args.speed,
            "stt_language": args.language,
            "compute_type": args.compute_type,
            "device_requested": args.device,
            "cpu_threads": args.cpu_threads,
            "num_workers": args.num_workers,
            "vad_filter": True,
        },
        "runtime": runtime_metadata(),
        "metrics": {
            "source_duration_seconds": round(source_duration, 3) if source_duration else None,
            "audio_extract_seconds": round(audio_elapsed, 3),
            "stt_elapsed_seconds": round(stt_elapsed, 3),
            "stt_realtime_factor": realtime_factor,
            "stt_realtime_speed": round(1 / realtime_factor, 3) if realtime_factor else None,
            "total_elapsed_seconds": round(time.perf_counter() - started_at, 3),
            "segment_count": len(result.segments),
            "transcript_length": len(result.text or ""),
            "detected_language": result.language,
            "language_probability": result.language_probability,
            "model_cache_hit": result.model_cache_hit,
            "model_load_seconds": result.model_load_seconds,
            "model_source": result.model_source,
            "device_resolved": result.device_resolved,
        },
    }
    if args.include_preview:
        payload["transcript_preview"] = (result.text or "")[:200]

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
