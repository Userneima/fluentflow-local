"""Keep this Mac from idle-sleeping while any local task is queued or running.

Measured on 2026-09-24: a 16-minute recording queued at 05:45 took 69 minutes,
of which its own work was 4. The system log shows the machine asleep for the
rest, waking for a few seconds every quarter hour. A batch handed over before
walking away ran at the speed of those wakes.

One ``caffeinate -i`` process is held from the first active task to the last,
so the machine sleeps normally the moment the queue empties. ``-w`` ties it to
this server's pid as well: a server that dies without its ``finally`` does not
leave the Mac unable to sleep. Idle sleep only — a closed lid, the user's own
sleep command, or a low battery still win.

On Windows a small Python process holds the same request through
``SetThreadExecutionState`` (system required, display free to turn off) and
reads its stdin, which this server holds open: it exits when released, and
also when this server dies, because the pipe then closes (10-18 review,
item 8: a long CPU transcription on Windows could stop when the PC slept).

Elsewhere, or where ``caffeinate`` is missing, this does nothing.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)


# ES_CONTINUOUS | ES_SYSTEM_REQUIRED, held until stdin closes.
WINDOWS_HOLDER = (
    "import ctypes, sys; "
    "ctypes.windll.kernel32.SetThreadExecutionState(0x80000001); "
    "sys.stdin.read()"
)


class KeepAwake:
    def __init__(self, command: list[str] | None = None) -> None:
        self._command = command
        self._active = 0
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def _resolved_command(self) -> list[str] | None:
        if self._command is not None:
            return self._command
        if sys.platform == "win32":
            return [sys.executable, "-c", WINDOWS_HOLDER]
        if sys.platform != "darwin":
            return None
        binary = shutil.which("caffeinate")
        if not binary:
            return None
        return [binary, "-i", "-w", str(os.getpid())]

    @property
    def active(self) -> int:
        return self._active

    @property
    def holding(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def acquire(self) -> None:
        with self._lock:
            self._active += 1
            if self.holding:
                return
            command = self._resolved_command()
            if not command:
                return
            try:
                self._process = subprocess.Popen(
                    command,
                    # The Windows holder lives while this pipe is open;
                    # caffeinate ignores it.
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    # No console window flashing up on Windows.
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                self._process = None
                logger.warning("could not keep the machine awake for queued tasks: %s", exc)

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)
            if self._active or self._process is None:
                return
            process, self._process = self._process, None
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


KEEP_AWAKE = KeepAwake()
