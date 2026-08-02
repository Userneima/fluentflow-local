"""Retention must never delete the user's own writing on a timer.

`enforce_history_retention`'s artifact cutoff does not trim artifacts — it
deletes the whole job row, and the note body lives in that row. It also runs
from the task-completion path, so the old 30-day default surfaced as "I
processed one new video and months of notes disappeared". These tests pin the
default OFF, and pin that expiring the bulky source media still works, because
that is the setting that is actually supposed to reclaim disk.
"""

import os
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from backend.core.history_retention import enforce_history_retention
from backend.core.local_retention_config import artifact_retention_days, source_retention_days

CLIENT = "local-single-user"


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).astimezone().isoformat(timespec="seconds")


def _completed_job(task_id: str, *, age_days: int, source_expired_days: int | None = None) -> dict:
    result = {"summary_markdown": f"# note {task_id}", "transcript_text": "..."}
    if source_expired_days is not None:
        result["source_file_available"] = True
        result["source_retention_expires_at"] = _iso(source_expired_days)
    return {
        "task_id": task_id,
        "status": "completed",
        "updated_at": _iso(age_days),
        "client_id": CLIENT,
        "result": result,
        "metadata": {},
    }


def _run(jobs: list[dict], *, artifact_days: int, source_days: int = 7) -> tuple[dict, list[str], list[dict]]:
    deleted: list[str] = []
    updated: list[dict] = []
    with patch("backend.core.history_retention.cleanup_task_all_files", return_value={}), \
         patch("backend.core.history_retention.cleanup_task_source_files", return_value={}):
        report = enforce_history_retention(
            CLIENT,
            keep_count=0,
            artifact_days=artifact_days,
            source_days=source_days,
            list_jobs=lambda **_: jobs,
            update_result=lambda task_id, result, **_: updated.append({"task_id": task_id, "result": result}),
            delete_jobs=lambda ids, **_: deleted.extend(ids),
        )
    return report, deleted, updated


class ArtifactRetentionDefaultTests(TestCase):
    def test_history_retention_is_off_unless_the_user_opts_in(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLUENTFLOW_ARTIFACT_RETENTION_DAYS", None)
            self.assertEqual(artifact_retention_days(), 0)

    def test_the_default_keeps_a_note_nobody_touched_for_a_year(self):
        jobs = [_completed_job("old-note", age_days=365)]
        _, deleted, _ = _run(jobs, artifact_days=artifact_retention_days())
        self.assertEqual(deleted, [])

    def test_an_explicit_cutoff_still_prunes_for_users_who_want_it(self):
        jobs = [_completed_job("old-note", age_days=40), _completed_job("recent-note", age_days=3)]
        _, deleted, _ = _run(jobs, artifact_days=30)
        self.assertEqual(deleted, ["old-note"])

    def test_an_invalid_setting_falls_back_to_off_not_to_deleting(self):
        with patch.dict(os.environ, {"FLUENTFLOW_ARTIFACT_RETENTION_DAYS": "soon"}):
            self.assertEqual(artifact_retention_days(), 0)


class SourceRetentionTests(TestCase):
    def test_expired_source_media_is_still_reclaimed_with_history_kept(self):
        # Disk pressure is source media's problem to solve; the note stays.
        jobs = [_completed_job("old-note", age_days=365, source_expired_days=1)]
        report, deleted, updated = _run(jobs, artifact_days=artifact_retention_days())
        self.assertEqual(deleted, [])
        self.assertEqual(report["expired_source_count"], 1)
        self.assertEqual(updated[0]["task_id"], "old-note")
        self.assertFalse(updated[0]["result"]["source_file_available"])
        self.assertEqual(updated[0]["result"]["summary_markdown"], "# note old-note")

    def test_source_media_still_expires_after_a_week_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLUENTFLOW_SOURCE_RETENTION_DAYS", None)
            self.assertEqual(source_retention_days(), 7)
