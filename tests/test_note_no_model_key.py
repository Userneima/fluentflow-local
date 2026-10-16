"""A task with no model at all ends with a transcript, and every account of it
says so: the page, the agent's task package and the decision log.

Found on Windows, 2026-10-16: with no key the note was recorded as failed and
told the user to retry or shorten the material; after it became a skip, the
agent package still called it "transcript-only mode", which the user had not
chosen.
"""

from __future__ import annotations

from backend.core import decision_log_core, note_diagnosis

NO_KEY = {
    "transcript_text": "大家好",
    "summary_status": "skipped",
    "summary_skipped": True,
    "summary_skipped_reason": "no_model_key",
}


def test_the_agent_package_says_to_add_a_key():
    diagnosis = note_diagnosis.build_note_generation_diagnosis(
        {"status": "completed"}, NO_KEY, diagnose_error=lambda _e: {},
    )
    assert diagnosis["status"] == "skipped" and diagnosis["code"] == "no_model_key"
    assert "Key" in diagnosis["title"] and "仅转录" not in diagnosis["title"]


def test_transcript_only_mode_is_still_called_that():
    chosen = {**NO_KEY, "summary_skipped_reason": None}
    diagnosis = note_diagnosis.build_note_generation_diagnosis(
        {"status": "completed"}, chosen, diagnose_error=lambda _e: {},
    )
    assert diagnosis["code"] == "transcript_only_mode"


def test_the_decision_log_gives_the_same_reason():
    status, _decision, reason, _impact = decision_log_core._note_status(NO_KEY, {"status": "completed"})
    assert status == "skipped" and "Key" in reason and "仅转录" not in reason
