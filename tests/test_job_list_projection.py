"""Regression coverage for the job-list projection that destroyed a note.

A list row used to ship a 240-char preview under the canonical
`summary_markdown` / `transcript_text` names. The editor opened one of those
rows, could not tell it apart from a real record, and its autosave wrote the
preview back over a 13k-char note. The projection must never speak in the
canonical field names again.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from backend.core.job_store import get_job, list_job_summaries, upsert_job
from backend.core.job_views import RECORD_BODY_FIELDS, assert_is_list_row

FULL_NOTE = "# 完整笔记\n\n" + ("这是一段很长的笔记正文。" * 200)
FULL_TRANSCRIPT = "这是一段很长的转录文本。" * 400


class JobListProjectionTests(TestCase):
    def setUp(self):
        # Windows keeps the sqlite file handle alive past the last connection,
        # so teardown must not treat a locked temp file as a test failure.
        self._tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "jobs.sqlite"
        upsert_job(
            task_id="task-a",
            status="completed",
            client_id="local-single-user",
            stage="done",
            progress=100,
            result={
                "task_id": "task-a",
                "status": "completed",
                "summary_markdown": FULL_NOTE,
                "summary_status": "completed",
                "transcript_text": FULL_TRANSCRIPT,
            },
            db_path=self.db_path,
        )

    def _row(self):
        rows = list_job_summaries(db_path=self.db_path)
        self.assertEqual(len(rows), 1)
        return rows[0]["result"]

    def test_a_list_row_never_carries_truncated_text_under_the_canonical_names(self):
        result = self._row()
        self.assertNotIn("summary_markdown", result)
        self.assertNotIn("transcript_text", result)

    def test_a_list_row_marks_itself_partial_so_editors_refuse_to_save_it(self):
        self.assertIs(self._row()["result_partial"], True)

    def test_a_list_row_still_previews_the_note_and_reports_its_real_size(self):
        result = self._row()
        self.assertEqual(result["summary_preview"], FULL_NOTE[:240])
        self.assertEqual(result["summary_markdown_chars"], len(FULL_NOTE))
        self.assertEqual(result["transcript_text_preview"], FULL_TRANSCRIPT[:240])
        self.assertEqual(result["transcript_text_chars"], len(FULL_TRANSCRIPT))

    def test_a_row_carrying_a_body_field_is_rejected_rather_than_shipped(self):
        # The wall is checked, not assumed: reintroducing a body field under its
        # canonical name must fail here rather than reach an editor that saves it.
        with self.assertRaises(AssertionError) as caught:
            assert_is_list_row({"task_id": "t", "summary_preview": "x", "summary_markdown": "x"})
        self.assertIn("summary_markdown", str(caught.exception))

    def test_every_body_field_is_guarded_not_just_the_note(self):
        for field in RECORD_BODY_FIELDS:
            with self.subTest(field=field):
                with self.assertRaises(AssertionError):
                    assert_is_list_row({"task_id": "t", field: "anything"})

    def test_an_unknown_metadata_field_is_dropped_rather_than_passed_through(self):
        # Allowlisted, not filtered: a new body-sized field added to results
        # cannot reach list rows by default.
        row = self._row()
        self.assertNotIn("speaker_diarization", row)
        self.assertNotIn("chapter_coverage", row)

    def test_reading_one_job_still_returns_the_whole_record(self):
        result = get_job("task-a", db_path=self.db_path)["result"]
        self.assertEqual(result["summary_markdown"], FULL_NOTE)
        self.assertEqual(result["transcript_text"], FULL_TRANSCRIPT)
        self.assertFalse(result.get("result_partial"))
