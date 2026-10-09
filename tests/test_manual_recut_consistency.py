"""A manual re-cut that fails leaves the task describing the cut file it kept.

Requirements, in the user's terms:

- A manual cut that renders replaces the cut file, the cut list and the
  subtitles together: all three describe the same cut.
- When a re-cut's render fails, the previous cut file stays, and so do the cut
  list and the subtitles that match it (on disk and in the task), so the
  subtitles never run against a file they were not made for.
- A manual cut is recorded as made after transcription, even when an earlier
  automatic cut was declined before transcription; that is what lets the user
  cut the task again later.

Only ffmpeg is faked: detection returns a fixed plan, and the render either
writes a file or fails.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.core.debreath_job as dj
from backend.core import job_store, silence_cuts as sc
from backend.core.result_artifacts import artifact_target_path


def _plan(cut_at: float) -> sc.CutPlan:
    return sc.CutPlan(
        source_duration_seconds=10.0,
        cuts=[sc.TimeRange(cut_at, cut_at + 2.0)],
        keeps=[sc.TimeRange(0.0, cut_at), sc.TimeRange(cut_at + 2.0, 10.0)],
        silences_found=1, noise_db=-30.0, min_silence_seconds=0.5, padding_seconds=0.1,
    )


@pytest.fixture()
def task(tmp_path, monkeypatch):
    task_id = f"recut-{uuid.uuid4().hex[:8]}"
    source = tmp_path / "lecture.m4a"
    source.write_bytes(b"recording")
    job_store.upsert_job(task_id=task_id, status="completed", client_id=None, result={
        "task_id": task_id,
        "transcript_media": "source",
        "raw_segments": [
            {"start": 0.5, "end": 1.5, "text": "开头"},
            {"start": 8.0, "end": 9.0, "text": "结尾"},
        ],
        # A declined automatic cut, transcribed from the recording.
        "debreath": {"status": "completed", "ran_before_transcription": True,
                     "used_for_transcription": False, "rendered": False},
    })
    monkeypatch.setattr(dj, "resolve_source", lambda _t: source)
    monkeypatch.setattr(dj, "adapt_threshold", lambda _s, noise_db, runner=None: (noise_db, {}))
    monkeypatch.setattr(dj, "log_event", lambda **_k: None)
    dj.release(task_id)
    yield task_id
    dj.release(task_id)
    job_store.delete_jobs([task_id])


def _renders(monkeypatch, content: bytes):
    def render(_source, _plan, output, runner=None):
        Path(output).write_bytes(content)
        return SimpleNamespace(ok=True, as_dict=lambda: {"ok": True})

    monkeypatch.setattr(sc, "render_cut_plan", render)


def _render_fails(monkeypatch):
    def render(*_a, **_k):
        raise sc.SilenceCutError("concat failed: disk full")

    monkeypatch.setattr(sc, "render_cut_plan", render)


def _on_disk(result: dict, kind: str) -> str:
    return artifact_target_path(result["task_id"], result["artifacts"][kind]["filename"]).read_text("utf-8")


def test_a_failed_re_cut_keeps_the_cut_list_and_subtitles_of_the_kept_file(task, monkeypatch):
    monkeypatch.setattr(sc, "plan_silence_cuts", lambda *_a, **_k: _plan(3.0))
    _renders(monkeypatch, b"first cut")
    dj.run_debreath(task)
    before = job_store.get_job(task)["result"]
    cut_list_before = _on_disk(before, dj.CUT_LIST_KIND)
    subtitles_before = _on_disk(before, dj.TRANSCRIPT_KIND)
    assert json.loads(cut_list_before)["cuts"][0]["start"] == 3.0

    monkeypatch.setattr(sc, "plan_silence_cuts", lambda *_a, **_k: _plan(5.0))
    _render_fails(monkeypatch)
    with pytest.raises(dj.DebreathError):
        dj.run_debreath(task)

    after = job_store.get_job(task)["result"]
    assert after["debreath"]["status"] == "failed"
    assert _on_disk(after, dj.CUT_LIST_KIND) == cut_list_before
    assert _on_disk(after, dj.TRANSCRIPT_KIND) == subtitles_before
    assert after["artifacts"][dj.CUT_LIST_KIND] == before["artifacts"][dj.CUT_LIST_KIND]
    assert after["artifacts"][dj.TRANSCRIPT_KIND] == before["artifacts"][dj.TRANSCRIPT_KIND]
    assert after["debreath"]["plan"] == before["debreath"]["plan"]
    assert after["debreath"]["transcript_timeline"] == before["debreath"]["transcript_timeline"]
    assert _on_disk(after, dj.MEDIA_KIND) == "first cut"


def test_a_successful_re_cut_replaces_all_three_together(task, monkeypatch):
    monkeypatch.setattr(sc, "plan_silence_cuts", lambda *_a, **_k: _plan(3.0))
    _renders(monkeypatch, b"first cut")
    dj.run_debreath(task)

    monkeypatch.setattr(sc, "plan_silence_cuts", lambda *_a, **_k: _plan(5.0))
    _renders(monkeypatch, b"second cut")
    dj.run_debreath(task)

    after = job_store.get_job(task)["result"]
    assert json.loads(_on_disk(after, dj.CUT_LIST_KIND))["cuts"][0]["start"] == 5.0
    assert _on_disk(after, dj.MEDIA_KIND) == "second cut"
    # The closing line at 8s moves 2s earlier on the new cut's clock.
    assert "00:00:06,000" in _on_disk(after, dj.TRANSCRIPT_KIND)


def test_a_manual_cut_is_recorded_as_made_after_transcription(task, monkeypatch):
    monkeypatch.setattr(sc, "plan_silence_cuts", lambda *_a, **_k: _plan(3.0))
    _renders(monkeypatch, b"cut")

    dj.run_debreath(task)

    state = job_store.get_job(task)["result"]["debreath"]
    assert state["used_for_transcription"] is True
    assert state["ran_before_transcription"] is False
