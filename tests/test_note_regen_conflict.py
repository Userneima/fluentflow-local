"""Regression coverage for the conflict check that discarded a finished note.

`/regenerate-summary` snapshots the job's result, generates for minutes, then
refuses to write if the result moved. The check compared the *whole* result
dict, so an unrelated write vetoed the note: an autosave stored the identical
summary text and only bumped `summary_edited_at`, and nine minutes of
generation were thrown away with a 409.

The guarantee to keep is narrow — a real edit must never be clobbered.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from backend.core.job_store import (
    finalize_job_result_if_unchanged,
    get_job,
    note_conflict_fingerprint,
    upsert_job,
)

NOTE = "# 完整笔记\n\n正文。"
TRANSCRIPT = "转录文本。" * 100


class NoteConflictFingerprintTests(TestCase):
    def test_bookkeeping_that_moves_on_its_own_is_not_a_conflict(self):
        before = {"summary_markdown": NOTE, "transcript_text": TRANSCRIPT, "summary_edited_at": "T1"}
        after = {
            "summary_markdown": NOTE,
            "transcript_text": TRANSCRIPT,
            "summary_edited_at": "T2",
            "summary_edited": True,
            "source_retention_expires_at": "2026-09-01T00:00:00+08:00",
            "artifacts": {"playback_audio": {"filename": "a.mp3"}},
        }
        self.assertEqual(note_conflict_fingerprint(before), note_conflict_fingerprint(after))

    def test_an_edited_note_is_a_conflict(self):
        before = {"summary_markdown": NOTE, "transcript_text": TRANSCRIPT}
        after = {"summary_markdown": NOTE + "\n\n我手写的补充。", "transcript_text": TRANSCRIPT}
        self.assertNotEqual(note_conflict_fingerprint(before), note_conflict_fingerprint(after))

    def test_an_edited_transcript_is_a_conflict(self):
        before = {"summary_markdown": NOTE, "transcript_text": TRANSCRIPT}
        after = {"summary_markdown": NOTE, "transcript_text": TRANSCRIPT + "补一句。"}
        self.assertNotEqual(note_conflict_fingerprint(before), note_conflict_fingerprint(after))

    def test_a_missing_result_has_no_fingerprint(self):
        self.assertIsNone(note_conflict_fingerprint(None))


class FinalizeIfUnchangedTests(TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "jobs.sqlite"
        self.snapshot = {
            "task_id": "task-a",
            "summary_markdown": NOTE,
            "transcript_text": TRANSCRIPT,
            "summary_edited_at": "2026-08-02T14:00:00+08:00",
        }
        upsert_job(
            task_id="task-a",
            status="completed",
            client_id="local-single-user",
            stage="done",
            progress=100,
            result=self.snapshot,
            db_path=self.db_path,
        )

    def _store(self, result):
        upsert_job(
            task_id="task-a",
            status="completed",
            client_id="local-single-user",
            result=result,
            db_path=self.db_path,
        )

    def _finalize(self, regenerated):
        return finalize_job_result_if_unchanged(
            task_id="task-a",
            expected_result=self.snapshot,
            result=regenerated,
            status="completed",
            client_id="local-single-user",
            stage="done",
            progress=100,
            summary_status="completed",
            db_path=self.db_path,
        )

    def test_an_autosave_that_rewrote_the_same_text_no_longer_discards_the_note(self):
        # Exactly the observed failure: identical summary, new timestamp.
        self._store({**self.snapshot, "summary_edited_at": "2026-08-02T17:22:39+08:00", "summary_edited": True})
        regenerated = {**self.snapshot, "summary_markdown": "# 重生后的完整笔记\n\n很长的正文。"}

        self.assertIsNotNone(self._finalize(regenerated))
        stored = get_job("task-a", client_id="local-single-user", db_path=self.db_path)["result"]
        self.assertEqual(stored["summary_markdown"], "# 重生后的完整笔记\n\n很长的正文。")

    def test_a_real_note_edit_still_wins_over_the_regenerated_note(self):
        self._store({**self.snapshot, "summary_markdown": NOTE + "\n\n我手写的补充。"})

        self.assertIsNone(self._finalize({**self.snapshot, "summary_markdown": "# 重生"}))
        stored = get_job("task-a", client_id="local-single-user", db_path=self.db_path)["result"]
        self.assertIn("我手写的补充。", stored["summary_markdown"])

    def test_a_transcript_edit_still_blocks_a_note_written_from_the_old_text(self):
        self._store({**self.snapshot, "transcript_text": TRANSCRIPT + "补一句。"})
        self.assertIsNone(self._finalize({**self.snapshot, "summary_markdown": "# 重生"}))

    def test_a_cancelled_job_is_never_finalized(self):
        upsert_job(task_id="task-a", client_id="local-single-user", status="cancelled", db_path=self.db_path)
        self.assertIsNone(self._finalize({**self.snapshot, "summary_markdown": "# 重生"}))
