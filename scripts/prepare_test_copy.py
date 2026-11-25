#!/usr/bin/env python3
"""Prepare a copy of FluentFlow's runtime data for a test run on real data,
and print the command that starts a service on it (decision 8.11).

What it does, reading the real data directory and writing only the copy:

- the task database (and the event database) copied with sqlite's online
  backup, so a service running on the real one is not disturbed;
- ``fluentflow_config.json`` and ``recommended_models.json`` copied, with
  automatic export to Feishu turned off in the copy;
- ``models`` linked, not copied (the transcription and speaker models);
- every step in the copy that has not reached its end closed as skipped, and
  every task in the copy still queued or running marked failed: otherwise
  the test service carries on (or queues again from the original) the tasks
  the real one is running, spends model calls on them and writes into the
  folders beside their recordings (2026-10-26, caught once);
- the list of the copied tasks (``test_copy_tasks.json``): the test service
  only lets new tasks be worked on (``backend/core/test_copy.py``).

The printed command starts the service with a budget on model calls by
default (``--max-calls``, ``--max-usd``, ``--max-cny``) and, with ``--fake``,
the fake model, which calls no model at all. It also sets
``FLUENTFLOW_TEST_COPY``: the copied tasks still point at the user's real
notes, and the service then does not watch them or their Feishu documents.

It runs the service with this checkout's ``.venv``, or, in a worktree that has
none, the main checkout's; and gives it a throwaway access token, printed with
a line that calls the MCP tool functions in ``scripts/fluentflow_mcp`` against
the copy: the MCP tools a conversation is connected to reach port 8000 and the
main checkout's code, not this one. ``--frontend-dir`` serves the page from a
build of its own (``node scripts/write_frontend_config.js && npx vite build
--config vite.local.config.mjs --outDir <dir>``); the copy's page always sends its requests back to the copy
(``backend/core/test_copy.py``, decision 8.29).

    python3 scripts/prepare_test_copy.py --into <scratch dir> --fake --port 8013

On Windows the printed command is for PowerShell, and takes the variables it
sets out of the window again when the service ends.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.runtime_paths import app_data_root  # noqa: E402

DEFAULT_SOURCE = app_data_root()
DATABASES = ("fluentflow_jobs.sqlite", "fluentflow_events.sqlite")
FILES = ("fluentflow_config.json", "recommended_models.json")
TASKS_FILE = "test_copy_tasks.json"
DEFAULT_MAX_CALLS = 40
DEFAULT_MAX_USD = 1.0
DEFAULT_MAX_CNY = 1.0


def backup_database(source: Path, target: Path) -> None:
    """``source`` copied to ``target`` through sqlite's backup, read-only on
    the source."""
    target.unlink(missing_ok=True)
    reading = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    writing = sqlite3.connect(target)
    try:
        reading.backup(writing)
    finally:
        writing.close()
        reading.close()


def close_open_steps(database: Path) -> int:
    """Every step in the copy not at an end is closed as skipped; how many."""
    connection = sqlite3.connect(database)
    try:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
        if "job_steps" not in tables:
            return 0
        changed = connection.execute(
            "update job_steps set status='skipped' where status in ('pending','running')"
        ).rowcount
        connection.commit()
        return changed
    finally:
        connection.close()


UNFINISHED_REASON = "test_copy_unfinished"


def close_open_tasks(database: Path) -> int:
    """Every task in the copy still queued or running is marked failed; how
    many. Not as cut off by a restart, so nothing offers to carry it on."""
    connection = sqlite3.connect(database)
    try:
        changed = connection.execute(
            "update jobs set status='failed', error_reason=? where status in ('queued','running')",
            (UNFINISHED_REASON,),
        ).rowcount
        connection.commit()
        return changed
    finally:
        connection.close()


def write_task_list(database: Path, target: Path) -> int:
    connection = sqlite3.connect(database)
    try:
        ids = [row[0] for row in connection.execute("select task_id from jobs order by created_at")]
    finally:
        connection.close()
    target.write_text(json.dumps(ids), encoding="utf-8")
    return len(ids)


def copy_config(source: Path, target: Path) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    preferences = data.get("preferences") if isinstance(data.get("preferences"), dict) else None
    for holder in (data, preferences):
        if isinstance(holder, dict) and "export_to_lark" in holder:
            holder["export_to_lark"] = False
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare(source: Path, into: Path) -> dict[str, object]:
    source, into = source.expanduser().resolve(), into.expanduser().resolve()
    if into == source or source in into.parents:
        raise SystemExit(f"the copy must not be inside the real data directory: {into}")
    if (source / TASKS_FILE).is_file():
        # A window the printed command ran in can still name the copy as the
        # data directory (FLUENTFLOW_DATA_DIR, the default --source).
        raise SystemExit(f"{source} is a test copy ({TASKS_FILE} is in it), not the real data: "
                         "pass --source the real data directory")
    if not (source / DATABASES[0]).is_file():
        raise SystemExit(f"no task database in {source}")
    into.mkdir(parents=True, exist_ok=True)
    for name in DATABASES:
        if (source / name).is_file():
            backup_database(source / name, into / name)
    for name in FILES:
        if not (source / name).is_file():
            continue
        if name == "fluentflow_config.json":
            copy_config(source / name, into / name)
        else:
            (into / name).write_bytes((source / name).read_bytes())
    link = into / "models"
    if (source / "models").is_dir() and not link.exists():
        link.symlink_to(source / "models", target_is_directory=True)
    database = into / DATABASES[0]
    return {
        "into": into,
        "closed_steps": close_open_steps(database),
        "closed_tasks": close_open_tasks(database),
        "copied_tasks": write_task_list(database, into / TASKS_FILE),
    }


def _venv_python(checkout: Path, windows: bool) -> Path:
    return checkout / ".venv" / ("Scripts/python.exe" if windows else "bin/python")


def main_checkout(repo: Path) -> Path | None:
    """The checkout a worktree was made from, or None outside git."""
    try:
        common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(common).parent if common else None


def interpreter(repo: Path, *, windows: bool = os.name == "nt") -> Path:
    """This checkout's ``.venv`` Python; in a worktree without one (T3 and
    Claude Code make them without), the main checkout's. Run from this
    checkout either way, so it serves this checkout's code."""
    own = _venv_python(repo, windows)
    if own.exists():
        return own
    main = main_checkout(repo)
    if main is not None and _venv_python(main, windows).exists():
        return _venv_python(main, windows)
    return own


def start_command(into: Path, *, port: int, fake: bool, max_calls: int | None, max_usd: float | None,
                  max_cny: float | None, repo: Path, token: str, python: Path | None = None,
                  frontend_dir: Path | None = None, windows: bool = os.name == "nt") -> str:
    """The command that starts the test service: PowerShell on Windows, a POSIX shell elsewhere."""
    env = {
        "FLUENTFLOW_DATA_DIR": str(into),
        "FLUENTFLOW_CONFIG_PATH": str(into / "fluentflow_config.json"),
        "FLUENTFLOW_LOCAL_PORT": str(port),
        "FLUENTFLOW_TEST_COPY": "1",
        # The Agent API is closed without one, and a worktree has no .env.
        "FLUENTFLOW_ACCESS_TOKEN": token,
    }
    if frontend_dir is not None:
        env["FLUENTFLOW_TEST_FRONTEND_DIR"] = str(frontend_dir)
    if fake:
        env["FLUENTFLOW_FAKE_MODEL"] = "1"
    for name, value in (("FLUENTFLOW_TEST_MAX_CALLS", max_calls), ("FLUENTFLOW_TEST_MAX_USD", max_usd),
                        ("FLUENTFLOW_TEST_MAX_CNY", max_cny)):
        if value is not None:
            env[name] = f"{value:g}" if isinstance(value, float) else str(value)
    python = python or _venv_python(repo, windows)
    return _run_in(repo, env, python, f"-m uvicorn backend.local_main:app --host 127.0.0.1 --port {port}", windows)


def tool_call_example(*, port: int, repo: Path, token: str, python: Path | None = None,
                      windows: bool = os.name == "nt") -> str:
    """A command calling one MCP tool function against the copy: replace
    ``notes.get_status()`` with the tool to try."""
    env = {"FLUENTFLOW_API_BASE": f"http://127.0.0.1:{port}", "FLUENTFLOW_ACCESS_TOKEN": token}
    code = "from scripts.fluentflow_mcp import notes; print(notes.get_status())"
    quoted = "'" + code + "'" if windows else shlex.quote(code)
    return _run_in(repo, env, python or _venv_python(repo, windows), f"-c {quoted}", windows)


def _run_in(repo: Path, env: dict[str, str], python: Path, arguments: str, windows: bool) -> str:
    if windows:
        def quote(value) -> str:
            return "'" + str(value).replace("'", "''") + "'"

        # $env: is set for the whole PowerShell window; put back as it was when
        # the command ends (finally runs on Ctrl+C too; $null removes one that
        # was not set), so a later command there neither reads the copy as the
        # real data nor loses the address or token the window had before.
        kept = "; ".join(f"$ff_{name} = $env:{name}" for name in env)
        assignments = "; ".join(f"$env:{name}={quote(value)}" for name, value in env.items())
        restored = "; ".join(f"$env:{name} = $ff_{name}" for name in env)
        return (f"Set-Location {quote(repo)}; {kept}; try {{ {assignments}; & {quote(python)} {arguments} }} "
                f"finally {{ {restored} }}")
    assignments = " ".join(f"{name}={shlex.quote(value)}" for name, value in env.items())
    return f"cd {shlex.quote(str(repo))} && {assignments} {shlex.quote(str(python))} {arguments}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--into", required=True, type=Path, help="where the copy goes (a scratch directory)")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="the real data directory")
    parser.add_argument("--port", type=int, default=8013)
    parser.add_argument("--fake", action="store_true", help="start with the fake model (tests of the flow)")
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    parser.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD)
    parser.add_argument("--max-cny", type=float, default=DEFAULT_MAX_CNY)
    parser.add_argument("--no-limit", action="store_true", help="no budget (only with the owner's go-ahead)")
    parser.add_argument("--frontend-dir", type=Path,
                        help="serve the page from this build (vite build --outDir), not frontend/dist-local")
    args = parser.parse_args(argv)
    # Before anything is written: a copy reused for another run keeps its tasks.
    frontend_dir = args.frontend_dir.expanduser().resolve() if args.frontend_dir else None
    if frontend_dir is not None and not (frontend_dir / "local.html").is_file():
        raise SystemExit(f"no local.html in {frontend_dir}: build into it with node scripts/write_frontend_config.js "
                         f"&& npx vite build --config vite.local.config.mjs --outDir {frontend_dir}")
    done = prepare(args.source, args.into)
    limits = (None, None, None) if args.no_limit else (args.max_calls, args.max_usd, args.max_cny)
    repo = Path(__file__).resolve().parents[1]
    python = interpreter(repo)
    token = secrets.token_urlsafe(18)
    print(f"copy: {done['into']}  ({done['copied_tasks']} tasks copied, read-only in the test service; "
          f"closed in the copy: {done['closed_steps']} steps, {done['closed_tasks']} tasks)")
    if not _venv_python(repo, os.name == "nt").exists():
        print(f"this checkout has no .venv: using {python}")
    print("start the test service with:")
    print(start_command(done["into"], port=args.port, fake=args.fake, max_calls=limits[0], max_usd=limits[1],
                        max_cny=limits[2], repo=repo, token=token, python=python, frontend_dir=frontend_dir))
    print("call an MCP tool function on it (the conversation's own MCP tools reach port 8000) with:")
    print(tool_call_example(port=args.port, repo=repo, token=token, python=python))
    return 0


if __name__ == "__main__":
    sys.exit(main())
