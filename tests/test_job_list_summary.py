"""What the records list shows for a finished task, read the cheap way.

Requirements (2026-10-09, stability run):

1. The list carries everything a card shows: title, note and transcript
   previews, the note's real length, subtitle mode, which file the note was
   written from, and the cut in counts.
2. It does not carry segments, full transcripts or the visual note's copy of
   the note; those made the list take seconds.
3. An old result with no stored subtitle mode still reads as bilingual when its
   segments had Chinese.
"""

from __future__ import annotations

import uuid

from backend.core import job_store

NOTE = "# 线性代数\n\n" + "要点。" * 200
SEGMENTS = [{"start": float(i), "end": float(i + 1), "text": f"line {i}", "text_zh": f"第 {i} 行"} for i in range(50)]


def _finished(result: dict) -> str:
    task_id = "list-" + uuid.uuid4().hex
    job_store.upsert_job(task_id=task_id, status="completed", client_id="list-test", stage="done", result=result)
    return task_id


def _listed(task_id: str) -> dict:
    return next(job for job in job_store.list_job_summaries(limit=None, client_id="list-test") if job["task_id"] == task_id)


def test_list_row_has_what_a_card_shows_and_none_of_the_bulk():
    task_id = _finished({
        "display_title": "线性代数第一讲",
        "summary_markdown": NOTE,
        "transcript_text": "大家好" * 300,
        "raw_transcript_text": "大家好" * 300,
        "display_segments": SEGMENTS,
        "raw_segments": SEGMENTS,
        "stt_raw_segments": SEGMENTS,
        "summary_written_from": "debreath_media_note",
        "visual_note": {"markdown": NOTE, "frames_sent": [{"path": "/x.jpg"}] * 20,
                        "media_source": {"kind": "source_no_cuts"}},
        "debreath": {"status": "completed", "plan": {"cut_count": 12, "removed_seconds": 30.0}},
    })
    try:
        result = _listed(task_id)["result"]
        assert result["display_title"] == "线性代数第一讲"
        assert result["summary_preview"] == NOTE[:240]
        assert result["summary_chars"] == len(NOTE.strip())
        assert result["transcript_text_preview"] == ("大家好" * 300)[:240]
        assert result["subtitle_mode"] == "bilingual_zh"
        # The note read the original recording, so the stamp is corrected.
        assert result["summary_written_from"] == "source_media_note"
        assert result["debreath"]["plan"]["cut_count"] == 12
        for bulky in ("display_segments", "raw_segments", "stt_raw_segments", "raw_transcript_text", "visual_note"):
            assert bulky not in result
    finally:
        job_store.delete_jobs([task_id], client_id="list-test")


def test_old_result_without_subtitle_mode_still_reads_bilingual():
    task_id = _finished({"summary_markdown": NOTE, "bilingual_segments": SEGMENTS})
    plain_id = _finished({"summary_markdown": NOTE, "segments": [{"start": 0.0, "end": 1.0, "text": "hi"}]})
    try:
        import sqlite3
        with sqlite3.connect(job_store.DEFAULT_DB_PATH) as conn:
            for tid in (task_id, plain_id):
                conn.execute(
                    "UPDATE jobs SET result_json = json_remove(result_json, '$.subtitle_mode', '$.result_schema_version') WHERE task_id = ?",
                    (tid,),
                )
        assert _listed(task_id)["result"]["subtitle_mode"] == "bilingual_zh"
        assert _listed(plain_id)["result"]["subtitle_mode"] == "source_only"
    finally:
        job_store.delete_jobs([task_id, plain_id], client_id="list-test")
