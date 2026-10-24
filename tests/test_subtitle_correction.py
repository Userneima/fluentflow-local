"""Fixing misheard words from the subtitles burned into the picture.

Written from the requirement (owner, 2026-10-24), not from the code:

- A video whose picture carries no subtitles is left alone, quickly, without
  asking a model anything. So is audio, and a machine without text recognition.
- With subtitles, a word the transcript misheard is replaced by the spelling the
  subtitle shows, in the transcript itself, with a record of what changed.
- A word is replaced only when a subtitle line read off the picture spells it
  out; recogniser garble and the chapter bar under the subtitle are not a
  source, and the model cannot write in a spelling the picture did not show.
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
from backend.core import job_store, subtitle_correction as sc
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.core.result_artifacts import _attach_result_artifacts
from backend.core.storage_paths import _artifact_storage_dir

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "今天我们的嘉宾是罗福利"},
    {"start": 2.0, "end": 4.0, "text": "他目前是小米大模型的负责人"},
    {"start": 4.0, "end": 6.0, "text": "访谈是在OpenCloud发布之后"},
    {"start": 6.0, "end": 8.0, "text": "我们更深入地聊了聊"},
    {"start": 8.0, "end": 10.0, "text": "应该是GOM跟KIMI"},
    {"start": 10.0, "end": 12.0, "text": "这些能力都是可以被学会的"},
    {"start": 12.0, "end": 14.0, "text": "环境反而比经验更重要"},
    {"start": 14.0, "end": 16.0, "text": "这是一个很难的问题"},
]

# What the picture shows at each moment, as the subtitle author wrote it, plus
# the chapter bar some videos run under it.
SUBTITLES = [
    (0.0, 2.0, "今天我们的嘉宾是罗福莉"),
    (2.0, 4.0, "她目前是小米大模型的负责人"),
    (4.0, 6.0, "访谈是在OpenClaw发布之后"),
    (6.0, 8.0, "我们更深入地聊了聊"),
    (8.0, 10.0, "训练的应该是GLM-5跟Kimi"),
    (10.0, 12.0, "这些能力都是可以被学会的"),
    (12.0, 14.0, "环境反而比经验更重要"),
    (14.0, 16.0, "这是一个很难的问题"),
]
CHAPTER_BAR = "1V2觉醒和伏击"


def _picture(subtitles=SUBTITLES, garble: dict[float, str] | None = None):
    reads: list[float] = []

    def read(video, at, workdir):
        reads.append(at)
        lines = [text for start, end, text in subtitles if start <= at < end]
        for start, text in (garble or {}).items():
            if start <= at < start + 2:
                lines = [text]
        return [*lines, CHAPTER_BAR]

    return read, reads


class Model:
    """Answers both questions the way a model would, and records what it was asked."""

    def __init__(self, suspects=None, fixes=None):
        self.suspects = suspects if suspects is not None else [
            {"i": 0, "text": "罗福利"}, {"i": 2, "text": "OpenCloud"}, {"i": 4, "text": "GOM"},
        ]
        self.fixes = fixes
        self.asked: list[dict] = []

    def __call__(self, system: str, user: str) -> str:
        if "可能听错的地方" in system and "suspects" in system:
            return json.dumps({"suspects": self.suspects}, ensure_ascii=False)
        items = json.loads(user)
        self.asked.extend(items)
        if self.fixes is not None:
            return json.dumps({"fixes": self.fixes(items)}, ensure_ascii=False)
        spelled = {"罗福利": "罗福莉", "OpenCloud": "OpenClaw", "GOM": "GLM-5"}
        return json.dumps({"fixes": [
            {"id": item["id"], "from": item["suspect"], "to": spelled[item["suspect"]]} for item in items
        ]}, ensure_ascii=False)


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "访谈.mp4"
    path.write_bytes(b"video")
    return path


def _run(video, model, read, **kwargs):
    kwargs.setdefault("video_check", lambda path: True)
    kwargs.setdefault("ocr_available", lambda: True)
    return sc.correct_from_subtitles(video, SEGMENTS, model, read=read, **kwargs)


# ── when the step does nothing ────────────────────────────────────────────

def test_a_picture_without_subtitles_is_skipped_without_asking_a_model(video):
    model = Model()
    read, reads = _picture(subtitles=[])

    outcome = _run(video, model, read)

    assert outcome.status == "skipped" and outcome.reason == "no_burned_subtitles"
    assert outcome.segments == SEGMENTS and outcome.corrections == []
    assert model.asked == [] and len(reads) <= sc.PROBE_SENTENCES


def test_audio_and_machines_without_text_recognition_are_skipped(video):
    read, reads = _picture()

    assert _run(video, Model(), read, video_check=lambda path: False).reason == "no_picture"
    assert _run(video, Model(), read, ocr_available=lambda: False).reason == "no_text_recognition"
    assert sc.correct_from_subtitles(None, SEGMENTS, Model(), read=read, ocr_available=lambda: True).reason == "no_picture"
    assert reads == []


def test_subtitles_but_no_model_leaves_the_transcript(video):
    read, _ = _picture()

    outcome = _run(video, None, read)

    assert outcome.status == "skipped" and outcome.reason == "no_model"
    assert outcome.segments == SEGMENTS


def test_nothing_here_fails_a_task(video):
    def broken(system, user):
        raise RuntimeError("model down")

    read, _ = _picture()
    assert _run(video, broken, read).segments == SEGMENTS

    def unreadable(video, at, workdir):
        raise OSError("disk gone")

    outcome = _run(video, Model(), unreadable)
    assert outcome.status == "failed" and outcome.segments == SEGMENTS


def test_it_can_be_turned_off(monkeypatch):
    assert sc.enabled()
    monkeypatch.setenv("FLUENTFLOW_SUBTITLE_CORRECTION", "off")
    assert not sc.enabled()


# ── what it changes ───────────────────────────────────────────────────────

def test_a_misheard_word_takes_the_subtitles_spelling_and_is_recorded(video):
    read, _ = _picture()

    outcome = _run(video, Model(), read)

    texts = [seg["text"] for seg in outcome.segments]
    assert texts[0] == "今天我们的嘉宾是罗福莉"
    assert texts[2] == "访谈是在OpenClaw发布之后"
    assert texts[4] == "应该是GLM-5跟KIMI"
    assert outcome.status == "completed" and len(outcome.corrections) == 3
    record = next(r for r in outcome.corrections if r["from"] == "GOM")
    assert record["to"] == "GLM-5" and record["start"] == 8.0
    assert record["subtitle"] == "训练的应该是GLM-5跟Kimi"
    assert record["sentence_before"] == "应该是GOM跟KIMI"


def test_only_the_doubtful_sentences_are_read(video):
    read, reads = _picture()
    outcome = _run(video, Model(suspects=[{"i": 4, "text": "GOM"}]), read)

    looked_up = reads[outcome.checked:]
    assert looked_up and all(6.0 <= at <= 12.0 for at in looked_up)


def test_the_model_cannot_write_in_a_spelling_the_picture_did_not_show(video):
    read, _ = _picture()
    model = Model(fixes=lambda items: [{"id": item["id"], "from": item["suspect"], "to": "GPT-5"} for item in items])

    outcome = _run(video, model, read)

    assert outcome.corrections == [] and outcome.segments == SEGMENTS


def test_the_model_cannot_replace_text_that_is_not_in_the_sentence(video):
    read, _ = _picture()
    model = Model(
        suspects=[{"i": 4, "text": "GOM"}],
        fixes=lambda items: [{"id": item["id"], "from": "训练的", "to": "GLM-5"} for item in items],
    )

    assert _run(video, model, read).corrections == []


def test_a_suspect_the_model_invented_is_not_looked_up(video):
    read, _ = _picture()
    model = Model(suspects=[{"i": 4, "text": "GPT"}, {"i": 99, "text": "GOM"}])

    outcome = _run(video, model, read)

    assert outcome.suspects == 0 and model.asked == []


def test_garble_and_the_chapter_bar_are_not_evidence(video):
    # Around 8-10s the recogniser reads the subtitle as noise (frame_text
    # recorded 如何监督 read as 如們血苔); the chapter bar is always there.
    read, _ = _picture(garble={6.5: "如們血苔蕾", 8.0: "如們血苔蕾", 10.0: "如們血苔蕾"})
    model = Model(suspects=[{"i": 4, "text": "GOM"}, {"i": 2, "text": "OpenCloud"}])

    outcome = _run(video, model, read)

    assert [r["to"] for r in outcome.corrections] == ["OpenClaw"]
    for item in model.asked:
        assert CHAPTER_BAR not in item["subtitles"] and "如們血苔蕾" not in item["subtitles"]


def test_subtitles_that_run_a_moment_behind_the_transcript_are_still_found(video):
    late = [(start + 1.5, end + 1.5, text) for start, end, text in SUBTITLES]
    read, _ = _picture(subtitles=late)

    outcome = _run(video, Model(suspects=[{"i": 4, "text": "GOM"}]), read)

    assert [r["to"] for r in outcome.corrections] == ["GLM-5"]


# ── undoing ───────────────────────────────────────────────────────────────

def _corrected(video) -> dict:
    read, _ = _picture()
    outcome = _run(video, Model(), read)
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

    assert len(reverted) == 2 and len(missing) == 1
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


def test_the_pipeline_stage_skips_without_a_model_and_never_raises(video, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from backend.core import media_job

    monkeypatch.setattr(sc, "correct_from_subtitles", lambda v, s, chat: sc.SubtitleCorrectionResult(
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


def test_a_letter_the_recogniser_swaps_does_not_block_the_right_spelling(video):
    # OpenAI was read off the Luo Fuli interview as OpenAl.
    subtitles = [*SUBTITLES[:4], (8.0, 10.0, "然后有OpenAl"), *SUBTITLES[5:]]
    segments = [*SEGMENTS[:4], {"start": 8.0, "end": 10.0, "text": "然后有Open Eye"}, *SEGMENTS[5:]]
    read, _ = _picture(subtitles=subtitles)
    model = Model(
        suspects=[{"i": 4, "text": "Open Eye"}],
        fixes=lambda items: [{"id": item["id"], "from": "Open Eye", "to": "OpenAI"} for item in items],
    )

    outcome = sc.correct_from_subtitles(video, segments, model, read=read, video_check=lambda p: True, ocr_available=lambda: True)

    assert outcome.segments[4]["text"] == "然后有OpenAI"


def test_undoing_takes_out_the_change_where_it_was_made():
    # 「现在才做」 corrected to 「现在在做」 has two 在; the first is not the
    # one that changed (found on the Luo Fuli interview).
    segments = [{"start": 0.0, "end": 2.0, "text": "现在才做的保密"}]
    fixes = [{"suspect": {"index": 0, "word": "才"}, "from": "才", "to": "在", "subtitle": "现在在做的保密"}]
    fixed, records = sc.apply(segments, fixes)
    result = {"raw_segments": fixed, "display_segments": fixed, "subtitle_corrections": records}

    undone, _, _ = sc.revert(result, None)

    assert undone["raw_segments"][0]["text"] == "现在才做的保密"


def test_two_changes_in_one_sentence_are_both_undone():
    segments = [{"start": 0.0, "end": 2.0, "text": "也是cloud code加cloud ops"}]
    fixes = [
        {"suspect": {"index": 0, "word": "cloud code"}, "from": "cloud code", "to": "Claude Code", "subtitle": "也是Claude Code加Claude Opus"},
        {"suspect": {"index": 0, "word": "cloud ops"}, "from": "cloud ops", "to": "Claude Opus", "subtitle": "也是Claude Code加Claude Opus"},
    ]
    fixed, records = sc.apply(segments, fixes)
    assert fixed[0]["text"] == "也是Claude Code加Claude Opus"

    undone, reverted, missing = sc.revert({"raw_segments": fixed, "subtitle_corrections": records}, None)

    assert undone["raw_segments"][0]["text"] == "也是cloud code加cloud ops"
    assert reverted == [r["id"] for r in records] and missing == []
