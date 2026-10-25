"""Fixing misheard words from the subtitles burned into the picture.

Written from the requirement (owner, 2026-10-24 and 2026-10-25), not from the code:

- A video whose picture carries no subtitles is left alone, quickly, without
  asking a model anything. So is audio, and a machine without text recognition.
- With subtitles, a word the transcript misheard is replaced by the spelling the
  subtitle shows, in the transcript itself, with a record of what changed.
- Every sentence is compared with the subtitle, not only those a model thought
  doubtful; the model sees only sentences the subtitle reads differently, and
  not those where the subtitle only leaves fillers out.
- A word is replaced only when a subtitle line read off the picture spells it
  out; the model cannot write in a spelling the picture did not show.
- Nothing here fails a task.
- Any change can be undone, and the subtitle files follow.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routers.local_agent as local_agent
from backend.core import frame_text, job_store, subtitle_correction as sc
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.core.result_artifacts import _attach_result_artifacts
from backend.core.storage_paths import _artifact_storage_dir

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "今天我们的嘉宾是罗福利"},
    {"start": 2.0, "end": 4.0, "text": "他目前是小米大模型的负责人"},
    {"start": 4.0, "end": 6.0, "text": "访谈是在OpenCloud发布之后"},
    {"start": 6.0, "end": 8.0, "text": "嗯我们更深入地聊了聊"},
    {"start": 8.0, "end": 10.0, "text": "应该是GOM跟KIMI"},
    {"start": 10.0, "end": 12.0, "text": "这些能力都是可以被学会的"},
    {"start": 12.0, "end": 14.0, "text": "环境反而比经验更重要"},
    {"start": 14.0, "end": 16.0, "text": "大家太相信MA了"},
]

# What the picture shows each second, as the subtitle author wrote it. The
# 6-8s line drops the filler 嗯; that is editing, not a mishearing.
SUBTITLES = [
    (0.0, 2.0, "今天我们的嘉宾是罗福莉"),
    (2.0, 4.0, "她目前是小米大模型的负责人"),
    (4.0, 6.0, "访谈是在OpenClaw发布之后"),
    (6.0, 8.0, "我们更深入地聊了聊"),
    (8.0, 10.0, "训练的应该是GLM-5跟Kimi"),
    (10.0, 12.0, "这些能力都是可以被学会的"),
    (12.0, 14.0, "环境反而比经验更重要"),
    (14.0, 16.0, "大家太相信MLA了"),
]

RIGHT = {"罗福利": "罗福莉", "OpenCloud": "OpenClaw", "GOM": "GLM-5", "MA": "MLA", "他": "她"}


def _probe(subtitles=SUBTITLES):
    """The bottom of one frame: the subtitle line where it sits, and a chapter
    bar under it."""
    reads: list[float] = []

    def read(video, at, workdir):
        reads.append(at)
        lines = [frame_text.TextBox(text, 0.88, 0.92, 0.3, 0.7) for start, end, text in subtitles if start <= at < end]
        return [*lines, frame_text.TextBox("1V2觉醒和伏击", 0.97, 0.99, 0.0, 0.2)]

    return read, reads


def _track(subtitles=SUBTITLES, garble: dict[int, str] | None = None):
    def read(video, band, workdir, stop=None):
        out = []
        for second in range(0, 16):
            text = next((t for start, end, t in subtitles if start <= second < end), "")
            out.append((float(second), (garble or {}).get(second, text)))
        return out

    return read


class Model:
    """Answers the way a model would, and records what it was shown."""

    def __init__(self, fixes=None):
        self.fixes = fixes
        self.asked: list[dict] = []

    def __call__(self, system: str, user: str) -> str:
        items = json.loads(user)
        self.asked.extend(items)
        if self.fixes is not None:
            return json.dumps({"fixes": self.fixes(items)}, ensure_ascii=False)
        found = []
        for item in items:
            for wrong, right in RIGHT.items():
                if wrong in item["sentence"] and any(right in sub for sub in item["subtitles"]):
                    found.append({"id": item["id"], "from": wrong, "to": right})
        return json.dumps({"fixes": found}, ensure_ascii=False)


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "访谈.mp4"
    path.write_bytes(b"video")
    return path


def _run(video, model, probe=None, track=None, segments=SEGMENTS, **kwargs):
    kwargs.setdefault("video_check", lambda path: True)
    kwargs.setdefault("ocr_available", lambda: True)
    return sc.correct_from_subtitles(
        video, segments, model, probe=probe or _probe()[0], track_reader=track or _track(), **kwargs,
    )


# ── when the step does nothing ────────────────────────────────────────────

def test_a_picture_without_subtitles_is_skipped_without_asking_a_model(video):
    model = Model()
    probe, reads = _probe(subtitles=[])

    def no_track(*_a):
        raise AssertionError("the whole video must not be read")

    outcome = _run(video, model, probe=probe, track=no_track)

    assert outcome.status == "skipped" and outcome.reason == "no_burned_subtitles"
    assert outcome.segments == SEGMENTS and outcome.corrections == []
    assert model.asked == [] and len(reads) <= sc.PROBE_SENTENCES


def test_audio_and_machines_without_text_recognition_are_skipped(video):
    probe, reads = _probe()

    assert _run(video, Model(), probe=probe, video_check=lambda path: False).reason == "no_picture"
    assert _run(video, Model(), probe=probe, ocr_available=lambda: False).reason == "no_text_recognition"
    assert sc.correct_from_subtitles(None, SEGMENTS, Model(), probe=probe, ocr_available=lambda: True).reason == "no_picture"
    assert reads == []


def test_subtitles_but_no_model_leaves_the_transcript(video):
    outcome = _run(video, None)

    assert outcome.status == "skipped" and outcome.reason == "no_model"
    assert outcome.segments == SEGMENTS


def test_nothing_here_fails_a_task(video):
    def broken(system, user):
        raise RuntimeError("model down")

    assert _run(video, broken).segments == SEGMENTS

    def unreadable(*_a):
        raise OSError("disk gone")

    outcome = _run(video, Model(), track=unreadable)
    assert outcome.status == "failed" and outcome.segments == SEGMENTS


def test_it_can_be_turned_off(monkeypatch):
    assert sc.enabled()
    monkeypatch.setenv("FLUENTFLOW_SUBTITLE_CORRECTION", "off")
    assert not sc.enabled()


# ── finding where the subtitle is ─────────────────────────────────────────

def test_the_strip_read_is_the_subtitle_line_not_the_chapter_bar_under_it(video, tmp_path):
    band, checked, matched = sc.locate(video, SEGMENTS, tmp_path, _probe()[0])

    assert matched >= sc.PROBE_NEEDED
    assert band.top < 0.88 and 0.92 <= band.bottom < 0.97


def test_one_line_read_on_several_seconds_is_one_cue_with_the_reading_most_agree_on():
    track = [(10.0, "感官之间的协同"), (11.0, "感官之间旳协同"), (12.0, "感官之间的协同"), (13.0, "下一句完全不同")]

    found = sc.cues(track)

    assert [(c.start, c.end, c.text) for c in found] == [(10.0, 13.0, "感官之间的协同"), (13.0, 14.0, "下一句完全不同")]


# ── what goes to the model ────────────────────────────────────────────────

def test_only_sentences_the_subtitle_reads_differently_reach_the_model(video):
    model = Model()

    _run(video, model)

    shown = sorted(item["sentence"] for item in model.asked)
    assert shown == sorted([
        "今天我们的嘉宾是罗福利", "他目前是小米大模型的负责人", "访谈是在OpenCloud发布之后",
        "应该是GOM跟KIMI", "大家太相信MA了",
    ])


def test_a_misheard_word_takes_the_subtitles_spelling_and_is_recorded(video):
    outcome = _run(video, Model())

    texts = [seg["text"] for seg in outcome.segments]
    assert texts[0] == "今天我们的嘉宾是罗福莉"
    assert texts[2] == "访谈是在OpenClaw发布之后"
    assert texts[4] == "应该是GLM-5跟KIMI"
    assert texts[7] == "大家太相信MLA了"
    assert texts[3] == "嗯我们更深入地聊了聊"
    record = next(r for r in outcome.corrections if r["from"] == "GOM")
    assert record["to"] == "GLM-5" and record["start"] == 8.0
    assert record["subtitle"] == "训练的应该是GLM-5跟Kimi"
    assert record["sentence_before"] == "应该是GOM跟KIMI"


def test_the_model_cannot_write_in_a_spelling_the_picture_did_not_show(video):
    model = Model(fixes=lambda items: [{"id": item["id"], "from": "GOM", "to": "GPT-5"} for item in items])

    assert _run(video, model).corrections == []


def test_the_model_cannot_replace_text_that_is_not_in_the_sentence(video):
    model = Model(fixes=lambda items: [{"id": item["id"], "from": "训练的", "to": "GLM-5"} for item in items])

    assert _run(video, model).corrections == []


def test_garbled_seconds_do_not_outvote_the_clean_reading(video):
    # The recogniser misreads one of the two seconds of a line.
    model = Model()

    outcome = _run(video, model, track=_track(garble={9: "训练旳应该是GLM-5跟Kimi"}))

    assert "应该是GLM-5跟KIMI" in [seg["text"] for seg in outcome.segments]


def test_subtitles_that_run_a_moment_behind_the_transcript_are_still_found(video):
    late = [(start + 1.0, end + 1.0, text) for start, end, text in SUBTITLES]

    outcome = _run(video, Model(), probe=_probe(late)[0], track=_track(late))

    assert "GLM-5" in [r["to"] for r in outcome.corrections]


def test_a_letter_the_recogniser_swaps_does_not_block_the_right_spelling(video):
    # OpenAI was read off the Luo Fuli interview as OpenAl.
    subtitles = [*SUBTITLES[:4], (8.0, 10.0, "然后有OpenAl"), *SUBTITLES[5:]]
    segments = [*SEGMENTS[:4], {"start": 8.0, "end": 10.0, "text": "然后有Open Eye"}, *SEGMENTS[5:]]
    model = Model(fixes=lambda items: [
        {"id": item["id"], "from": "Open Eye", "to": "OpenAI"} for item in items if "Open Eye" in item["sentence"]
    ])

    outcome = _run(video, model, probe=_probe(subtitles)[0], track=_track(subtitles), segments=segments)

    assert outcome.segments[4]["text"] == "然后有OpenAI"


# ── undoing ───────────────────────────────────────────────────────────────

def _corrected(video) -> dict:
    outcome = _run(video, Model())
    return {
        "raw_segments": outcome.segments,
        "display_segments": outcome.segments,
        "transcript_text": sc.joined(outcome.segments),
        "subtitle_corrections": outcome.corrections,
    }


def test_one_correction_is_undone(video):
    result = _corrected(video)
    gom = next(r["id"] for r in result["subtitle_corrections"] if r["from"] == "GOM")

    undone, reverted, missing = sc.revert(result, [gom])

    assert reverted == [gom] and missing == []
    assert undone["raw_segments"][4]["text"] == "应该是GOM跟KIMI"
    assert undone["display_segments"][4]["text"] == "应该是GOM跟KIMI"
    assert undone["raw_segments"][0]["text"] == "今天我们的嘉宾是罗福莉"
    assert "GOM" in undone["transcript_text"] and "罗福莉" in undone["transcript_text"]
    assert [r["reverted"] for r in undone["subtitle_corrections"] if r["id"] == gom] == [True]


def test_all_are_undone_without_ids_and_a_sentence_edited_since_is_reported(video):
    result = _corrected(video)
    result["raw_segments"][2]["text"] = "访谈是在别的什么发布之后"
    result["display_segments"] = [dict(seg) for seg in result["raw_segments"]]

    undone, reverted, missing = sc.revert(result, None)

    assert len(reverted) == len(result["subtitle_corrections"]) - 1 and len(missing) == 1
    assert sc.joined(undone["raw_segments"]).startswith("今天我们的嘉宾是罗福利")


TOKEN = "subtitle-token"
AGENT = {"x-fluentflow-access-token": TOKEN, "x-fluentflow-client-id": LOCAL_OWNER_ID}


def test_the_agent_undoes_a_correction_and_the_subtitle_files_follow(video, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", TOKEN)
    task_id = f"sc-{uuid.uuid4().hex[:8]}"
    corrected = _corrected(video)
    result = _attach_result_artifacts(task_id, {"filename": "访谈.mp4", "display_title": "访谈", **corrected})
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, result=result)
    app = FastAPI()
    app.include_router(local_agent.router)
    client = TestClient(app)
    try:
        listed = client.get(f"/agent/v1/tasks/{task_id}/subtitle-corrections", headers=AGENT).json()
        gom = next(r["id"] for r in listed["corrections"] if r["from"] == "GOM")

        answer = client.post(f"/agent/v1/tasks/{task_id}/subtitle-corrections/revert", headers=AGENT, json={"ids": [gom]}).json()

        assert answer["reverted"] == [gom]
        saved = job_store.get_job(task_id)["result"]
        assert saved["raw_segments"][4]["text"] == "应该是GOM跟KIMI"
        srt = _artifact_storage_dir() / task_id / saved["artifacts"]["transcript_srt"]["filename"]
        assert "应该是GOM跟KIMI" in srt.read_text(encoding="utf-8")
        assert "罗福莉" in srt.read_text(encoding="utf-8")
    finally:
        job_store.delete_jobs([task_id])


def test_the_mcp_tools_reach_these_routes(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import fluentflow_mcp_server as mcp

    calls: list[tuple] = []
    monkeypatch.setattr(mcp, "_agent_request", lambda method, path, **kw: calls.append((method, path, kw.get("payload"))) or {"ok": True})

    mcp.list_subtitle_corrections("t1")
    mcp.revert_subtitle_corrections("t1", ["a"])

    assert calls == [
        ("GET", "/agent/v1/tasks/t1/subtitle-corrections", None),
        ("POST", "/agent/v1/tasks/t1/subtitle-corrections/revert", {"ids": ["a"]}),
    ]
    names = {tool["name"] for tool in mcp.TOOL_DEFINITIONS}
    assert {"list_subtitle_corrections", "revert_subtitle_corrections"} <= names


# ── in the pipeline ───────────────────────────────────────────────────────

def test_the_records_move_onto_the_cut_clock_with_the_transcript():
    from backend.core import cut_timeline, survey_cut

    ranges = [cut_timeline.KeptRange(start=0.0, end=4.0, new_start=0.0), cut_timeline.KeptRange(start=6.0, end=16.0, new_start=4.0)]
    result = {
        "raw_segments": [dict(seg) for seg in SEGMENTS],
        "subtitle_corrections": [{"id": "x", "start": 8.0, "end": 10.0, "from": "GOM", "to": "GLM-5"}],
    }

    moved = survey_cut._onto_cut_clock(result, ranges)

    assert moved["subtitle_corrections"][0]["start"] == pytest.approx(6.0)
    assert moved["subtitle_corrections"][0]["source_start"] == 8.0


def test_the_pipeline_stage_skips_without_a_model_and_never_raises(video, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from backend.core import media_job

    monkeypatch.setattr(sc, "correct_from_subtitles", lambda v, s, chat, **_kw: sc.SubtitleCorrectionResult(
        status="skipped", segments=s, reason="no_model" if chat is None else "x"))
    ctx = SimpleNamespace(task_id_value="t", source_type="local", source_filename="a.mp4", event_route="/x",
                          secret_resolver=None, loop=None)

    async def run():
        ctx.loop = asyncio.get_running_loop()
        return await media_job._run_subtitle_correction_stage(ctx, video=video, segments=SEGMENTS, ai_kwargs=None)

    segments, fields = asyncio.run(run())

    assert segments == SEGMENTS
    assert fields["subtitle_correction"]["reason"] == "no_model"
    assert "subtitle_corrections" not in fields


def test_undoing_takes_out_the_change_where_it_was_made():
    # 「现在才做」 corrected to 「现在在做」 has two 在; the first is not the
    # one that changed (found on the Luo Fuli interview).
    segments = [{"start": 0.0, "end": 2.0, "text": "现在才做的保密"}]
    fixes = [{"index": 0, "from": "才", "to": "在", "at": 2, "subtitle": "现在在做的保密"}]
    fixed, records = sc.apply(segments, fixes)
    assert fixed[0]["text"] == "现在在做的保密"
    result = {"raw_segments": fixed, "display_segments": fixed, "subtitle_corrections": records}

    undone, _, _ = sc.revert(result, None)

    assert undone["raw_segments"][0]["text"] == "现在才做的保密"


def test_two_changes_in_one_sentence_are_both_undone():
    segments = [{"start": 0.0, "end": 2.0, "text": "也是cloud code加cloud ops"}]
    fixes = [
        {"index": 0, "from": "cloud code", "to": "Claude Code", "at": 2, "subtitle": "也是Claude Code加Claude Opus"},
        {"index": 0, "from": "cloud ops", "to": "Claude Opus", "at": 13, "subtitle": "也是Claude Code加Claude Opus"},
    ]
    fixed, records = sc.apply(segments, fixes)
    assert fixed[0]["text"] == "也是Claude Code加Claude Opus"

    undone, reverted, missing = sc.revert({"raw_segments": fixed, "subtitle_corrections": records}, None)

    assert undone["raw_segments"][0]["text"] == "也是cloud code加cloud ops"
    assert reverted == [r["id"] for r in records] and missing == []


def test_a_batch_whose_answer_cannot_be_read_is_asked_again_not_lost(video):
    # On the Luo Fuli interview one batch of a hundred came back unreadable
    # and every correction in it was lost.
    replies = iter(["抱歉，我先说明一下 {这里不是 JSON", "还是不行"])
    good = Model()

    def flaky(system, user):
        reply = next(replies, None)
        return reply if reply is not None else good(system, user)

    outcome = _run(video, flaky)

    assert "GLM-5" in [r["to"] for r in outcome.corrections]


def test_prose_around_the_answer_does_not_hide_it(video):
    def chatty(system, user):
        answer = Model()(system, user)
        return "看过了 {几处} 如下：\n```json\n" + answer + "\n```"

    assert "GLM-5" in [r["to"] for r in _run(video, chatty).corrections]


# ── review, 2026-10-25 ────────────────────────────────────────────────────

class Counting:
    """A model that answers with ``replies`` in turn (a string, or an
    exception to raise), then like ``Model``; counts its calls."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0
        self.model = Model()

    def __call__(self, system, user):
        self.calls += 1
        if self.replies:
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return self.model(system, user)


def test_a_model_that_fails_ends_the_step_without_asking_again(video):
    # Every call failing used to be asked twice and split, 4444 calls for
    # 1117 sentences, and the step still read as no changes.
    model = Counting(*[RuntimeError("usage limit reached")] * 50)

    outcome = _run(video, model)

    assert outcome.status == "failed" and "usage limit reached" in outcome.reason
    assert model.calls == 1
    assert outcome.segments == SEGMENTS


def test_an_unreadable_reply_is_asked_once_more_before_anything_else(video):
    model = Counting("这里没有 JSON")

    outcome = _run(video, model)

    assert model.calls == 2
    assert "GLM-5" in [r["to"] for r in outcome.corrections]


def test_a_reply_that_stays_unreadable_costs_a_bounded_number_of_calls(video):
    model = Counting(*["读不出"] * 50)

    outcome = _run(video, model)

    # once, once more, then each half once
    assert model.calls == 4
    assert outcome.corrections == [] and outcome.meta()["unreadable_batches"] == 1


def test_a_note_the_subtitle_author_added_in_brackets_is_not_copied_into_what_was_said(video):
    segments = [*SEGMENTS[:4], {"start": 8.0, "end": 10.0, "text": "这是一个10B的模型"}, *SEGMENTS[5:]]
    subtitles = [*SUBTITLES[:4], (8.0, 10.0, "这是一个10B的（激活）模型"), *SUBTITLES[5:]]
    model = Model(fixes=lambda items: [
        {"id": item["id"], "from": "的模型", "to": "的（激活）模型"} for item in items if "10B" in item["sentence"]
    ])

    outcome = _run(video, model, probe=_probe(subtitles)[0], track=_track(subtitles), segments=segments)

    assert outcome.segments[4]["text"] == "这是一个10B的模型"


def test_a_whole_sentence_cannot_be_rewritten_only_a_word_replaced(video):
    segments = [*SEGMENTS[:4], {"start": 8.0, "end": 10.0, "text": "因为他们用一个识别的模型做到了这一点"}, *SEGMENTS[5:]]
    subtitles = [*SUBTITLES[:4], (8.0, 10.0, "因为它们用一个10B的模型做到了这一点"), *SUBTITLES[5:]]
    whole = Model(fixes=lambda items: [
        {"id": item["id"], "from": item["sentence"], "to": "10B"} for item in items if "识别" in item["sentence"]
    ])
    word = Model(fixes=lambda items: [
        {"id": item["id"], "from": "识别", "to": "10B"} for item in items if "识别" in item["sentence"]
    ])
    kwargs = {"probe": _probe(subtitles)[0], "track": _track(subtitles), "segments": segments}

    assert _run(video, whole, **kwargs).segments[4]["text"] == segments[4]["text"]
    assert _run(video, word, **kwargs).segments[4]["text"] == "因为他们用一个10B的模型做到了这一点"


def test_a_word_said_twice_is_replaced_where_the_subtitle_reads_it_differently(video):
    segments = [*SEGMENTS[:4], {"start": 8.0, "end": 10.0, "text": "我们用MA他们也用MA"}, *SEGMENTS[5:]]
    subtitles = [*SUBTITLES[:4], (8.0, 10.0, "我们用MA他们也用MLA"), *SUBTITLES[5:]]
    model = Model(fixes=lambda items: [
        {"id": item["id"], "from": "MA", "to": "MLA"} for item in items if "他们也用" in item["sentence"]
    ])

    outcome = _run(video, model, probe=_probe(subtitles)[0], track=_track(subtitles), segments=segments)

    assert outcome.segments[4]["text"] == "我们用MA他们也用MLA"


def test_a_cancelled_task_stops_before_the_model_is_asked(video):
    import threading

    stop = threading.Event()
    seen = {}

    def track(video, band, workdir, stop_=None):
        seen["stop"] = stop_
        stop.set()  # the task is cancelled while the subtitle track is read
        return _track()(video, band, workdir)

    model = Model()
    outcome = _run(video, model, track=track, stop=stop)

    assert seen["stop"] is stop
    assert outcome.status == "cancelled" and model.asked == []
    assert outcome.segments == SEGMENTS


def test_undoing_reaches_every_copy_of_the_transcript(video):
    result = _corrected(video)
    gom = next(r for r in result["subtitle_corrections"] if r["from"] == "GOM")
    # The text-model correction's copy, and the copies on the original's
    # clock (here the cut moved everything 100 seconds earlier).
    result["corrected_segments"] = [dict(seg) for seg in result["raw_segments"]]
    result["corrected_transcript_text"] = sc.joined(result["corrected_segments"])
    result["source_raw_segments"] = [dict(seg, start=seg["start"] + 100, end=seg["end"] + 100) for seg in result["raw_segments"]]
    for record in result["subtitle_corrections"]:
        record["source_start"] = record["start"] + 100

    undone, reverted, missing = sc.revert(result, [gom["id"]])

    assert reverted == [gom["id"]] and missing == []
    for key in ("raw_segments", "display_segments", "corrected_segments", "source_raw_segments"):
        assert "应该是GOM跟KIMI" in [seg["text"] for seg in undone[key]], key
    assert "应该是GOM跟KIMI" in undone["corrected_transcript_text"]


def test_undoing_is_refused_while_the_task_is_still_being_worked_on(video, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", TOKEN)
    task_id = f"sc-{uuid.uuid4().hex[:8]}"
    corrected = _corrected(video)
    result = {"filename": "访谈.mp4", "display_title": "访谈", "summary_status": "pending", **corrected}
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, result=result)
    app = FastAPI()
    app.include_router(local_agent.router)
    try:
        answer = TestClient(app).post(f"/agent/v1/tasks/{task_id}/subtitle-corrections/revert", headers=AGENT, json={})

        assert answer.status_code == 409 and answer.json()["detail"]["code"] == "task_busy"
        assert job_store.get_job(task_id)["result"]["raw_segments"][4]["text"] == "应该是GLM-5跟KIMI"
    finally:
        job_store.delete_jobs([task_id])


def test_cancelling_the_task_stops_the_step_running_in_its_thread(video, monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace

    from backend.core import media_job

    started, stopped = threading.Event(), threading.Event()

    def runs_until_stopped(v, s, chat, stop=None, **_kw):
        started.set()
        if stop.wait(timeout=5):
            stopped.set()
        return sc.SubtitleCorrectionResult(status="cancelled", segments=s)

    monkeypatch.setattr(sc, "correct_from_subtitles", runs_until_stopped)
    ctx = SimpleNamespace(task_id_value="t", source_type="local", source_filename="a.mp4", event_route="/x",
                          secret_resolver=None, loop=None)

    async def run():
        ctx.loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(
            media_job._run_subtitle_correction_stage(ctx, video=video, segments=SEGMENTS, ai_kwargs=None))
        while not started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())

    assert stopped.wait(timeout=5)
