"""The records list read from the part of each result kept for it.

Requirements (2026-11-26, decision 6.96):

- The whole list no longer reads every result (94 MB on 303 tasks; seconds
  on a busy machine): each result's list part is written with it.
- Every row reads exactly as it did from the whole result, field for field,
  whatever shape the result has (an empty cut, a part without the keys a row
  reads, an old result without subtitle mode, booleans, long transcripts).
- A result written without its part (an older FluentFlow, a hand-run UPDATE)
  still reads right: the part is cleared and the row is read from the result.
- A database from before this gets the parts on first open, and keeps every
  task.
"""

from __future__ import annotations

import ast
import inspect
import json
import sqlite3
from pathlib import Path

import pytest

from backend.core import job_store
from backend.core.result_schema import LEGACY_SEGMENT_KEYS

# How the list read a result before its part was kept (the code as it was).
_DROPPED = (
    "raw_segments", "display_segments", "stt_raw_segments", *sorted(LEGACY_SEGMENT_KEYS),
    "raw_transcript_text", "cleaned_transcript_text",
    "visual_note.markdown", "visual_note.frames_sent", "visual_note.frames_opened",
    "visual_note.frames_cited", "visual_note.basis_note", "visual_note.replaced_note",
)
_BEFORE = (
    "task_id, created_at, updated_at, status, client_id, stage, progress, source_type,"
    " source_filename, source_file_size_mb, summary_status, error_reason, metadata_json,"
    " json_remove(result_json, " + ", ".join(f"'$.{key}'" for key in _DROPPED) + ") AS result_json,"
    " instr(result_json, '\"text_zh\"') > 0 AS has_text_zh"
)
SEGMENTS = [{"start": float(i), "end": float(i + 1), "text": f"line {i}", "text_zh": f"第 {i} 行"} for i in range(30)]
PLAIN = [{"start": float(i), "end": float(i + 1), "text": f"line {i}"} for i in range(30)]
REPORT = {"sections": [{"judge": True, "items": [{"key": "a"}, {"key": "b"}]}, {"items": [{"key": "x"}]}]}

RESULTS = {
    "full": {
        "display_title": "第四课", "summary_markdown": "# 笔记\n" + "要点。" * 200, "transcript_text": "大家好" * 300,
        "raw_transcript_text": "x" * 500, "display_segments": SEGMENTS, "raw_segments": PLAIN, "stt_raw_segments": PLAIN,
        "source_stt_raw_segments": PLAIN, "source_display_segments": SEGMENTS, "summary_written_from": "debreath_media_note",
        "visual_note": {"status": "running", "markdown": "n" * 300, "media_source": {"kind": "source_no_cuts"}, "frames_sent": [1, 2]},
        "debreath": {"status": "completed", "used_for_transcription": True, "ran_before_transcription": True,
                     "already_cut": False, "render_verified": True, "delivered": False, "delivery_error": "磁盘满",
                     "plan": {"cut_count": 12, "removed_seconds": 30.5, "ranges": [[1, 2]] * 50},
                     "survey": {"report": REPORT, "verdicts": {"a": "agree"}, "judged_by": "claude", "spoken": PLAIN, "rerendering": True}},
        "agent_cut": {"status": "waiting", "since": "2026-11-26T00:00:00+00:00", "plan_cuts": PLAIN},
        "output_folder": {"path": "/x", "cut_path": "/x/cut.mp4", "md_path": "/x/a.md"},
        "lark_response": {"url": "https://example.invalid/doc", "sync": {"doc_id": "d"}},
        "artifacts": {"debreath_media": {"filename": "a.mp4", "size": None}, "playback_audio": {"filename": "a.mp3"}},
        "playback_audio_available": True, "source_file_available": False, "download_only": False,
        "note_mode_plan_warnings": ["w"], "stt_realtime_factor": 0.12345678901234567,
    },
    "bilingual_old": {"summary_markdown": "x", "bilingual_segments": SEGMENTS},
    "plain_old": {"summary_markdown": "x", "segments": PLAIN},
    "empty_parts": {"debreath": {}, "agent_cut": {}, "visual_note": {}, "output_folder": {}, "lark_response": {}},
    "parts_without_read_keys": {"debreath": {"ranges": [1]}, "visual_note": {"frames_sent": [1]},
                                "agent_cut": {"plan_cuts": []}, "output_folder": {"md_path": "/x"}},
    "parts_not_objects": {"debreath": [1], "agent_cut": "x", "visual_note": None, "artifacts": [], "transcript_text": 5},
    "survey_shapes": {"debreath": {"status": "completed", "plan": None, "survey": {"report": {}, "verdicts": None}}},
    "nothing": {},
}


@pytest.fixture
def db(tmp_path) -> Path:
    path = tmp_path / "jobs.sqlite"
    job_store.reset_job_db_cache()
    for name, result in RESULTS.items():
        job_store.upsert_job(task_id=name, status="completed", client_id="u", stage="done", result=result,
                             metadata={"display_title": name}, db_path=path)
    return path


def _before(path: Path) -> list[dict]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(f"SELECT {_BEFORE} FROM jobs ORDER BY updated_at DESC, task_id").fetchall()
    return sorted((job_store._row_to_summary_dict(row) for row in rows), key=lambda job: job["task_id"])


def _now(path: Path) -> list[dict]:
    return sorted(job_store.list_job_summaries(limit=None, db_path=path), key=lambda job: job["task_id"])


def _parts(path: Path) -> dict[str, str | None]:
    with sqlite3.connect(path) as conn:
        return dict(conn.execute("SELECT task_id, list_result_json FROM jobs").fetchall())


def test_every_row_reads_as_it_did_from_the_whole_result(db):
    assert _now(db) == _before(db)


def test_the_part_is_written_with_the_result_and_leaves_the_bulk_out(db):
    parts = _parts(db)
    assert all(parts[name] for name in RESULTS)
    full = json.loads(parts["full"])
    assert not [key for key in full if key.endswith("segments")]
    assert "spoken" not in full["debreath"]["survey"] and "ranges" not in full["debreath"]["plan"]
    assert len(full["transcript_text"]) == 240
    assert len(parts["full"]) < 6000


def test_each_kind_of_write_keeps_the_part_in_step(db):
    full = job_store.get_job("full", db_path=db)["result"]
    job_store.update_job_result("full", {**full, "display_title": "改过"}, db_path=db)
    plain = job_store.get_job("plain_old", db_path=db)["result"]
    job_store.save_result_changes("plain_old", plain, {**plain, "display_title": "也改过"}, db_path=db)
    job_store.append_job_result_list_item("nothing", "glossary_corrections", {"from": "a", "to": "b"}, db_path=db)
    job_store.finalize_note_if_unchanged("bilingual_old", {}, {"summary_markdown": "新笔记"}, status="completed",
                                         stage="done", progress=1.0, summary_status="completed", db_path=db)
    # A progress write without a result keeps the part it had.
    job_store.upsert_job(task_id="nothing", status="processing", client_id="u", stage="stt", db_path=db)
    assert _now(db) == _before(db)
    assert all(_parts(db).values())


def test_a_result_written_without_its_part_is_read_from_the_result(db):
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE jobs SET result_json = json_set(result_json, '$.display_title', '手改') WHERE task_id = 'full'")
    assert _parts(db)["full"] is None
    rows = {job["task_id"]: job for job in _now(db)}
    assert rows["full"]["result"]["display_title"] == "手改"
    assert _now(db) == _before(db)
    # Opened again later, the part is made.
    job_store.reset_job_db_cache()
    job_store.ensure_job_db(db)
    assert _parts(db)["full"] and _now(db) == _before(db)


def test_a_database_from_before_gets_the_parts_on_first_open(tmp_path, db):
    old = tmp_path / "old.sqlite"
    with sqlite3.connect(db) as source, sqlite3.connect(old) as target:
        columns = [row[1] for row in source.execute("PRAGMA table_info(jobs)") if not row[1].startswith("list_result")]
        target.execute(f"CREATE TABLE jobs ({', '.join(columns)}, PRIMARY KEY (task_id))")
        target.executemany(f"INSERT INTO jobs VALUES ({', '.join('?' for _ in columns)})",
                           source.execute(f"SELECT {', '.join(columns)} FROM jobs").fetchall())
    expected = _before(old)
    job_store.reset_job_db_cache()
    job_store.ensure_job_db(old)
    assert all(_parts(old).values())
    assert _now(old) == expected and len(expected) == len(RESULTS)


def test_every_key_a_row_reads_is_kept():
    """A key read by the summary but missing from the part would read as
    absent, on every task, with nothing failing."""
    read: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(job_store._result_summary))):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "get" \
                and getattr(node.func.value, "id", "") == "result" and isinstance(node.args[0], ast.Constant):
            read.add(node.args[0].value)
        if isinstance(node, ast.Subscript) and getattr(node.value, "id", "") == "result" and isinstance(node.slice, ast.Constant):
            read.add(node.slice.value)
    # note_from_frames is worked out on every read (normalize_result_for_read).
    kept = (set(job_store._LIST_RESULT_KEYS) | {key for key in job_store._LIST_RESULT_PARTS if "." not in key}
            | {"transcript_text", "note_from_frames"})
    assert read and read <= kept, read - kept
