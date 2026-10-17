"""Run speaker separation in a child process the service can stop.

Speaker separation used to run on a thread of the service. A thread cannot be
stopped: past its time budget the task moved on without labels, but the thread
stayed inside the separator, holding four cores, for as long as the separator
liked. On 2026-10-16/17 one such run kept the service from answering the page
and agents for about 16 hours, until it was killed by hand. A child process is
stopped when its budget is spent or its task is cancelled, and whatever it does
in the meantime cannot hold the service's own threads.
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import queue
import traceback
from pathlib import Path
from typing import Any

from backend.core.speaker_turns import SpeakerTurn
from backend.core.stt_process import STT_FOLDER_ACCESS_LOST, terminate_process

_POLL_SECONDS = 0.5


def _diarize_worker(out_queue: mp.Queue, audio_path: str) -> None:
    try:
        from backend.core.speaker_diarization import diarize_audio

        turns = diarize_audio(audio_path)
        out_queue.put({
            "type": "result",
            "turns": [(float(turn.start), float(turn.end), str(turn.speaker)) for turn in turns],
        })
    except BaseException as exc:  # noqa: BLE001 - reported to the parent, which decides
        try:
            out_queue.put({"type": "error", "error": str(exc) or type(exc).__name__,
                           "traceback": traceback.format_exc(limit=12)})
        except Exception:  # noqa: BLE001 - the parent sees the exit code instead
            pass


async def diarize_in_child(
    audio_path: str | Path, *, budget_seconds: float, worker: Any = _diarize_worker,
) -> list[SpeakerTurn]:
    """Separate speakers in a child process.

    Raises ``asyncio.TimeoutError`` once ``budget_seconds`` is spent, after the
    child has been stopped; a cancelled caller stops it too. Any failure inside
    the child comes back as ``RuntimeError`` with its message.
    """
    try:
        os.getcwd()
    except PermissionError as exc:
        raise RuntimeError(STT_FOLDER_ACCESS_LOST) from exc
    ctx = mp.get_context("spawn")
    out_queue: mp.Queue = ctx.Queue()
    process = ctx.Process(
        target=worker,
        args=(out_queue, str(Path(audio_path).expanduser().resolve())),
        daemon=True,
    )
    process.start()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_seconds
    try:
        while True:
            alive = process.is_alive()
            try:
                # The result may land in the queue just after the child exits.
                message = out_queue.get(timeout=1.0) if not alive else out_queue.get_nowait()
            except queue.Empty:
                message = None
            if message is not None:
                if message.get("type") == "result":
                    return [SpeakerTurn(start=a, end=b, speaker=s) for a, b, s in message["turns"]]
                raise RuntimeError(str(message.get("error") or "说话人区分失败"))
            if not alive:
                raise RuntimeError(f"说话人区分的进程意外退出（退出码 {process.exitcode}）")
            if loop.time() >= deadline:
                raise asyncio.TimeoutError
            await asyncio.sleep(_POLL_SECONDS)
    finally:
        # Every way out stops the child: a result (it is finishing anyway), a
        # spent budget, a cancelled task, an error.
        await loop.run_in_executor(None, terminate_process, process)
        out_queue.close()


__all__ = ["diarize_in_child"]
