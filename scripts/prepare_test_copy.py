#!/usr/bin/env python3
"""Prepare a copy of FluentFlow's runtime data for a test run on real data,
and print the command that starts a service on it (decision 8.11).

What it does, reading the real data directory and writing only the copy:

- the task database (and the event database) copied with sqlite's online
  backup, so a service running on the real one is not disturbed;
- ``fluentflow_config.json`` and ``recommended_models.json`` copied, with
  automatic export to Feishu turned off in the copy;
- ``models`` linked, not copied (the transcription and speaker models);
- every step in the copy that has not reached its end closed as skipped:
  otherwise the test service carries on the tasks the real one is running,
  spends a model call each and writes into the folders beside their
  recordings (2026-10-26, caught once).

The printed command starts the service with a budget on model calls by
default (``--max-calls``, ``--max-usd``, ``--max-cny``) and, with ``--fake``,
the fake model, which calls no model at all. It also sets
``FLUENTFLOW_TEST_COPY``: the copied tasks still point at the user's real
notes, and the service then does not watch them or their Feishu documents.

    .venv/bin/python scripts/prepare_test_copy.py --into <scratch dir> --fake --port 8013
"""

from __future__ import annotations

import argparse
import json
import shlex
import sqlite3
import sys
from pathlib import Path

DEFAULT_SOURCE = Path.home() / "Library" / "Application Support" / "FluentFlow"
DATABASES = ("fluentflow_jobs.sqlite", "fluentflow_events.sqlite")
FILES = ("fluentflow_config.json", "recommended_models.json")
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
    return {"into": into, "closed_steps": close_open_steps(into / DATABASES[0])}


def start_command(into: Path, *, port: int, fake: bool, max_calls: int | None, max_usd: float | None,
                  max_cny: float | None, repo: Path) -> str:
    env = {
        "FLUENTFLOW_DATA_DIR": str(into),
        "FLUENTFLOW_CONFIG_PATH": str(into / "fluentflow_config.json"),
        "FLUENTFLOW_LOCAL_PORT": str(port),
        "FLUENTFLOW_TEST_COPY": "1",
    }
    if fake:
        env["FLUENTFLOW_FAKE_MODEL"] = "1"
    for name, value in (("FLUENTFLOW_TEST_MAX_CALLS", max_calls), ("FLUENTFLOW_TEST_MAX_USD", max_usd),
                        ("FLUENTFLOW_TEST_MAX_CNY", max_cny)):
        if value is not None:
            env[name] = f"{value:g}" if isinstance(value, float) else str(value)
    assignments = " ".join(f"{name}={shlex.quote(value)}" for name, value in env.items())
    python = shlex.quote(str(repo / ".venv" / "bin" / "python"))
    return (f"cd {shlex.quote(str(repo))} && {assignments} {python} -m uvicorn backend.local_main:app "
            f"--host 127.0.0.1 --port {port}")


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
    args = parser.parse_args(argv)
    done = prepare(args.source, args.into)
    limits = (None, None, None) if args.no_limit else (args.max_calls, args.max_usd, args.max_cny)
    repo = Path(__file__).resolve().parents[1]
    print(f"copy: {done['into']}  (steps closed in the copy: {done['closed_steps']})")
    print("start the test service with:")
    print(start_command(done["into"], port=args.port, fake=args.fake, max_calls=limits[0], max_usd=limits[1],
                        max_cny=limits[2], repo=repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
