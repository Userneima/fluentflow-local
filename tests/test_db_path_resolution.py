"""Regression coverage for where the job and event databases actually get written.

`job_store` and `event_logger` used to declare `db_path: Path | str =
DEFAULT_DB_PATH`. Python evaluates a default argument once, at import, so the
path froze before any test could redirect it: `tests/test_note_regen_stream.py`
pointed FluentFlow at a temp database and still inserted 5 job rows and 7 event
rows into the developer's live database under %APPDATA%/FluentFlow.

Both modules now resolve the path per call. Two things are pinned here: no
public function may bind the path as a default again, and redirecting the
configured path must move a real write off the default database.
"""

import inspect
import os
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import backend.core.event_logger as event_logger
import backend.core.job_store as job_store
from backend.core.local_entry_guards import claim_task_id, require_owned_task_id

CLIENT = "local-single-user"


def _snapshot(path: Path) -> tuple[int, int] | None:
    """Enough of a file's state to tell whether anything wrote to it."""
    if not path.exists():
        return None
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


def _path_defaults(module) -> dict[str, object]:
    """The declared default of every public `db_path` parameter in `module`."""
    defaults: dict[str, object] = {}
    for name, func in vars(module).items():
        if name.startswith("_") or not inspect.isfunction(func):
            continue
        if func.__module__ != module.__name__:
            continue
        parameter = inspect.signature(func).parameters.get("db_path")
        if parameter is not None and parameter.default is not inspect.Parameter.empty:
            defaults[name] = parameter.default
    return defaults


class DatabasePathDefaultsTests(TestCase):
    def test_no_public_job_store_function_binds_the_path_at_import(self):
        defaults = _path_defaults(job_store)
        self.assertIn("upsert_job", defaults)  # guard against an empty scan passing
        self.assertEqual({name for name, value in defaults.items() if value is not None}, set())

    def test_no_public_event_logger_function_binds_the_path_at_import(self):
        defaults = _path_defaults(event_logger)
        self.assertIn("log_event", defaults)
        self.assertEqual({name for name, value in defaults.items() if value is not None}, set())

    def test_resolve_db_path_follows_the_configured_path_at_call_time(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            jobs = Path(temp_dir) / "jobs.sqlite"
            events = Path(temp_dir) / "events.sqlite"
            with patch.dict(os.environ, {
                "FLUENTFLOW_JOB_DB_PATH": str(jobs),
                "FLUENTFLOW_EVENT_DB_PATH": str(events),
            }):
                self.assertEqual(job_store.resolve_db_path(), jobs)
                self.assertEqual(event_logger.resolve_db_path(), events)

    def test_an_explicit_path_still_wins_over_the_configured_one(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            explicit = Path(temp_dir) / "explicit.sqlite"
            with patch.dict(os.environ, {
                "FLUENTFLOW_JOB_DB_PATH": str(Path(temp_dir) / "configured.sqlite"),
            }):
                self.assertEqual(job_store.resolve_db_path(explicit), explicit)
                self.assertEqual(job_store.resolve_db_path(str(explicit)), explicit)


class RedirectedWriteTests(TestCase):
    """`DEFAULT_DB_PATH` is the path each module snapshotted at import — the very
    file the frozen defaults used to write to. It must stay untouched here."""

    def setUp(self):
        self._tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.jobs_db = Path(self._tmp.name) / "jobs.sqlite"
        self.events_db = Path(self._tmp.name) / "events.sqlite"
        self.real_jobs_db = job_store.DEFAULT_DB_PATH
        self.real_events_db = event_logger.DEFAULT_DB_PATH
        self._real_jobs_before = _snapshot(self.real_jobs_db)
        self._real_events_before = _snapshot(self.real_events_db)
        self.addCleanup(self._assert_real_databases_untouched)
        self._redirect = patch.dict(os.environ, {
            "FLUENTFLOW_JOB_DB_PATH": str(self.jobs_db),
            "FLUENTFLOW_EVENT_DB_PATH": str(self.events_db),
        })
        self._redirect.start()
        self.addCleanup(self._redirect.stop)

    def _assert_real_databases_untouched(self):
        self.assertEqual(_snapshot(self.real_jobs_db), self._real_jobs_before)
        self.assertEqual(_snapshot(self.real_events_db), self._real_events_before)

    @staticmethod
    def _row_count(db_path: Path, table: str) -> int:
        with sqlite3.connect(db_path) as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def test_a_job_write_with_no_explicit_path_lands_in_the_redirected_database(self):
        job_store.upsert_job(
            task_id="redirect-probe",
            status="completed",
            client_id=CLIENT,
            result={"task_id": "redirect-probe", "summary_markdown": "# 笔记"},
        )

        self.assertTrue(self.jobs_db.exists())
        self.assertEqual(self._row_count(self.jobs_db, "jobs"), 1)
        stored = job_store.get_job("redirect-probe", client_id=CLIENT)
        self.assertEqual(stored["result"]["summary_markdown"], "# 笔记")

    def test_an_event_write_with_no_explicit_path_lands_in_the_redirected_database(self):
        event_id = event_logger.log_event(task_id="redirect-probe", event_name="summary_completed")

        self.assertIsNotNone(event_id)
        self.assertTrue(self.events_db.exists())
        self.assertEqual(self._row_count(self.events_db, "events"), 1)

    def test_the_entry_guards_reserve_task_ids_in_the_redirected_database(self):
        # `claim_task_id`/`require_owned_task_id` hold no path of their own; they
        # inherit whatever the job store resolves at call time.
        task_id = claim_task_id(None, client_id=CLIENT)

        self.assertEqual(require_owned_task_id(task_id, client_id=CLIENT), task_id)
        self.assertEqual(self._row_count(self.jobs_db, "jobs"), 1)
