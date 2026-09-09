"""The local edition's automatic flow: cut first, transcribe that, note from that.

Choosing a video to make a note from is one action. So the ordering here is not a
convenience — it is what makes every surface agree: the transcript is produced
*from* the shortened audio, so its timestamps are that file's from the start, and
the page, the note and the subtitles need no conversion between them.

What is guarded:

- **Cutting first must never cost somebody their upload or their words.** Whatever
  the cut removes, the transcript will never contain. So the spot check can veto
  the cut file, an unsupported container or a broken render falls back to the
  recording, and every fallback says why in the result instead of failing the job.
- **The transcript's file is recorded, not inferred.** Two orders exist now (cut
  before transcription, and cut afterwards on a finished task) and they need
  opposite treatment. A transcript made from the cut file must never be remapped
  through the cut list — that would shift every line by an interval already taken
  out once, and nothing would look wrong.
- **The old note step is gone from this path**, and a failed automatic note leaves
  a task that explains itself rather than one that says the note was skipped.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import backend.core.debreath_job as dj
import backend.core.local_intake_flow as flow
import backend.core.visual_note_job as vn
from backend.core import cut_timeline, local_folder_intake
from backend.core.job_store import get_job, upsert_job
from backend.core.result_artifacts import (
    TRANSCRIPT_MEDIA_CUT,
    TRANSCRIPT_MEDIA_SOURCE,
    artifact_target_path,
)
from backend.core.silence_cuts import CutPlan, RenderReport, SilenceCutError, TimeRange

TASK = "task-local-intake"


@pytest.fixture(autouse=True)
def switches_on(monkeypatch):
    monkeypatch.delenv(flow.CUT_FIRST_ENV, raising=False)
    monkeypatch.delenv(flow.AUTO_NOTE_ENV, raising=False)
    vn.release(TASK)
    yield
    vn.release(TASK)


def _plan(*cuts: tuple[float, float], separated: bool = True, duration: float = 100.0) -> CutPlan:
    ranges = [TimeRange(start, end) for start, end in cuts]
    plan = CutPlan(
        source_duration_seconds=duration,
        cuts=ranges,
        keeps=[TimeRange(0.0, 10.0), TimeRange(20.0, 100.0)],
        silences_found=len(ranges),
        noise_db=-30.0,
        min_silence_seconds=0.25,
        padding_seconds=0.1,
    )
    if ranges:
        plan.level_separation = {
            "measured": True,
            "separated": separated,
            "sampled_cuts": 10,
            "narrow_cut_count": 0 if separated else 4,
            "narrowest_db": 15.0 if separated else 2.1,
        }
    return plan


def _report(path: Path, *, ok: bool = True) -> RenderReport:
    return RenderReport(
        output_path=path,
        expected_seconds=90.0,
        actual_seconds=90.0 if ok else 40.0,
        video_seconds=90.0,
        audio_seconds=90.0,
        frame_count=2700,
        batches=1,
        checks={"duration_matches_plan": ok},
        source_skew_seconds=0.0,
    )


@pytest.fixture()
def engine(monkeypatch):
    calls: dict[str, object] = {"rendered": 0}

    def fake_plan(media, **kwargs):
        return calls.get("plan") or _plan((10.0, 20.0))

    def fake_render(media, plan, output, **kwargs):
        calls["rendered"] = int(calls["rendered"]) + 1
        Path(output).write_bytes(b"rendered cut media")
        return _report(Path(output), ok=bool(calls.get("render_ok", True)))

    monkeypatch.setattr(dj.silence_cuts, "plan_silence_cuts", fake_plan)
    monkeypatch.setattr(dj.silence_cuts, "render_cut_plan", fake_render)
    return calls


@pytest.fixture()
def source(tmp_path):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"pretend recording")
    return media


# ── cutting first, and what it hands the pipeline ──────────────────────────

def test_the_pipeline_is_handed_the_cut_file(source, engine):
    prepared = flow.preprocess_media(TASK, source)

    assert prepared.used is True
    assert prepared.path != source
    assert prepared.path.is_file() and prepared.path.read_bytes() == b"rendered cut media"
    assert prepared.state["used_for_transcription"] is True
    assert prepared.state["ran_before_transcription"] is True
    assert dj.MEDIA_KIND in prepared.artifacts and dj.CUT_LIST_KIND in prepared.artifacts
    assert source.read_bytes() == b"pretend recording", "the recording is not rewritten"


def test_switched_off_it_leaves_the_pipeline_exactly_as_it_was(source, engine, monkeypatch):
    monkeypatch.setenv(flow.CUT_FIRST_ENV, "0")

    assert flow.preprocess_media(TASK, source) is None
    assert engine["rendered"] == 0


def test_cuts_that_sit_close_to_the_speech_veto_the_cut_file(source, engine):
    """The one case where cutting first would destroy something.

    Words the cut removes are words the transcript will never contain, and the
    engine's spot check is the only thing that catches a faintly recorded room —
    every count looks reasonable there. So the recording is transcribed, and the
    cut list is still kept because re-rendering it at a wider threshold is cheap.
    """
    engine["plan"] = _plan((10.0, 20.0), separated=False)

    prepared = flow.preprocess_media(TASK, source)

    assert prepared.used is False
    assert prepared.path == source
    assert engine["rendered"] == 0, "not even rendered: the file would not be used"
    assert "录得很轻" in prepared.state["not_used_reason"]
    assert dj.CUT_LIST_KIND in prepared.artifacts, "the judgement is still the deliverable"


def test_a_render_that_fails_its_own_checks_is_kept_but_not_transcribed(source, engine):
    engine["render_ok"] = False

    prepared = flow.preprocess_media(TASK, source)

    assert prepared.used is False and prepared.path == source
    assert prepared.state["rendered"] is True, "the file is kept, as everywhere else here"
    assert prepared.state["render_verified"] is False
    assert dj.MEDIA_KIND in prepared.artifacts


def test_nothing_to_cut_is_a_normal_outcome_not_a_fallback(source, engine):
    engine["plan"] = _plan()

    prepared = flow.preprocess_media(TASK, source)

    assert prepared.used is False and prepared.path == source
    assert prepared.state["plan"]["cut_count"] == 0
    assert prepared.state.get("not_used_reason") is None, "there is nothing to explain"
    assert engine["rendered"] == 0


def test_an_unsupported_container_is_named_and_the_upload_still_runs(tmp_path, engine):
    odd = tmp_path / "source.pdf"
    odd.write_bytes(b"%PDF")

    prepared = flow.preprocess_media(TASK, odd)

    assert prepared.used is False and prepared.path == odd
    assert ".pdf" in prepared.state["not_used_reason"]


def test_ffmpeg_falling_over_costs_the_cut_and_nothing_else(source, engine, monkeypatch):
    """A de-breath failure must not take somebody's transcription with it."""
    def explode(*_a, **_k):
        raise SilenceCutError("ffmpeg not found")

    monkeypatch.setattr(dj.silence_cuts, "plan_silence_cuts", explode)

    prepared = flow.preprocess_media(TASK, source)

    assert prepared.used is False and prepared.path == source
    assert prepared.state["status"] == dj.STATUS_FAILED
    assert "ffmpeg not found" in prepared.state["error"]


# ── the transcript's own clock ─────────────────────────────────────────────

def test_a_transcript_made_from_the_cut_file_is_never_remapped_again(tmp_path):
    """The failure this prevents is silent and cumulative.

    A transcript produced from the shortened audio already carries that file's
    timestamps. Sending it through the cut list would move every line by an
    interval that was already removed once, and nothing on screen would look
    wrong — the note would simply quote the wrong moments.
    """
    media = artifact_target_path(TASK, "debreath/source_debreath.mp4")
    media.write_bytes(b"cut media")
    artifact_target_path(TASK, "debreath/source_cut_list.json").write_text(
        json.dumps({"cuts": [{"start": 10.0, "end": 20.0}],
                    "keeps": [{"start": 0.0, "end": 10.0}, {"start": 20.0, "end": 100.0}]}),
        encoding="utf-8",
    )
    result = {
        "task_id": TASK,
        "transcript_media": TRANSCRIPT_MEDIA_CUT,
        "display_segments": [{"start": 30.0, "end": 35.0, "text": "剪后第 30 秒说的话"}],
        "debreath": {
            "status": "completed", "rendered": True, "used_for_transcription": True,
            "media_filename": "debreath/source_debreath.mp4",
            "plan": {"cut_count": 1},
        },
        "artifacts": {
            dj.MEDIA_KIND: {"kind": dj.MEDIA_KIND, "filename": "debreath/source_debreath.mp4"},
            dj.CUT_LIST_KIND: {"kind": dj.CUT_LIST_KIND, "filename": "debreath/source_cut_list.json"},
        },
    }

    media_record = vn.cut_media(TASK, result)
    transcript, timeline, _cues = vn._transcript_for_media(TASK, result, media_record)

    assert timeline["source"] == cut_timeline.TIMELINE_SOURCE_NATIVE
    assert "[00:30] 剪后第 30 秒说的话" in transcript, "the timestamp it already had"
    assert timeline["dropped_segments"] == 0


def test_a_transcript_from_the_recording_is_still_remapped(tmp_path):
    """The other order, unchanged: cut on a task that was already transcribed."""
    artifact_target_path(TASK, "debreath/source_debreath.mp4").write_bytes(b"cut media")
    artifact_target_path(TASK, "debreath/source_cut_list.json").write_text(
        json.dumps({"cuts": [{"start": 10.0, "end": 20.0}],
                    "keeps": [{"start": 0.0, "end": 10.0}, {"start": 20.0, "end": 100.0}]}),
        encoding="utf-8",
    )
    result = {
        "task_id": TASK,
        "transcript_media": TRANSCRIPT_MEDIA_SOURCE,
        "display_segments": [{"start": 30.0, "end": 35.0, "text": "原片第 30 秒说的话"}],
        "debreath": {
            "status": "completed", "rendered": True, "media_filename": "debreath/source_debreath.mp4",
            "plan": {"cut_count": 1},
        },
        "artifacts": {
            dj.MEDIA_KIND: {"kind": dj.MEDIA_KIND, "filename": "debreath/source_debreath.mp4"},
            dj.CUT_LIST_KIND: {"kind": dj.CUT_LIST_KIND, "filename": "debreath/source_cut_list.json"},
        },
    }

    transcript, timeline, _cues = vn._transcript_for_media(TASK, result, vn.cut_media(TASK, result))

    assert timeline["source"] == cut_timeline.TIMELINE_SOURCE_CUT_LIST
    assert "[00:20]" in transcript, "30s in the recording, 20s after a 10s cut"


def test_re_cutting_a_natively_cut_task_does_not_write_wrong_subtitles(monkeypatch, source, engine):
    """Its transcript is on neither the recording's clock nor this new cut's."""
    state = {
        "task_id": TASK,
        "status": "completed",
        "result": {
            "task_id": TASK,
            "transcript_media": TRANSCRIPT_MEDIA_CUT,
            "display_segments": [{"start": 5.0, "end": 8.0, "text": "已经是剪后的时间点"}],
        },
    }
    monkeypatch.setattr(dj, "get_job", lambda task_id, **_: dict(state) if task_id == TASK else None)
    monkeypatch.setattr(
        dj, "update_job_result",
        lambda task_id, result, **_: state.__setitem__("result", result) or dict(state),
    )
    monkeypatch.setattr(dj, "find_source_file", lambda _t: source)

    updated = dj.run_debreath(TASK, render=False)

    assert dj.TRANSCRIPT_KIND not in (updated["result"].get("artifacts") or {})
    timeline = dj.debreath_state(updated["result"])["transcript_timeline"]
    assert timeline["source"] == cut_timeline.TIMELINE_SOURCE_NATIVE
    assert "不需要再换算" in timeline["skipped_reason"]


# ── the note, written without being asked ──────────────────────────────────

def _finished_task(**result_extra) -> None:
    upsert_job(
        task_id=TASK,
        status="completed",
        client_id="anonymous",
        result={
            "task_id": TASK,
            "filename": "lecture.mp4",
            "transcript_text": "一段转录",
            "raw_segments": [{"start": 0.0, "end": 3.0, "text": "一段转录"}],
            "summary_skipped": True,
            **result_extra,
        },
    )


def test_a_finished_task_with_no_note_wants_one(tmp_path):
    _finished_task()

    assert flow.note_is_wanted(TASK, "anonymous") is True


def test_a_task_that_already_has_a_note_is_left_alone(tmp_path):
    _finished_task(summary_markdown="已经有笔记了")

    assert flow.note_is_wanted(TASK, "anonymous") is False


def test_switched_off_no_note_is_written_and_the_entry_still_exists(tmp_path, monkeypatch):
    monkeypatch.setenv(flow.AUTO_NOTE_ENV, "0")
    _finished_task()

    assert flow.note_is_wanted(TASK, "anonymous") is False


def test_while_the_note_is_being_written_the_page_does_not_say_it_was_skipped(tmp_path):
    """For about a minute the task is finished and its note is not there yet.

    Leaving the pipeline's skipped-note state in place would tell the user the note
    was skipped — a plain lie that sends them looking for a button.
    """
    _finished_task()

    flow.mark_note_running(TASK, "anonymous")

    result = get_job(TASK)["result"]
    assert result["summary_skipped"] is False
    assert result["summary_status"] == "pending"


def test_a_note_that_cannot_be_written_says_so_where_the_note_belongs(tmp_path, monkeypatch):
    _finished_task()

    def refuse(*_a, **_k):
        raise vn.VisualNoteError("这台机器上的 Claude 还没有登录，先在终端运行一次 `claude`。")

    monkeypatch.setattr(vn, "run_visual_note", refuse)

    flow.write_note(TASK, "anonymous")

    result = get_job(TASK)["result"]
    assert result["summary_status"] == "failed"
    assert "还没有登录" in result["summary_error"]
    assert TASK not in vn._running, "and the slot is released"


def test_an_unexpected_failure_still_leaves_a_readable_task(tmp_path, monkeypatch):
    _finished_task()
    monkeypatch.setattr(vn, "run_visual_note", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("boom")))

    flow.write_note(TASK, "anonymous")

    result = get_job(TASK)["result"]
    assert result["summary_status"] == "failed" and "boom" in result["summary_error"]
    assert result["transcript_text"] == "一段转录", "the transcript is still theirs"


# ── a declined cut must not cost the user their note ──────────────────────

def test_a_declined_cut_still_gets_a_note_from_the_recording(monkeypatch, tmp_path):
    """Reported on a 50-minute meeting recording.

    The spot check found the cuts sitting too close to the speech, so the recording
    was transcribed — correctly. But the note step only knew how to read a cut file,
    refused, and left the task with a transcript and an error. The note reads whatever
    the transcript describes; that is the rule, and a decline is one of its cases.
    """
    source = tmp_path / "meeting.m4a"
    source.write_bytes(b"a faint recording")
    monkeypatch.setattr(vn, "find_source_file", lambda _t: source)
    result = {
        "task_id": TASK,
        "transcript_media": TRANSCRIPT_MEDIA_SOURCE,
        "display_segments": [{"start": 0.0, "end": 4.0, "text": "会议开始"}],
        "debreath": {
            "status": "completed",
            "ran_before_transcription": True,
            "used_for_transcription": False,
            "not_used_reason": "抽查发现剪掉的部分只比紧邻的说话声低一点点，这份材料录得很轻。",
            "rendered": False,
            "plan": {"cut_count": 760},
        },
    }

    media = vn.cut_media(TASK, result)

    assert media is not None, "a declined cut is not a reason to refuse the note"
    assert media.path == source and media.unchanged is True
    assert media.has_video is False, "an .m4a has no picture and must not claim one"

    transcript, timeline, _cues = vn._transcript_for_media(TASK, result, media)
    assert "[00:00] 会议开始" in transcript
    assert timeline["source"] == cut_timeline.TIMELINE_SOURCE_ORIGINAL, "no remap: it is the original's clock"


def test_a_cut_file_that_failed_its_check_is_not_what_the_note_reads(monkeypatch, tmp_path):
    """The file exists but the transcript came from the recording, so the note must
    follow the transcript rather than the newer file."""
    source = tmp_path / "lecture.mp4"
    source.write_bytes(b"the recording")
    monkeypatch.setattr(vn, "find_source_file", lambda _t: source)
    artifact_target_path(TASK, "debreath/lecture_debreath.mp4").write_bytes(b"a suspect cut")
    result = {
        "task_id": TASK,
        "transcript_media": TRANSCRIPT_MEDIA_SOURCE,
        "display_segments": [{"start": 0.0, "end": 3.0, "text": "开场"}],
        "debreath": {
            "status": "completed",
            "rendered": True,
            "render_verified": False,
            "used_for_transcription": False,
            "not_used_reason": "剪出来的文件和剪辑表对不上（没通过自检）。",
            "media_filename": "debreath/lecture_debreath.mp4",
            "plan": {"cut_count": 40},
        },
        "artifacts": {dj.MEDIA_KIND: {"kind": dj.MEDIA_KIND, "filename": "debreath/lecture_debreath.mp4"}},
    }

    media = vn.cut_media(TASK, result)

    assert media.path == source, "the transcript is the recording's, so the note is too"
    assert media.unchanged is True


def test_the_preview_says_which_file_and_why_when_the_cut_declined(monkeypatch, tmp_path):
    source = tmp_path / "meeting.m4a"
    source.write_bytes(b"a faint recording")
    monkeypatch.setattr(vn, "find_source_file", lambda _t: source)
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "api_key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    job = {
        "task_id": TASK,
        "status": "completed",
        "result": {
            "task_id": TASK,
            "transcript_media": TRANSCRIPT_MEDIA_SOURCE,
            "display_segments": [{"start": 0.0, "end": 4.0, "text": "会议开始"}],
            "debreath": {
                "status": "completed",
                "used_for_transcription": False,
                "not_used_reason": "抽查发现剪掉的部分只比紧邻的说话声低一点点。",
                "rendered": False,
                "plan": {"cut_count": 760},
            },
        },
    }

    described = vn.describe(TASK, job, api_key="sk-ant-test")

    assert described["eligible"] is True, "it can be written; only the file is different"
    assert described["frame_budget"] == 0, "an .m4a has no frames to promise"
    assert any("按原文件写笔记" in item for item in described["media_warnings"])
    assert any("只比紧邻的说话声低" in item for item in described["media_warnings"])


# ── the threshold is measured, not assumed ────────────────────────────────
#
# Both measurements are stubbed in every case below. The threshold reads two now
# — the file's average and its loudness distribution — and stubbing only the
# first left the second running for real, which needs ffmpeg: these passed on a
# developer machine and raised "ffmpeg not found" on CI, which installs none.
# None for the distribution is the unreadable case, which is the branch these
# were written against; the last one reads nothing at all and falls back.

def test_a_faint_recording_gets_a_threshold_that_suits_it(monkeypatch, tmp_path):
    """Measured on a real 49-minute WeChat meeting recording (mean -31.1dB).

    At the fixed -30dB the engine cut 1552 places and its own spot check found
    three of ten sampled cuts within 7.5dB of the neighbouring speech, so the flow
    declined and the user got no cut at all. At -46dB — the material's own average
    minus 15 — it cut 642 places with 26.8dB of separation and no warnings. The
    threshold was in the wrong place, not the material.
    """
    from backend.core import silence_cuts as sc

    monkeypatch.setattr(sc, "overall_mean_volume_db", lambda *_a, **_k: -31.1)
    monkeypatch.setattr(sc, "loudness_range_db", lambda *_a, **_k: None)

    chosen, why = sc.suggest_noise_db(tmp_path / "meeting.m4a")

    assert chosen == -46.1
    assert why["adapted"] is True
    assert "把说话声当成静音" in why["reason"]


def test_a_normally_recorded_file_keeps_the_validated_default(monkeypatch, tmp_path):
    """A close-mic recording on this machine measures about -20dB. The -30dB default
    was validated on 34.3 hours of material and must not move for it."""
    from backend.core import silence_cuts as sc

    monkeypatch.setattr(sc, "overall_mean_volume_db", lambda *_a, **_k: -20.1)
    monkeypatch.setattr(sc, "loudness_range_db", lambda *_a, **_k: None)

    chosen, why = sc.suggest_noise_db(tmp_path / "lecture.mp4")

    assert chosen == sc.DEFAULT_NOISE_DB
    assert why["adapted"] is False


def test_a_near_silent_file_does_not_push_the_threshold_off_the_scale(monkeypatch, tmp_path):
    from backend.core import silence_cuts as sc

    monkeypatch.setattr(sc, "overall_mean_volume_db", lambda *_a, **_k: -91.0)
    monkeypatch.setattr(sc, "loudness_range_db", lambda *_a, **_k: None)

    chosen, _ = sc.suggest_noise_db(tmp_path / "silent.mp4")

    assert chosen == sc.MIN_ADAPTED_NOISE_DB


def test_an_unmeasurable_file_falls_back_to_the_default(monkeypatch, tmp_path):
    from backend.core import silence_cuts as sc

    monkeypatch.setattr(sc, "overall_mean_volume_db", lambda *_a, **_k: None)
    monkeypatch.setattr(sc, "loudness_range_db", lambda *_a, **_k: None)

    chosen, why = sc.suggest_noise_db(tmp_path / "odd.mp4")

    assert chosen == sc.DEFAULT_NOISE_DB and why["measured"] is False


def test_the_intake_cut_records_which_threshold_it_chose_and_why(source, engine, monkeypatch):
    monkeypatch.setattr(dj.silence_cuts, "overall_mean_volume_db", lambda *_a, **_k: -31.1)
    # Same reason as above: without this the second measurement runs for real,
    # which needs ffmpeg and decides differently.
    monkeypatch.setattr(dj.silence_cuts, "loudness_range_db", lambda *_a, **_k: None)

    prepared = flow.preprocess_media(TASK, source)

    assert prepared.state["settings"]["noise_db"] == -46.1
    choice = prepared.state["threshold_choice"]
    assert choice["adapted"] is True and choice["mean_volume_db"] == -31.1


def test_an_explicit_threshold_is_never_second_guessed(source, engine, monkeypatch):
    """A number the caller chose is their judgement; measuring over it would make
    the setting a lie."""
    monkeypatch.setattr(dj.silence_cuts, "overall_mean_volume_db", lambda *_a, **_k: -31.1)

    prepared = dj.prepare_cut_media(TASK, source, noise_db=-35.0)

    assert prepared.state["settings"]["noise_db"] == -35.0
    assert prepared.state["threshold_choice"] is None


# ── a file that has already been cut ──────────────────────────────────────

def test_our_own_cut_file_is_recognised_by_the_mark_inside_it(tmp_path, monkeypatch, engine):
    """The certain half of the answer. The mark travels with the bytes, so a renamed
    or moved file is still recognised. The point of the skip is cost, not safety: a
    second pass over a real 15 minute cut file was measured at 2 cuts / 0.3 seconds,
    so what it avoids is re-encoding a whole file to gain a third of a second."""
    renamed = tmp_path / "讲座 最终版.mp4"
    renamed.write_bytes(b"already cut, and renamed")
    monkeypatch.setattr(dj.silence_cuts, "carries_cut_mark", lambda *_a, **_k: True)

    prepared = dj.prepare_cut_media(TASK, renamed)

    assert prepared.used is False and prepared.path == renamed
    assert prepared.state["already_cut"] is True
    assert "文件里有标记" in prepared.state["not_used_reason"]
    assert engine["rendered"] == 0, "no second pass, and no wasted encode"


def test_a_name_that_looks_cut_is_taken_as_a_hint(tmp_path, monkeypatch, engine):
    """The uncertain half: all that is available for a file something else re-encoded."""
    looks_cut = tmp_path / "0811-morning_debreath.mp4"
    looks_cut.write_bytes(b"probably cut")
    monkeypatch.setattr(dj.silence_cuts, "carries_cut_mark", lambda *_a, **_k: False)

    prepared = dj.prepare_cut_media(TASK, looks_cut)

    assert prepared.state["already_cut"] is True
    assert "文件名看起来" in prepared.state["not_used_reason"]


def test_an_ordinary_recording_is_still_cut(source, engine, monkeypatch):
    monkeypatch.setattr(dj.silence_cuts, "carries_cut_mark", lambda *_a, **_k: False)

    prepared = flow.preprocess_media(TASK, source)

    assert prepared.used is True
    assert prepared.state.get("already_cut") is None


def test_a_saving_of_a_fraction_of_a_second_does_not_re_encode_the_file(tmp_path, monkeypatch, engine):
    """The general form of the already-cut check, and it catches what a mark cannot.

    Measured on a real 15 minute cut file: a second pass finds 2 cuts and 0.3
    seconds. Rendering that re-encodes the whole file to produce a near-duplicate.
    """
    source = tmp_path / "already-short.mp4"
    source.write_bytes(b"nothing much left")
    monkeypatch.setattr(dj.silence_cuts, "carries_cut_mark", lambda *_a, **_k: False)
    engine["plan"] = _plan((10.0, 10.2), (400.0, 400.1), duration=900.0)

    prepared = dj.prepare_cut_media(TASK, source)

    assert prepared.used is False
    assert prepared.state["not_worth_rendering"] is True
    assert "不值得" in prepared.state["not_used_reason"]
    assert engine["rendered"] == 0
    assert prepared.state["plan"]["cut_count"] == 2, "the plan is still recorded"


def test_a_real_saving_is_still_rendered(source, engine, monkeypatch):
    """The line has to leave working material alone: an ordinary video at 1% / 6s."""
    monkeypatch.setattr(dj.silence_cuts, "carries_cut_mark", lambda *_a, **_k: False)
    engine["plan"] = _plan((30.0, 36.0), duration=690.0)

    prepared = dj.prepare_cut_media(TASK, source)

    assert prepared.used is True and engine["rendered"] == 1


def test_a_file_cut_by_something_else_is_not_claimed_to_be_detectable(tmp_path, monkeypatch, engine):
    """There is no signature for another tool's output, and none is needed: measured
    on a real cut file, a second pass finds ~0% to remove. It runs and lands safely
    instead of being detected, and nothing is claimed that was not measured."""
    foreign = tmp_path / "edited-elsewhere.mp4"
    foreign.write_bytes(b"cut by some other editor")
    monkeypatch.setattr(dj.silence_cuts, "carries_cut_mark", lambda *_a, **_k: False)
    engine["plan"] = _plan()  # nothing left worth cutting

    prepared = dj.prepare_cut_media(TASK, foreign)

    assert prepared.state.get("already_cut") is None, "we do not pretend to know"
    assert prepared.state["plan"]["cut_count"] == 0
    assert engine["rendered"] == 0, "and nothing is re-encoded for no reason"


def test_the_render_stamps_its_output_so_it_can_be_recognised_later(monkeypatch, tmp_path):
    """The mark has to be written, or the recognition above never fires."""
    from backend.core import silence_cuts as sc

    seen: dict = {}

    def runner(command, **_kwargs):
        text = " ".join(str(part) for part in command)
        if "concat" in text:
            seen["concat"] = text
            Path(command[-1]).write_bytes(b"out")
        elif "-show_entries" in text:
            return subprocess.CompletedProcess(command, 0, stdout="2\n", stderr="")
        else:
            Path(command[-1]).write_bytes(b"part")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    # Nothing is executed — the runner is fake — but the command is still built
    # from a resolved binary, and CI has no ffmpeg installed.
    monkeypatch.setattr(sc, "_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(sc, "_ffprobe_path", lambda: "ffprobe")

    out = tmp_path / "cut.mp4"
    sc.render_keeps(tmp_path / "in.mp4", [TimeRange(0.0, 5.0)], out, runner=runner)

    assert sc.CUT_FILE_MARK in seen["concat"]


# ── Finding a dropped file on the machine that holds it ────────────────────
#
# A browser hands a dropped file's contents to the page with its name, size and
# modification time, and deliberately never its folder. That is why a drop has to
# be uploaded: a second copy of a gigabyte into the store, and no "next to the
# original" for the cut file to go to. This edition runs on the machine holding
# the file, so it can look — cheaply, and only where the owner already sent it.


def _drop(path):
    info = path.stat()
    return path.name, info.st_size, info.st_mtime * 1000


def test_a_dropped_file_is_found_in_a_folder_this_machine_has_used(tmp_path):
    folder = tmp_path / "lectures"
    folder.mkdir()
    recording = folder / "week-8.mp4"
    recording.write_bytes(b"x" * 4096)
    name, size, modified = _drop(recording)

    found = local_folder_intake.locate_dropped_file(name, size, [folder], modified_ms=modified)

    assert found == recording.resolve()


def test_a_file_of_the_same_name_but_a_different_size_is_not_it(tmp_path):
    folder = tmp_path / "lectures"
    folder.mkdir()
    (folder / "week-8.mp4").write_bytes(b"x" * 4096)

    assert local_folder_intake.locate_dropped_file("week-8.mp4", 9999, [folder]) is None


def test_a_file_modified_at_a_different_time_is_not_it(tmp_path):
    folder = tmp_path / "lectures"
    folder.mkdir()
    recording = folder / "week-8.mp4"
    recording.write_bytes(b"x" * 4096)
    name, size, _ = _drop(recording)

    assert local_folder_intake.locate_dropped_file(name, size, [folder], modified_ms=0) is None


def test_two_folders_holding_the_same_file_produce_no_answer(tmp_path):
    """Delivering a cut file beside the wrong copy is worse than not delivering
    it, so an ambiguous match is refused rather than guessed at."""
    first, second = tmp_path / "a", tmp_path / "b"
    for folder in (first, second):
        folder.mkdir()
        (folder / "week-8.mp4").write_bytes(b"x" * 4096)
    name, size, modified = _drop(first / "week-8.mp4")

    assert local_folder_intake.locate_dropped_file(name, size, [first, second], modified_ms=modified) is None


def test_a_file_from_a_folder_this_machine_has_never_seen_is_not_found(tmp_path):
    """Which costs nothing: the page uploads, exactly as it does today."""
    known, elsewhere = tmp_path / "known", tmp_path / "elsewhere"
    for folder in (known, elsewhere):
        folder.mkdir()
    recording = elsewhere / "week-8.mp4"
    recording.write_bytes(b"x" * 4096)
    name, size, modified = _drop(recording)

    assert local_folder_intake.locate_dropped_file(name, size, [known], modified_ms=modified) is None


def test_nothing_is_searched_for_without_a_name_or_a_size(tmp_path):
    assert local_folder_intake.locate_dropped_file("", 4096, [tmp_path]) is None
    assert local_folder_intake.locate_dropped_file("week-8.mp4", 0, [tmp_path]) is None
