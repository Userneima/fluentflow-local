"""The note is written while the cut renders.

Requirements (owner, 2026-10-16, to-do batch three), in the user's terms:

- Once the model has decided what to cut, the note starts; the cut video
  renders beside it. The note does not wait for the render.
- The next recording waits until the render is done and the note's pictures
  are taken (both use this machine), not for the note's wait on Claude.
- The note reads the recording as the cut will be: its subtitles are on the
  cut's clock, its pictures are taken from the original at the moment each
  will have in the cut, and nothing from a stretch the cut removes appears.
- When nothing is to be cut, the note waits for the cut step and reads what it
  left, as before. Without Claude (the text note stands in) it also waits.
- When the cut then does not come out, the task stays on the original, and so
  does the note: its pictures' times and the times it cites in the
  transcript's ``[mm:ss]`` form go back onto the original's clock; a clock time
  such as 10:30 is left alone.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.core.visual_note_job as vn
from backend.core import cut_timeline
from backend.routers import local_processing as lp
from backend.core import task_flow as tf, task_queue as tq, task_submission as ts  # noqa: F401
from test_visual_note_job import TASK, job_store, no_ambient_key, vn_result  # noqa: F401

# Kept: 0-10 s and 20-100 s of the original; 10-20 s is cut.
RANGES = cut_timeline.kept_ranges({"keeps": [{"start": 0, "end": 10}, {"start": 20, "end": 100}]})


# ── the flow ───────────────────────────────────────────────────────────────

class Flow:
    def __init__(self, *, plan=True):
        self.log: list[str] = []
        self.plan = plan
        self.render_gate = threading.Event()
        self.remote_gate = threading.Event()
        self.planned = None

    async def pipeline(self, ctx):
        self.log.append("pipeline")

    def survey_and_cut(self, task_id, client_id, on_plan=None):
        self.log.append("judged")
        if self.plan and on_plan is not None:
            on_plan(RANGES)
        self.render_gate.wait(5)
        self.log.append("cut-done")

    def write_note(self, task_id, client_id, *, on_local_work_done=None, planned_ranges=None):
        self.planned = planned_ranges
        self.log.append("note-start")
        self.log.append("pictures-taken")
        on_local_work_done()
        self.remote_gate.wait(5)
        self.log.append("note-done")


def _wire(monkeypatch, flow, *, claude=True):
    monkeypatch.setattr(tf, "_run_pipeline", flow.pipeline)
    monkeypatch.setattr(tf.local_intake_flow, "survey_first_enabled", lambda: True)
    monkeypatch.setattr(tf.local_intake_flow, "note_is_wanted", lambda *_a: True)
    monkeypatch.setattr(tf.local_intake_flow, "mark_note_running", lambda *_a: None)
    monkeypatch.setattr(tf.local_intake_flow, "write_note", flow.write_note)
    monkeypatch.setattr(tf.survey_cut, "survey_and_cut", flow.survey_and_cut)
    monkeypatch.setattr(
        tf.visual_note_channel, "resolve_channel", lambda *_a, **_k: SimpleNamespace(available=claude),
    )
    monkeypatch.setattr(tf.visual_note_job, "put_back_onto_original",
                        lambda *_a, **_k: flow.log.append("put-back-checked"))
    monkeypatch.setattr(tf, "_export_note_written_after_pipeline", _nothing)
    monkeypatch.setattr(tf, "_deliver_to_output_folder", _nothing)


async def _nothing(_ctx):
    return None


async def _until(predicate, tries=150):
    for _ in range(tries):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


def _ctx():
    return SimpleNamespace(task_id_value=TASK, client_id=None)


def test_the_note_starts_once_the_cut_is_decided_and_does_not_wait_for_the_render(monkeypatch):
    flow = Flow()
    _wire(monkeypatch, flow)

    async def scenario():
        done = asyncio.Event()
        runner = asyncio.create_task(tf.run_pipeline_then_note(done, _ctx()))
        assert await _until(lambda: "note-start" in flow.log), "the note waited for the render"
        assert "cut-done" not in flow.log
        await asyncio.sleep(0.1)
        assert not done.is_set(), "the queue moved on while the cut was still rendering"
        flow.render_gate.set()
        assert await _until(done.is_set), "the queue waited for the note's wait on Claude"
        assert "note-done" not in flow.log
        flow.remote_gate.set()
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert flow.planned == RANGES
    assert flow.log.index("note-start") < flow.log.index("cut-done")
    assert flow.log[-1] == "put-back-checked"


def test_with_nothing_to_cut_the_note_waits_for_the_cut_step(monkeypatch):
    flow = Flow(plan=False)
    _wire(monkeypatch, flow)
    flow.render_gate.set()
    flow.remote_gate.set()

    asyncio.run(asyncio.wait_for(tf.run_pipeline_then_note(asyncio.Event(), _ctx()), 5))
    assert flow.log.index("cut-done") < flow.log.index("note-start")
    assert flow.planned is None


def test_without_claude_the_note_waits_for_the_cut(monkeypatch):
    flow = Flow()
    _wire(monkeypatch, flow, claude=False)
    flow.render_gate.set()
    flow.remote_gate.set()

    asyncio.run(asyncio.wait_for(tf.run_pipeline_then_note(asyncio.Event(), _ctx()), 5))
    assert flow.log.index("cut-done") < flow.log.index("note-start")
    assert flow.planned is None


# ── what the note reads ───────────────────────────────────────────────────

def test_pictures_come_from_the_original_on_the_cuts_clock_and_none_from_a_cut_stretch(tmp_path):
    seen: dict = {}

    def extract(video_path, output_dir, segments=None, **kwargs):
        seen["video"] = str(video_path)
        seen["anchors"] = kwargs.get("anchor_seconds")
        frames = []
        for index, at in enumerate((5.0, 15.0, 30.0)):  # 15 s is in the cut stretch
            path = Path(output_dir) / f"scene_{index:04d}.jpg"
            path.write_bytes(b"\xff\xd8\xff" + bytes([index]) * 32)
            frames.append({"path": str(path), "timestamp_seconds": at, "source": "scene"})
        return vn_result(frames)

    original = tmp_path / "lecture.mp4"
    cues = [{"start": 2.0, "end": 4.0, "text": "开场"}, {"start": 12.0, "end": 14.0, "text": "第二句"}]
    frames = vn.collect_frames(TASK, original, keyframe_extractor=extract, segments=cues, ranges=RANGES)

    assert seen["video"] == str(original)
    # A line at 12 s of the cut was said at 22 s of the original.
    assert seen["anchors"] == [2.0, 22.0]
    assert [frame.timestamp_seconds for frame in frames] == [5.0, 20.0]


def test_the_note_reads_subtitles_on_the_cuts_clock_without_the_cut_stretch(tmp_path, monkeypatch):
    original = tmp_path / "lecture.mp4"
    original.write_bytes(b"video")
    monkeypatch.setattr(vn, "find_source_file", lambda _t: original)
    result = {
        "display_segments": [
            {"start": 1.0, "end": 3.0, "text": "留下的第一句"},
            {"start": 12.0, "end": 18.0, "text": "被剪掉的一句"},
            {"start": 25.0, "end": 27.0, "text": "留下的第二句"},
        ],
    }
    media = vn.planned_cut_media(TASK, RANGES)
    transcript, _timeline, segments = vn._transcript_for_media(TASK, result, media)

    assert media.path == original and media.kind == vn.MEDIA_CUT
    assert "被剪掉" not in transcript
    assert [round(seg["start"], 1) for seg in segments] == [1.0, 15.0]


# ── the cut did not come out ──────────────────────────────────────────────

def test_a_note_for_a_cut_that_never_rendered_goes_back_onto_the_original(job_store):  # noqa: F811
    markdown = "[00:20] 讲到对比表。会议 10:30 开始。\n\n![表](note_0001.jpg)"
    job_store["result"] = {
        "task_id": TASK,
        "transcript_media": "source_media",
        "summary_markdown": markdown,
        "visual_note": {
            "status": vn.STATUS_COMPLETED, "promoted": True, "markdown": markdown,
            "media_source": {"kind": vn.MEDIA_CUT, "pictures_from_original": True},
            "frames_sent": [{"filename": "note_0001.jpg", "timestamp_seconds": 20.0}],
            "frames_cited": [{"filename": "note_0001.jpg", "timestamp_seconds": 20.0}],
        },
    }

    assert vn.put_back_onto_original(TASK, RANGES) is True

    result = job_store["result"]
    note = vn.visual_note_state(result)
    assert result["summary_markdown"].startswith("[00:30] 讲到对比表。会议 10:30 开始。")
    assert note["markdown"].startswith("[00:30]")
    assert note["frames_sent"][0]["timestamp_seconds"] == 30.0
    assert note["frames_cited"][0]["timestamp_seconds"] == 30.0
    assert note["media_source"]["kind"] == vn.MEDIA_SOURCE_UNCHANGED
    assert result["summary_written_from"] == "source_media_note"
    # The version this replaces records why (owner, 2026-10-22).
    from backend.core import note_versions

    assert note_versions.changed_by(result) == note_versions.ADJUST


def test_a_note_whose_cut_did_come_out_is_left_alone(job_store):  # noqa: F811
    job_store["result"] = {
        "task_id": TASK,
        "transcript_media": "debreath_media",
        "summary_markdown": "[00:20] 内容",
        "visual_note": {"status": vn.STATUS_COMPLETED, "promoted": True, "markdown": "[00:20] 内容",
                        "media_source": {"kind": vn.MEDIA_CUT, "pictures_from_original": True}},
    }

    assert vn.put_back_onto_original(TASK, RANGES) is False
    assert job_store["result"]["summary_markdown"] == "[00:20] 内容"


@pytest.mark.parametrize("text,expected", [
    ("[00:05]", "[00:05]"),
    ("[00:50]", "[01:00]"),
    ("[0:00:50]", "[0:01:00]"),
    ("没有时间", "没有时间"),
])
def test_cited_times_move_by_the_cut_list(text, expected):
    assert vn._times_back_onto_original(text, RANGES) == expected
