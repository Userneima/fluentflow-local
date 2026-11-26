#!/usr/bin/env python3
"""Start the local service with the environment of the window that asked for it.

The Windows launcher starts the service through Windows' process service
(launchers/windows/fluentflow-env.ps1, Start-FluentFlowService) so that no
window or agent shell owns it. Such a process gets the user's saved
environment, not the launching window's, and handing the window's variables
over on the command line would put keys and the access token where any process
listing shows them. So the launcher writes them to a file in the user's own
temp folder, and this reads the file, deletes it, and runs uvicorn in this
process.

    python scripts/run_service.py --env-file <file> --port 8000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def detach_stdin() -> None:
    """Nothing in the service reads the terminal, and a child that inherits it
    can sit waiting on it (an ffmpeg did, 2026-11-25). Windows' process
    service gives this process no console, but that is not something a child's
    behaviour should depend on: stdin becomes the null device, here and for
    everything started from here."""
    try:
        null = os.open(os.devnull, os.O_RDONLY)
    except OSError:
        return
    try:
        os.dup2(null, 0)
    except OSError:  # no stdin to replace
        pass
    finally:
        if null != 0:
            os.close(null)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--port", default="8000")
    args = parser.parse_args(argv)
    if args.env_file:
        path = Path(args.env_file)
        try:
            os.environ.update({str(k): str(v) for k, v in json.loads(path.read_text(encoding="utf-8-sig")).items()})
        finally:
            path.unlink(missing_ok=True)
    detach_stdin()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    import uvicorn

    uvicorn.run("backend.local_main:app", host="127.0.0.1", port=int(args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
