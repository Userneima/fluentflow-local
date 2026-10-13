"""A long step's line back to its task: how far it got, and whether to stop.

Removing the breath gaps runs in a worker thread for minutes to hours on a long
screen recording, and it used to have no line back at all. It wrote one number
when it started and nothing until it finished, so a working task read as hung;
and cancelling the task stopped the coroutine waiting on it while the thread
went on re-encoding the whole recording, holding the render lock, with every
later task queued behind it (measured 2026-10-13: a cancelled 58-minute task
kept two ffmpeg processes busy until they were killed by hand).

``TaskWatch`` is that line. Its ``runner`` replaces ``silence_cuts.run_command``
for every command the step runs:

- each command is started under ``Popen`` and polled, so cancelling kills the
  process that is running and refuses to start the next one;
- an ffmpeg encode inside a phase that declared how many seconds of media it
  will write reports ``-progress``, and the phase's share of those seconds is
  handed to ``on_progress`` with the phase's label.

Commands that write to stdout themselves (probes, analysis printing to ``-``)
are run unchanged apart from being cancellable.
"""

from __future__ import annotations

import itertools
import os
import subprocess
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Sequence

from backend.core import device_profile

ProgressCallback = Callable[[str, "float | None"], None]

_POLL_SECONDS = 0.25
_COMMAND_IDS = itertools.count()


class TaskCancelled(Exception):
    """The task this step belongs to was cancelled; the step stops here."""


class TaskWatch:
    def __init__(
        self,
        on_progress: ProgressCallback | None = None,
        cancelled: threading.Event | None = None,
    ) -> None:
        self._on_progress = on_progress
        self.cancelled = cancelled or threading.Event()
        self._lock = threading.Lock()
        self._label = ""
        self._expected = 0.0
        self._written: dict[int, float] = {}

    # ── cancellation ────────────────────────────────────────────────────────

    def cancel(self) -> None:
        self.cancelled.set()

    def check(self) -> None:
        if self.cancelled.is_set():
            raise TaskCancelled("任务已取消")

    # ── progress ────────────────────────────────────────────────────────────

    @contextmanager
    def phase(self, label: str, expected_seconds: float | None = None) -> Iterator[None]:
        """Name what is running. With ``expected_seconds``, the ffmpeg encodes run
        inside it report how much of that much media they have written."""
        self.check()
        with self._lock:
            self._label = label
            self._expected = max(float(expected_seconds or 0.0), 0.0)
            self._written = {}
        self._emit(0.0 if self._expected else None)
        try:
            yield
        finally:
            with self._lock:
                self._expected = 0.0
                self._written = {}

    def _emit(self, fraction: float | None) -> None:
        if self._on_progress is None or self.cancelled.is_set():
            return
        try:
            self._on_progress(self._label, fraction)
        except Exception:  # noqa: BLE001 - a progress write never fails the step
            pass

    def _wrote(self, key: int, seconds: float) -> None:
        with self._lock:
            if not self._expected:
                return
            self._written[key] = max(seconds, self._written.get(key, 0.0))
            fraction = min(sum(self._written.values()) / self._expected, 1.0)
        self._emit(fraction)

    # ── running commands ────────────────────────────────────────────────────

    def runner(self, command: Sequence[str], timeout: float = 14400) -> "subprocess.CompletedProcess[str]":
        """``silence_cuts.run_command``, but cancellable and, for encodes inside a
        measured phase, reporting how far they got."""
        self.check()
        # Held to the CPU share the user chose, like ``run_command``.
        argv = device_profile.limit_ffmpeg_threads(command)
        reports = self._reports_progress(argv)
        if reports:
            argv = [argv[0], "-progress", "pipe:1", "-nostats", *argv[1:]]
        key = next(_COMMAND_IDS)
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        device_profile.lower_priority(process.pid)
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def read_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                if not reports:
                    stdout_chunks.append(line)
                    continue
                name, _, value = line.strip().partition("=")
                if name in {"out_time_us", "out_time_ms"} and value.lstrip("-").isdigit():
                    # ffmpeg writes microseconds under both names.
                    self._wrote(key, max(int(value), 0) / 1_000_000)

        def read_stderr() -> None:
            assert process.stderr is not None
            stderr_chunks.append(process.stderr.read())

        readers = [threading.Thread(target=read_stdout, daemon=True), threading.Thread(target=read_stderr, daemon=True)]
        for reader in readers:
            reader.start()
        started = time.monotonic()
        try:
            while True:
                try:
                    # Returns the moment the command ends: a step runs dozens of
                    # short probes, and a fixed sleep per command added up.
                    process.wait(timeout=_POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if self.cancelled.is_set():
                    _stop(process)
                    raise TaskCancelled("任务已取消")
                if time.monotonic() - started > timeout:
                    _stop(process)
                    raise subprocess.TimeoutExpired(argv, timeout)
        finally:
            for reader in readers:
                reader.join(timeout=5)
        return subprocess.CompletedProcess(argv, process.returncode, "".join(stdout_chunks), "".join(stderr_chunks))

    def _reports_progress(self, argv: list[str]) -> bool:
        if not self._expected or os.path.basename(argv[0]) != "ffmpeg" or "-progress" in argv:
            return False
        # A command that already writes to stdout keeps it to itself.
        return not any(part in {"-", "pipe:1", "pipe:"} or part.endswith("file=-") for part in argv)


def _stop(process: "subprocess.Popen[str]") -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


__all__ = ["ProgressCallback", "TaskCancelled", "TaskWatch"]
