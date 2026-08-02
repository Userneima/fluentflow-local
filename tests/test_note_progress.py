"""Coverage for note-generation progress reporting.

Note generation is minutes of silent model calls. `/process` streamed stage
updates; regeneration did not, so a normal nine-minute run was
indistinguishable from a hang. These tests pin the two halves of the fix: the
summarizer reports every finished call, and the mapping onto a progress bar
only ever moves forward.
"""

from unittest import TestCase

from backend.core.note_progress import note_progress_event, note_progress_fraction


class NoteProgressFractionTests(TestCase):
    def test_progress_only_moves_forward_through_a_chapter_coverage_run(self):
        run = [
            ("evidence", 0, 7), ("evidence", 3, 7), ("evidence", 7, 7),
            ("outline", 0, 1), ("outline", 1, 1),
            ("chapters", 0, 7), ("chapters", 4, 7), ("chapters", 7, 7),
            ("style", 1, 1),
            ("coverage", 1, 1),
            ("revision", 1, 1),
        ]
        seen = [note_progress_fraction(*step) for step in run]
        self.assertEqual(seen, sorted(seen))
        self.assertGreater(seen[0], -0.01)
        self.assertLessEqual(seen[-1], 1.0)

    def test_a_run_that_skips_the_conditional_revision_still_reaches_the_end(self):
        # `revision` only fires when the coverage check rejects the draft. A run
        # without it must not strand the bar short of done — the caller jumps to
        # 100 on the terminal event, so coverage alone has to land near the top.
        self.assertGreaterEqual(note_progress_fraction("coverage", 1, 1), 0.84)

    def test_an_unknown_step_never_drags_the_bar_backwards(self):
        self.assertEqual(note_progress_fraction("mystery", 1, 1), 0.0)

    def test_a_step_with_no_total_reports_its_starting_point(self):
        self.assertEqual(note_progress_fraction("chapters", 0, 0), note_progress_fraction("chapters", 0, 1))


class NoteProgressEventTests(TestCase):
    def test_a_step_event_maps_onto_the_callers_slice_of_the_bar(self):
        event = note_progress_event(
            {"step": "evidence", "completed": 7, "total": 7}, start=0.0, end=95.0
        )
        self.assertEqual(event["stage"], "summary")
        self.assertEqual(event["note_step"], "evidence")
        self.assertEqual(event["note_step_completed"], 7)
        self.assertEqual(event["note_step_total"], 7)
        self.assertAlmostEqual(event["progress"], 38.0, places=1)

    def test_the_event_carries_a_label_in_both_languages(self):
        event = note_progress_event(
            {"step": "chapters", "completed": 1, "total": 7}, start=0.0, end=100.0
        )
        self.assertEqual(event["note_step_label"], "撰写章节")
        self.assertEqual(event["note_step_label_en"], "Writing chapters")

    def test_an_unlabelled_step_falls_back_to_its_own_name(self):
        event = note_progress_event({"step": "mystery"}, start=0.0, end=100.0)
        self.assertEqual(event["note_step_label"], "mystery")
