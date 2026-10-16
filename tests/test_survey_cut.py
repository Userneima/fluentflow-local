"""The recording is read whole before it is cut, then cut once.

Requirements (owner, 2026-10-15, 方案/2026-10-15-先转录再定怎么剪):
1. Gaps are cut, except those the model keeps because the picture matters.
2. A stretch with talk in it that the model names as outside the content
   (leftover after the end) is cut too, with the model's reason, and put to
   the user to judge in the processing report.
3. Removals that would take away more than a quarter of what was said are not
   applied; the gap decisions still are.
4. Without a model, or when it fails, the loudness plan is cut unchanged.
5. After the cut the transcript is on the cut file's clock; the original's
   lines are kept beside it. The cut goes into the user's folder.
7. With the new flow on, nothing is cut before transcription.
9. No cut reaches into speech: a transcribed word that the voice detector also
   hears. A word's time alone is not enough (the transcriber stretches it over
   the pause after it). With or without a model.
10. Filler in the middle (sound checks, a sentence said twice, off-topic
    talk) is removed too, however many stretches there are.
11. Pauses under three seconds are cut without asking the model.
12. Any timed item of the processing report can be turned around: what was
    cut is put back, what was kept is cut. The change is recorded next to what
    the model had decided, and the report says it was the user's.
13. Each item of the report can be agreed with as it is ("剪得对" / "确实该保留"),
    one by one or a whole group, and taken back; the agreement survives the
    report being written again. An item nobody answered is not looked at.
14. Every change the user made stays in the report's bin and can be undone
    later: the model's decision is applied again and the cut rendered again.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from backend.core import cut_timeline, job_store, local_intake_flow, silence_cuts, survey_cut
from backend.core.silence_cuts import CutPlan, TimeRange

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")

GAPS = [(10, 14), (24, 28), (38, 42)]
LINES = [
    (0.5, 9.5, "今天我们先过一遍上周的进度，每个人讲一下自己负责的部分"),
    (14.5, 23.5, "接下来大家看一下这页表格，我停一下让大家读完"),
    (28.5, 37.5, "表格里第三列是本周新增的数字，比上周多了不少"),
    (42.5, 49.5, "好，今天就到这里，拜拜"),
    (50.5, 59.5, "嗯"),
]


def _plan(duration: float = 60.0, gaps=GAPS) -> CutPlan:
    cuts = [TimeRange(a + 0.1, b - 0.1) for a, b in gaps]
    return CutPlan(
        source_duration_seconds=duration, cuts=cuts,
        keeps=silence_cuts._keeps_between(duration, cuts, silence_cuts.MIN_KEEP_SECONDS),
        silences_found=len(cuts), noise_db=-30, min_silence_seconds=0.25, padding_seconds=0.1,
    )


def _segments(lines=LINES):
    return [{"start": a, "end": b, "text": t} for a, b, t in lines]


def _says(text):
    return lambda system, user: text


def _no_pictures(source, gaps):
    return {}


def test_kept_gap_stays_and_leftover_after_the_end_is_removed():
    plan, record = survey_cut.revise_plan(
        Path("x.mp4"), _plan(), _segments(),
        _says('{"keep_gaps": [{"id": 2, "reason": "讲者让大家读表格"}],'
              ' "remove": [{"from": 50.5, "to": null, "reason": "说完拜拜后多录的"}]}'),
        pictures=_no_pictures,
    )

    cut = [(c.start, c.end) for c in plan.cuts]
    assert (24.1, 27.9) not in cut
    assert (10.1, 13.9) in cut and (38.1, 41.9) in cut
    assert plan.cuts[-1].end == 60.0 and plan.cuts[-1].start == pytest.approx(50.4, abs=0.2)
    removed = record["removed"]
    assert len(removed) == 1
    assert removed[0]["start"] == pytest.approx(50.4, abs=0.2) and "拜拜" in removed[0]["reason"]
    assert record["kept_gaps"][0]["reason"] == "讲者让大家读表格"


def test_a_removal_that_takes_too_much_talk_is_refused_but_gaps_still_apply():
    plan, record = survey_cut.revise_plan(
        Path("x.mp4"), _plan(), _segments(),
        _says('{"keep_gaps": [{"id": 1, "reason": "演示"}], "remove": [{"from": 14.5, "to": null, "reason": "?"}]}'),
        pictures=_no_pictures,
    )

    assert "removal_refused" in record
    assert all(c.end < 50 for c in plan.cuts)
    assert (10.1, 13.9) not in [(c.start, c.end) for c in plan.cuts]


def test_the_model_reads_every_line_and_numbered_long_gaps_with_their_picture():
    seen = {}

    def model(system, user):
        seen["user"] = user
        return '{"keep_gaps": [], "remove": []}'

    survey_cut.revise_plan(
        Path("x.mp4"), _plan(), _segments(), model,
        pictures=lambda source, gaps: {0: 0.5, 1: 0.0},
    )

    text = seen["user"]
    assert all(line in text for _, _, line in LINES)
    assert "〔空白 #1 · 10.1–13.9 秒" in text and "画面一直在变" in text and "画面静止" in text
    assert text.index("今天我们先过") < text.index("空白 #1") < text.index("接下来大家看")


# --- end to end, on a short synthetic recording ---------------------------

def _recording(target: Path) -> Path:
    silent = "*".join(f"(1-between(t,{a},{b}))" for a, b in GAPS)
    subprocess.run(
        [shutil.which("ffmpeg"), "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=s=160x90:d=60:r=10",
         "-f", "lavfi", "-i", f"aevalsrc='0.5*sin(2*PI*300*t)*{silent}':d=60:s=16000",
         "-shortest", "-c:v", "libx264", "-g", "10", "-c:a", "aac", str(target)],
        check=True,
    )
    return target


@pytest.fixture
def task(tmp_path):
    (tmp_path / "周会").mkdir()
    source = _recording(tmp_path / "周会" / "周会.mp4")
    task_id = f"sv-{uuid.uuid4().hex[:8]}"
    segments = _segments()
    job_store.upsert_job(
        task_id=task_id, status="completed",
        metadata={"folder_intake": {"original_path": str(source)}},
        result={
            "raw_segments": segments, "display_segments": segments,
            "transcript_text": "\n".join(s["text"] for s in segments),
            "transcript_media": "source",
        },
    )
    return {"id": task_id, "source": source}


@needs_ffmpeg
def test_the_recording_is_cut_once_and_the_transcript_moves_onto_the_cut(task, monkeypatch):
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    answer = ('{"keep_gaps": [{"id": 2, "reason": "讲者让大家读表格"}],'
              ' "remove": [{"from": 50.5, "to": null, "reason": "说完拜拜后多录的"}]}')

    record = survey_cut.survey_and_cut(task["id"], chat=(_says(answer), "fake"))

    result = job_store.get_job(task["id"])["result"]
    state = result["debreath"]
    assert state["rendered"] is True and state["used_for_transcription"] is True
    assert result["transcript_media"] == "debreath_media"
    media = Path(state["delivered_path"])
    assert media.is_file() and media.parent.name.startswith("周会")
    # 60 s minus two 3.8 s gaps and the 9.6 s leftover; the kept gap stays.
    assert silence_cuts.media_duration_seconds(media) == pytest.approx(42.8, abs=1.0)
    texts = [seg["text"] for seg in result["raw_segments"]]
    assert "嗯" not in texts and "好，今天就到这里，拜拜" in texts
    table = next(seg for seg in result["raw_segments"] if seg["text"].startswith("表格里"))
    assert table["start"] == pytest.approx(28.5 - 3.8, abs=0.3)
    assert len(result["source_raw_segments"]) == len(LINES)
    report = state["survey"]["report"]
    assert report["before_seconds"] == pytest.approx(60.0, abs=0.5)
    assert report["after_seconds"] == pytest.approx(42.8, abs=1.0)
    refs = [item["ref"] for section in report["sections"] for item in section["items"]]
    assert "r1" in refs and any(ref.startswith("g") for ref in refs)
    # Removed talk is always put to the user.
    assert report["sections"][0]["title"] == "需要你判断"
    assert [item["ref"] for item in report["sections"][0]["items"]] == ["r1"]


@needs_ffmpeg
def test_the_plan_is_handed_on_before_the_render_and_a_note_saved_meanwhile_is_kept(task, monkeypatch):
    """To-do batch three: the note starts from the decided plan while the cut
    renders; what it saves during the render is still there afterwards."""
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    seen: dict = {}

    def on_plan(ranges):
        seen["ranges"] = ranges
        seen["stage"] = job_store.get_job(task["id"])["result"]["debreath"].get("stage")
        before = job_store.get_job(task["id"])["result"]
        job_store.save_result_changes(task["id"], before, {**before, "summary_markdown": "# 边剪边写的笔记"})

    survey_cut.survey_and_cut(task["id"], chat=(_says('{"keep_gaps": [], "remove": []}'), "fake"), on_plan=on_plan)

    result = job_store.get_job(task["id"])["result"]
    assert seen["stage"] == "reviewing"
    rendered = silence_cuts.media_duration_seconds(Path(result["debreath"]["delivered_path"]))
    assert cut_timeline.kept_duration_seconds(seen["ranges"]) == pytest.approx(rendered, abs=0.5)
    assert result["summary_markdown"] == "# 边剪边写的笔记"
    assert result["transcript_media"] == "debreath_media"


@needs_ffmpeg
def test_without_a_model_the_loudness_plan_is_cut_unchanged(task, monkeypatch):
    monkeypatch.setattr(survey_cut.cut_judge, "choose", lambda *a, **k: None)

    record = survey_cut.survey_and_cut(task["id"])

    result = job_store.get_job(task["id"])["result"]
    assert "skipped" in record
    assert silence_cuts.media_duration_seconds(Path(result["debreath"]["delivered_path"])) == pytest.approx(48.6, abs=1.0)


@needs_ffmpeg
def test_a_failing_model_leaves_the_loudness_plan(task, monkeypatch):
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})

    def broken(system, user):
        raise RuntimeError("down")

    record = survey_cut.survey_and_cut(task["id"], chat=(broken, "fake"))

    assert "error" in record
    result = job_store.get_job(task["id"])["result"]
    assert result["debreath"]["rendered"] is True


def test_nothing_is_cut_before_transcription_on_the_new_flow(monkeypatch, tmp_path):
    monkeypatch.delenv(local_intake_flow.SURVEY_FIRST_ENV, raising=False)
    monkeypatch.delenv(local_intake_flow.CUT_FIRST_ENV, raising=False)
    assert local_intake_flow.survey_first_enabled() is True
    assert local_intake_flow.preprocess_media("t", tmp_path / "a.mp4") is None


TAIL = ('{"keep_gaps": [{"id": 2, "reason": "讲者让大家读表格"}],'
        ' "remove": [{"from": 50.5, "to": null, "reason": "说完拜拜后多录的"}]}')


def test_no_cut_reaches_into_a_transcribed_word():
    plan, record = survey_cut.protect_words(_plan(gaps=[(10, 14)]), [(11.0, 11.5), (30.0, 31.0)])

    assert [(c.start, c.end) for c in plan.cuts] == [(10.1, 10.95), (11.55, 13.9)]
    assert record["seconds_kept_for_words"] == pytest.approx(0.6, abs=0.01)


def test_without_word_times_the_cuts_stand():
    plan, record = survey_cut.protect_words(_plan(), [])

    assert len(plan.cuts) == 3 and record == {}


def test_only_words_the_voice_detector_hears_are_spoken():
    # A word stretched over 11-13.5; the detector hears a voice in 11-11.4.
    heard_in = []

    def detector(source, regions):
        heard_in.extend(regions)
        return [TimeRange(11.0, 11.4)]

    spoken, record = survey_cut.spoken_words(Path("x.mp4"), _plan(gaps=[(10, 14)]), [(11.0, 13.5), (30.0, 31.0)], detector)

    assert [(r.start, r.end) for r in heard_in] == [(11.0, 13.5)]
    assert spoken == [(11.0, 11.4)]
    assert record["words_known"] is True


def test_without_a_voice_detector_words_do_not_block_cuts():
    spoken, record = survey_cut.spoken_words(Path("x.mp4"), _plan(gaps=[(10, 14)]), [(11.0, 12.0)], lambda s, r: None)

    assert spoken == [] and record["voice_unchecked"] is True


def test_filler_in_the_middle_is_removed_and_every_removal_is_listed():
    lines = [(float(t), t + 4.0, f"第{t}秒的正事内容讲得比较长一些") for t in range(0, 240, 5)]
    remove = ", ".join(f'{{"from": {t}, "to": {t}, "reason": "整句重说"}}' for t in range(10, 80, 10))
    plan, record = survey_cut.revise_plan(
        Path("x.mp4"), _plan(240.0, []), _segments(lines),
        _says(f'{{"keep_gaps": [], "remove": [{remove}]}}'), pictures=_no_pictures,
    )

    assert len(record["removed"]) == 7
    assert any(c.start <= 10.0 and c.end >= 14.0 for c in plan.cuts)
    assert not any(c.start < 5.0 < c.end for c in plan.cuts)


def test_short_pauses_are_not_shown_to_the_model():
    seen = {}

    def model(system, user):
        seen["user"] = user
        return '{"keep_gaps": [], "remove": []}'

    survey_cut.revise_plan(Path("x.mp4"), _plan(gaps=[(10, 12), (24, 28)]), _segments(), model, pictures=_no_pictures)

    assert "10.1–11.9" not in seen["user"] and "〔空白 #1 · 24.1–27.9 秒" in seen["user"]


@needs_ffmpeg
def test_a_quiet_word_inside_a_gap_survives_the_cut(task, monkeypatch):
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    # The synthetic gap is digital silence; stand in for a detector hearing the word.
    monkeypatch.setattr(survey_cut, "voiced_parts", lambda source, regions: regions)
    result = job_store.get_job(task["id"])["result"]
    result["stt_raw_segments"] = [
        {"start": 0.5, "end": 9.5, "text": "开场", "words": [{"start": 0.5, "end": 9.5, "text": "开场", "conf": 0.9}]},
        {"start": 11.0, "end": 12.0, "text": "对", "words": [{"start": 11.0, "end": 12.0, "text": "对", "conf": 0.9}]},
    ]
    job_store.update_job_result(task["id"], result)

    record = survey_cut.survey_and_cut(task["id"], chat=(_says('{"keep_gaps": [], "remove": []}'), "fake"))

    assert record["word_protection"]["words_known"] is True
    # The report quotes only the words saved, not the line around them.
    assert [span["said"] for span in record["spoken"]] == ["对"]
    media = Path(job_store.get_job(task["id"])["result"]["debreath"]["delivered_path"])
    # The 10-14 gap is cut on both sides of the word only: 1.1 s kept of it.
    assert silence_cuts.media_duration_seconds(media) == pytest.approx(48.6 + 1.1, abs=1.0)


@needs_ffmpeg
def test_a_report_item_that_was_cut_is_put_back(task, monkeypatch):
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    survey_cut.survey_and_cut(task["id"], chat=(_says(TAIL), "fake"))

    survey_cut.flip(task["id"], "r1")

    state = job_store.get_job(task["id"])["result"]["debreath"]
    assert silence_cuts.media_duration_seconds(Path(state["delivered_path"])) == pytest.approx(52.4, abs=1.0)
    change = state["survey"]["user_changes"][0]
    assert (change["model_said"], change["user_said"]) == ("removed", "kept")
    refs = [item["ref"] for section in state["survey"]["report"]["sections"] for item in section["items"]]
    assert "u1" in refs and "r1" not in refs


@needs_ffmpeg
def test_a_report_item_that_was_kept_is_cut(task, monkeypatch):
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    survey_cut.survey_and_cut(task["id"], chat=(_says(TAIL), "fake"))
    kept = [item for section in job_store.get_job(task["id"])["result"]["debreath"]["survey"]["report"]["sections"]
            for item in section["items"] if item["action"] == "kept"]
    assert [item["kind"] for item in kept] == ["long_gap"]

    survey_cut.flip(task["id"], kept[0]["ref"])

    state = job_store.get_job(task["id"])["result"]["debreath"]
    assert silence_cuts.media_duration_seconds(Path(state["delivered_path"])) == pytest.approx(42.8 - 3.8, abs=1.0)
    assert state["survey"]["user_changes"][0]["user_said"] == "removed"


def _report_items(task_id):
    survey = job_store.get_job(task_id)["result"]["debreath"]["survey"]
    return [item for section in survey["report"]["sections"] for item in section["items"]]


@needs_ffmpeg
def test_agreement_is_kept_by_item_and_survives_a_rerender(task, monkeypatch):
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    survey_cut.survey_and_cut(task["id"], chat=(_says(TAIL), "fake"))
    gaps = [item for item in _report_items(task["id"]) if item["kind"] == "long_gap" and item["action"] == "removed"]

    survey_cut.verdict(task["id"], [item["key"] for item in gaps], "agree")
    survey_cut.flip(task["id"], "r1")

    verdicts = job_store.get_job(task["id"])["result"]["debreath"]["survey"]["verdicts"]
    keys_now = {item["key"] for item in _report_items(task["id"])}
    assert set(verdicts) == {item["key"] for item in gaps} and set(verdicts) <= keys_now
    survey_cut.verdict(task["id"], [gaps[0]["key"]], "pending")
    assert len(job_store.get_job(task["id"])["result"]["debreath"]["survey"]["verdicts"]) == len(gaps) - 1


@needs_ffmpeg
def test_a_change_can_be_undone_from_the_bin(task, monkeypatch):
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    survey_cut.survey_and_cut(task["id"], chat=(_says(TAIL), "fake"))
    survey_cut.flip(task["id"], "r1")

    survey_cut.undo_change(task["id"], 0)

    state = job_store.get_job(task["id"])["result"]["debreath"]
    assert silence_cuts.media_duration_seconds(Path(state["delivered_path"])) == pytest.approx(42.8, abs=1.0)
    assert state["survey"]["user_changes"][0]["undone_at"]
    kinds = [item["kind"] for item in _report_items(task["id"])]
    assert "user_change" not in kinds and "removed_talk" in kinds
    with pytest.raises(survey_cut.ReviewError):
        survey_cut.undo_change(task["id"], 0)


@needs_ffmpeg
def test_without_word_times_the_lines_mark_where_someone_spoke(task, monkeypatch):
    """The transcriber is not asked for word times (it costs 18-40% more time
    for the same result); the lines are used, and the detector decides."""
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    heard = []

    def detector(source, regions):
        heard.extend(regions)
        return [TimeRange(11.0, 11.5)]

    monkeypatch.setattr(survey_cut, "voiced_parts", detector)
    result = job_store.get_job(task["id"])["result"]
    result["raw_segments"] = result["display_segments"] = _segments() + [{"start": 10.5, "end": 13.5, "text": "对"}]
    job_store.update_job_result(task["id"], result)

    record = survey_cut.survey_and_cut(task["id"], chat=(_says('{"keep_gaps": [], "remove": []}'), "fake"))

    assert any(r.start <= 11.0 and r.end >= 11.5 for r in heard)
    assert record["spoken"] == [{"start": 11.0, "end": 11.5, "said": ""}]


def _state(task_id):
    result = job_store.get_job(task_id)["result"]
    return result, result["debreath"]


@needs_ffmpeg
def test_a_rerender_that_fails_leaves_the_cut_as_it_was(task, monkeypatch):
    """Owner, 2026-10-15: a failed re-render must not leave the task half way."""
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    survey_cut.survey_and_cut(task["id"], chat=(_says(TAIL), "fake"))
    before, _ = _state(task["id"])

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(survey_cut.debreath_job, "run_debreath", broken)
    with pytest.raises(RuntimeError):
        survey_cut.flip(task["id"], "r1")

    result, state = _state(task["id"])
    assert result["transcript_media"] == "debreath_media" and state["used_for_transcription"] is True
    assert [s["start"] for s in result["raw_segments"]] == [s["start"] for s in before["raw_segments"]]
    assert state["survey"]["removed"] == before["debreath"]["survey"]["removed"]
    assert "disk full" in state["survey"]["restore_error"] and "rerendering" not in state["survey"]


@needs_ffmpeg
def test_a_rerender_stopped_by_a_restart_is_put_back_at_startup(task, monkeypatch):
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    survey_cut.survey_and_cut(task["id"], chat=(_says(TAIL), "fake"))
    before, _ = _state(task["id"])

    # What a service stopped half way through a re-render leaves behind: new
    # decisions, the transcript back on the original's clock, the old cut.
    result, state = _state(task["id"])
    survey = dict(state["survey"])
    survey.update({"rerendering": True, "removed": [],
                   "before_rerender": {"removed": survey["removed"], "kept_gaps": survey["kept_gaps"]}})
    for key in ("display_segments", "raw_segments", "stt_raw_segments"):
        if isinstance(result.get(f"source_{key}"), list):
            result[key] = result[f"source_{key}"]
    result["transcript_media"] = "source"
    result["debreath"] = {**state, "survey": survey, "used_for_transcription": False}
    job_store.update_job_result(task["id"], result)

    assert survey_cut.recover_interrupted() >= 1

    result, state = _state(task["id"])
    assert result["transcript_media"] == "debreath_media"
    assert [s["start"] for s in result["raw_segments"]] == [s["start"] for s in before["raw_segments"]]
    assert "打断" in state["survey"]["restore_error"]


# --- regressions found by the read-only review, 2026-10-16 -------------------

@needs_ffmpeg
def test_subtitles_edited_on_the_cut_survive_a_rerender(task, monkeypatch):
    """Edits saved in the editor stay after a report item is turned around:
    the re-render starts from them, not from the lines as transcribed."""
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    survey_cut.survey_and_cut(task["id"], chat=(_says(TAIL), "fake"))
    result = job_store.get_job(task["id"])["result"]
    edited = [dict(seg) for seg in result["raw_segments"]]
    table = next(seg for seg in edited if seg["text"].startswith("表格里"))
    table["text"] = "表格里第三列是本周新增的用户数"
    result.update({"raw_segments": edited, "display_segments": edited, "transcript_edited": True})
    job_store.update_job_result(task["id"], result)

    survey_cut.flip(task["id"], "r1")

    result = job_store.get_job(task["id"])["result"]
    texts = [seg["text"] for seg in result["raw_segments"]]
    assert "表格里第三列是本周新增的用户数" in texts
    assert "表格里第三列是本周新增的数字，比上周多了不少" not in texts
    assert "嗯" in texts, "the stretch put back is in the subtitles again"
    line = next(seg for seg in result["raw_segments"] if seg["text"].startswith("表格里"))
    assert line["start"] == pytest.approx(28.5 - 3.8, abs=0.3)
    assert result["transcript_edited"] is True


@needs_ffmpeg
def test_putting_back_the_last_cut_finishes_with_the_original_as_the_video(task, monkeypatch):
    """When every cut is put back there is nothing to render: the task ends
    with the original as the video, the transcript on its clock, the report
    written again, and no re-render left showing."""
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    monkeypatch.setattr(survey_cut, "voiced_parts", lambda source, regions: [])
    answer = '{"keep_gaps": [{"id": 1, "reason": "看表"}, {"id": 2, "reason": "看表"}], "remove": []}'
    survey_cut.survey_and_cut(task["id"], chat=(_says(answer), "fake"))
    _, state = _state(task["id"])
    delivered = Path(state["delivered_path"])
    removed = [item for item in _report_items(task["id"]) if item["action"] == "removed"]
    assert [item["kind"] for item in removed] == ["long_gap"]

    survey_cut.flip(task["id"], removed[0]["ref"])

    result, state = _state(task["id"])
    assert state["status"] == "completed" and state["rendered"] is False
    assert "rerendering" not in state["survey"] and "before_rerender" not in state["survey"]
    assert result["transcript_media"] == "source" and result.get("playback_media_kind") is None
    assert state["used_for_transcription"] is False
    table = next(seg for seg in result["raw_segments"] if seg["text"].startswith("表格里"))
    assert table["start"] == pytest.approx(28.5, abs=0.01)
    report = state["survey"]["report"]
    assert report["after_seconds"] == report["before_seconds"] == pytest.approx(60.0, abs=0.5)
    assert not delivered.exists(), "the old cut is not left in the folder as if it were current"
    assert state["survey"]["user_changes"][0]["user_said"] == "kept"


@needs_ffmpeg
def test_the_corrected_transcript_moves_onto_the_cut_too(task, monkeypatch):
    """The text note and an agent read the corrected lines first; they are on
    the cut's clock with the cut stretches gone, like the other lines."""
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    result = job_store.get_job(task["id"])["result"]
    corrected = [{**seg, "text": seg["text"].replace("表格", "报表")} for seg in result["raw_segments"]]
    result.update({"corrected_segments": corrected, "corrected_transcript_text": "\n".join(s["text"] for s in corrected)})
    job_store.update_job_result(task["id"], result)

    survey_cut.survey_and_cut(task["id"], chat=(_says(TAIL), "fake"))

    result = job_store.get_job(task["id"])["result"]
    lines = result["corrected_segments"]
    assert "嗯" not in [seg["text"] for seg in lines]
    third = next(seg for seg in lines if seg["text"].startswith("报表里"))
    assert third["start"] == pytest.approx(28.5 - 3.8, abs=0.3)
    assert "嗯" not in result["corrected_transcript_text"].splitlines()
    assert "报表里第三列" in result["corrected_transcript_text"]
    assert len(result["source_corrected_segments"]) == len(LINES)

    survey_cut.flip(task["id"], "r1")

    lines = job_store.get_job(task["id"])["result"]["corrected_segments"]
    assert "嗯" in [seg["text"] for seg in lines] and all(seg["text"] != "表格里第三列是本周新增的数字，比上周多了不少" for seg in lines)


@needs_ffmpeg
def test_putting_back_every_cut_leaves_a_video_the_user_put_in_its_place(task, monkeypatch):
    """Review 2026-10-16: the old cut is taken out of the folder only while
    that path is still the task's own file, not on the record alone."""
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    monkeypatch.setattr(survey_cut, "voiced_parts", lambda source, regions: [])
    answer = '{"keep_gaps": [{"id": 1, "reason": "看表"}, {"id": 2, "reason": "看表"}], "remove": []}'
    survey_cut.survey_and_cut(task["id"], chat=(_says(answer), "fake"))
    _, state = _state(task["id"])
    delivered = Path(state["delivered_path"])
    delivered.unlink()
    delivered.write_bytes(b"the user's own edit")
    removed = [item for item in _report_items(task["id"]) if item["action"] == "removed"]

    survey_cut.flip(task["id"], removed[0]["ref"])

    assert delivered.read_bytes() == b"the user's own edit"
