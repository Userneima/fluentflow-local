"""The recording is read whole before it is cut, then cut once.

Requirements (owner, 2026-10-15, 方案/2026-10-15-先转录再定怎么剪):
1. Gaps are cut, except those the model keeps because the picture matters.
2. A stretch with talk in it that the model names as outside the content
   (leftover after the end) is cut too, and listed for the user to confirm in
   the original recording's time, with the model's reason.
3. Removals that would take away more than a quarter of what was said are not
   applied; the gap decisions still are.
4. Without a model, or when it fails, the loudness plan is cut unchanged.
5. After the cut the transcript is on the cut file's clock; the original's
   lines are kept beside it. The cut goes into the user's folder.
6. Every single cut longer than a minute, and gaps in which words were
   transcribed, are listed for confirmation too.
7. With the new flow on, nothing is cut before transcription.
9. No cut reaches into a transcribed word, with or without a model.
10. Filler in the middle (sound checks, a sentence said twice, off-topic
    talk) is removed too; every removed stretch is listed, whatever their number.
11. Pauses under three seconds are cut without asking the model.
8. Each listed cut can be answered "right" or "wrong", and taken back. "Wrong"
   puts that stretch back and renders again without asking the model again;
   the transcript follows.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from backend.core import job_store, local_intake_flow, silence_cuts, survey_cut
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
    review = record["review"]
    assert [item["kind"] for item in review] == ["removed_talk"]
    assert review[0]["start"] == pytest.approx(50.4, abs=0.2) and "拜拜" in review[0]["reason"]
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


def test_long_cuts_and_words_in_gaps_are_listed_for_confirmation():
    lines = [(0.5, 9.5, "开场"), (11.0, 12.0, "对对"), (100.0, 110.0, "继续讲正事")]
    plan, record = survey_cut.revise_plan(
        Path("x.mp4"), _plan(120.0, [(10, 14), (20, 95)]), _segments(lines),
        _says('{"keep_gaps": [], "remove": []}'), pictures=_no_pictures,
    )

    kinds = {item["kind"] for item in record["review"]}
    assert kinds == {"long_gap", "words_in_gap"}
    words = next(item for item in record["review"] if item["kind"] == "words_in_gap")
    assert "对对" in words["reason"]


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
    assert record["review"][0]["kind"] == "removed_talk"
    assert state["survey"]["review"] == record["review"]


@needs_ffmpeg
def test_without_a_model_the_loudness_plan_is_cut_unchanged(task, monkeypatch):
    monkeypatch.setattr(survey_cut, "_default_chat", lambda: None)

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


@needs_ffmpeg
def test_answers_are_recorded_and_can_be_taken_back(task, monkeypatch):
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    survey_cut.survey_and_cut(task["id"], chat=(_says(TAIL), "fake"))

    survey_cut.answer(task["id"], 0, "right")
    assert job_store.get_job(task["id"])["result"]["debreath"]["survey"]["answers"] == {"0": "right"}
    survey_cut.answer(task["id"], 0, "pending")
    assert job_store.get_job(task["id"])["result"]["debreath"]["survey"]["answers"] == {}
    with pytest.raises(survey_cut.ReviewError):
        survey_cut.answer(task["id"], 5, "right")


@needs_ffmpeg
def test_a_cut_said_to_be_wrong_is_put_back_and_rendered_again(task, monkeypatch):
    monkeypatch.setattr(survey_cut, "measure_pictures", lambda source, gaps: {})
    survey_cut.survey_and_cut(task["id"], chat=(_says(TAIL), "fake"))

    survey_cut.restore(task["id"], 0)

    result = job_store.get_job(task["id"])["result"]
    state = result["debreath"]
    # The leftover is back; the kept gap is still kept, the other two still cut.
    assert silence_cuts.media_duration_seconds(Path(state["delivered_path"])) == pytest.approx(52.4, abs=1.0)
    assert "嗯" in [seg["text"] for seg in result["raw_segments"]]
    assert result["transcript_media"] == "debreath_media"
    assert len(result["source_raw_segments"]) == len(LINES)
    assert state["survey"]["answers"] == {"0": "wrong"} and state["survey"]["review"][0]["restored"] is True


def test_no_cut_reaches_into_a_transcribed_word():
    plan, record = survey_cut.protect_words(_plan(gaps=[(10, 14)]), [(11.0, 11.5), (30.0, 31.0)])

    assert [(c.start, c.end) for c in plan.cuts] == [(10.1, 10.95), (11.55, 13.9)]
    assert record["words_known"] is True and record["seconds_kept_for_words"] == pytest.approx(0.6, abs=0.01)


def test_without_word_times_the_cuts_stand():
    plan, record = survey_cut.protect_words(_plan(), [])

    assert len(plan.cuts) == 3 and record == {"words_known": False}


def test_filler_in_the_middle_is_removed_and_every_removal_is_listed():
    lines = [(float(t), t + 4.0, f"第{t}秒的正事内容讲得比较长一些") for t in range(0, 240, 5)]
    remove = ", ".join(f'{{"from": {t}, "to": {t}, "reason": "整句重说"}}' for t in range(10, 80, 10))
    plan, record = survey_cut.revise_plan(
        Path("x.mp4"), _plan(240.0, []), _segments(lines),
        _says(f'{{"keep_gaps": [], "remove": [{remove}]}}'), pictures=_no_pictures,
    )

    assert [item["kind"] for item in record["review"]] == ["removed_talk"] * 7
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
    result = job_store.get_job(task["id"])["result"]
    result["stt_raw_segments"] = [
        {"start": 0.5, "end": 9.5, "text": "开场", "words": [{"start": 0.5, "end": 9.5, "text": "开场", "conf": 0.9}]},
        {"start": 11.0, "end": 12.0, "text": "对", "words": [{"start": 11.0, "end": 12.0, "text": "对", "conf": 0.9}]},
    ]
    job_store.update_job_result(task["id"], result)

    record = survey_cut.survey_and_cut(task["id"], chat=(_says('{"keep_gaps": [], "remove": []}'), "fake"))

    assert record["word_protection"]["words_known"] is True
    media = Path(job_store.get_job(task["id"])["result"]["debreath"]["delivered_path"])
    # The 10-14 gap is cut on both sides of the word only: 1.1 s kept of it.
    assert silence_cuts.media_duration_seconds(media) == pytest.approx(48.6 + 1.1, abs=1.0)
