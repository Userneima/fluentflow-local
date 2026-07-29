"""Run faster-whisper STT in a cancellable child process."""

from __future__ import annotations

import multiprocessing as mp
import queue
import traceback
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any


def _safe_put(out_queue: mp.Queue, payload: dict[str, Any]) -> None:
    try:
        out_queue.put(payload)
    except Exception:
        pass


def _transcribe_worker(
    out_queue: mp.Queue,
    audio_path: str,
    options: dict[str, Any],
) -> None:
    from backend.core.local_stt import DEFAULT_MODEL_SIZE, transcribe_audio, transcribe_audio_chunked

    def on_progress(value: float) -> None:
        _safe_put(out_queue, {"type": "progress", "value": float(value)})

    def on_status(status: str) -> None:
        _safe_put(out_queue, {"type": "status", "status": status})

    try:
        common_kwargs = {
            "model_size": options.get("model_size") or DEFAULT_MODEL_SIZE,
            "speed_profile": options.get("speed_profile") or "balanced",
            "language": options.get("language") or "auto",
            "compute_type": options.get("compute_type") or "int8",
            "device": options.get("device") or "auto",
            "cpu_threads": int(options.get("cpu_threads") or 0),
            "num_workers": int(options.get("num_workers") or 1),
            "initial_prompt": options.get("initial_prompt") or None,
            "on_progress": on_progress,
            "on_status": on_status,
        }
        chunk_seconds = float(options.get("chunk_seconds") or 0)
        if chunk_seconds > 0:
            result = transcribe_audio_chunked(
                Path(audio_path),
                chunk_seconds=chunk_seconds,
                **common_kwargs,
            )
        else:
            result = transcribe_audio(
                Path(audio_path),
                **common_kwargs,
            )
        _safe_put(out_queue, {"type": "result", "result": result})
    except BaseException as exc:
        _safe_put(
            out_queue,
            {
                "type": "error",
                "error": str(exc),
                "traceback": traceback.format_exc(limit=12),
            },
        )


def start_transcription_process(
    audio_path: str | Path,
    *,
    model_size: str,
    speed_profile: str,
    language: str,
    compute_type: str = "int8",
    device: str = "auto",
    cpu_threads: int = 0,
    num_workers: int = 1,
    chunk_seconds: float = 0,
    initial_prompt: str | None = None,
) -> tuple[BaseProcess, mp.Queue]:
    """Start STT in a subprocess and return ``(process, queue)``."""
    ctx = mp.get_context("spawn")
    out_queue: mp.Queue = ctx.Queue()
    process = ctx.Process(
        target=_transcribe_worker,
        args=(
            out_queue,
            str(Path(audio_path).expanduser().resolve()),
            {
                "model_size": model_size,
                "speed_profile": speed_profile,
                "language": language,
                "compute_type": compute_type,
                "device": device,
                "cpu_threads": cpu_threads,
                "num_workers": num_workers,
                "chunk_seconds": chunk_seconds,
                "initial_prompt": initial_prompt,
            },
        ),
        daemon=True,
    )
    process.start()
    return process, out_queue


def drain_queue(out_queue: mp.Queue) -> list[dict[str, Any]]:
    """Return all currently available STT worker messages."""
    messages: list[dict[str, Any]] = []
    while True:
        try:
            messages.append(out_queue.get_nowait())
        except queue.Empty:
            break
    return messages


def terminate_process(process: BaseProcess | None, *, timeout: float = 2.0) -> bool:
    """Terminate a child process. Returns true if the process is no longer alive."""
    if process is None:
        return True
    if not process.is_alive():
        process.join(timeout=0)
        return True
    process.terminate()
    process.join(timeout=timeout)
    if process.is_alive():
        process.kill()
        process.join(timeout=timeout)
    return not process.is_alive()


__all__ = [
    "drain_queue",
    "start_transcription_process",
    "terminate_process",
]
