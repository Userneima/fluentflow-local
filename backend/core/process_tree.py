"""Stopping a child together with everything it started.

``Popen.kill`` stops one process. On Windows the programs this service runs are
often a launcher in front of the real work: ``claude`` installed with npm is
``claude.cmd``, a cmd.exe that starts node. Killing the launcher left node
running with the output pipes still open, so a call that had timed out went on
waiting for it to finish on its own (found 2026-10-16: a 0.5 s login check
returned after 5 s). ``taskkill /T`` stops the tree.

On macOS and Linux a child started in its own session is stopped through its
process group by the callers that need it; here it is only the child.
"""

from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)

_TASKKILL_TIMEOUT_SECONDS = 10


def kill_tree(process: "subprocess.Popen") -> None:
    """Kill ``process`` and, on Windows, every process it started."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=_TASKKILL_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            logger.info("taskkill could not stop process %s; killing it alone", process.pid, exc_info=True)
    try:
        process.kill()
    except OSError:
        pass  # already gone


__all__ = ["kill_tree"]
