"""Correcting misheard words from the series' word list and the context.

Written from the requirement (owner, 2026-10-25), not from the code:

- The model reads the transcript with the series' word list and the sentences
  around each one. A misheard word takes the word list's spelling. Without a
  word list the step still runs, and changes only what the model can say why
  it is sure of.
- Only misheard words change, never a sentence: what is taken out is short,
  what goes in is about as long, no bracket the sentence did not have, and a
  word said twice is changed only where the model says which one.
- Every change is recorded (old, new, word list entry or context, which
  sentence) and can be undone, one or all; all undone, every copy of the
  transcript reads exactly as before. Undoing is refused while the task is
  still being worked on.
- The Claude subscription goes first when this machine opted into it, the
  configured key takes over when it fails; a model that raises ends the step
  without asking again; nothing here fails a task; cancelling reaches it.
- It runs after the subtitle check and before the note.
- An agent can run it again on a finished task; the subtitle files follow.
- Sentences the user edited in the editor are left alone.
"""

from __future__ import annotations

import inspect
import json
import sys
import threading
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routers.local_agent as local_agent
from backend.core import glossary_correction as gc, job_store, subtitle_correction as sc
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.core.result_artifacts import _attach_result_artifacts
from backend.core.storage_paths import _artifact_storage_dir


GLOSSARY = """# FluentFlow 校正文档：训练营

## 名称
- Claude Code：Anthropic 的编程 agent。转录常听成：Cloud Code、Cloud Cool
- OpenClaw：开源的个人 agent 框架（「龙虾」）。转录常听成：OpenCloud、open cloud
- DeerFlow：字节开源的 agent 框架。转录常听成：earflow、DL Flow
"""

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "我建议每个小组的同学"},
    {"start": 2.0, "end": 4.0, "text": "至少要把Cloud Cool和Codex"},
    {"start": 4.0, "end": 6.0, "text": "先拉出来深度研究"},
    {"start": 6.0, "end": 8.0, "text": "我要研究OpenCloud"},
    {"start": 8.0, "end": 10.0, "text": "字节的earflow也可以看"},
    {"start": 10.0, "end": 12.0, "text": "AI生视频都应该把AlienHunters学好"},
    {"start": 12.0, "end": 14.0, "text": "嗯这个一定要大家都这么干好吧"},
]

# What a model that knows the material would answer: the word list's words,
# and one it can only know from the context (Agent Harness, said elsewhere).
LIST_FIXES = {"Cloud Cool": "Claude Code", "OpenCloud": "OpenClaw", "earflow": "DeerFlow"}
CONTEXT_FIX = ("AlienHunters", "Agent Harness")


class Model:
    """Answers the way a model would, and records what it was shown."""

    def __init__(self, fixes=None, fail=False, replies=None):
        self.fixes = fixes
        self.fail = fail
        self.replies = list(replies or [])
        self.systems: list[str] = []
        self.asked: list[dict] = []

    def __call__(self, system: str, user: str) -> str:
        self.systems.append(system)
        payload = json.loads(user)
        self.asked.append(payload)
        if self.fail:
            raise RuntimeError("usage limit reached")
        if self.replies:
            return self.replies.pop(0)
        items = payload["sentences"]
        if self.fixes is not None:
            return json.dumps({"fixes": self.fixes(items)}, ensure_ascii=False)
        found = []
        for item in items:
            for wrong, right in LIST_FIXES.items():
                if wrong in item["text"] and right in system:
                    found.append({"id": item["id"], "from": wrong, "to": right, "basis": "glossary"})
            if CONTEXT_FIX[0] in item["text"]:
                found.append({"id": item["id"], "from": CONTEXT_FIX[0], "to": CONTEXT_FIX[1], "basis": "context",
                              "why": "讲师前面说的是 Agent Harness"})
        return json.dumps({"fixes": found}, ensure_ascii=False)


def _texts(segments):
    return [seg["text"] for seg in segments]


# ── what changes ──────────────────────────────────────────────────────────

def test_misheard_words_take_the_word_lists_spelling_and_are_recorded():
    model = Model()

    outcome = gc.correct(SEGMENTS, GLOSSARY, model)

    assert outcome.status == "completed"
    texts = _texts(outcome.segments)
    assert texts[1] == "至少要把Claude Code和Codex"
    assert texts[3] == "我要研究OpenClaw"
    assert texts[4] == "字节的DeerFlow也可以看"
    assert GLOSSARY.strip() in model.systems[0]
    by_from = {r["from"]: r for r in outcome.corrections}
    claude = by_from["Cloud Cool"]
    assert claude["to"] == "Claude Code" and claude["basis"] == "glossary"
    assert claude["entry"].startswith("- Claude Code：")
    assert claude["sentence_before"] == "至少要把Cloud Cool和Codex"
    assert claude["sentence_after"] == "至少要把Claude Code和Codex"
    assert claude["start"] == 2.0
    assert outcome.meta()["from_glossary"] == 3


def test_a_word_the_context_makes_certain_is_changed_and_says_why():
    outcome = gc.correct(SEGMENTS, GLOSSARY, Model())

    record = next(r for r in outcome.corrections if r["from"] == "AlienHunters")
    assert record["basis"] == "context" and record["why"]
    assert _texts(outcome.segments)[5] == "AI生视频都应该把Agent Harness学好"
    assert outcome.meta()["from_context"] == 1


def test_without_a_word_list_it_still_runs_and_changes_only_what_the_model_is_sure_of():
    def fixes(items):
        out = []
        for item in items:
            if "AlienHunters" in item["text"]:
                out.append({"id": item["id"], "from": "AlienHunters", "to": "Agent Harness", "basis": "context",
                            "why": "上下文说的是 Agent Harness"})
            if "OpenCloud" in item["text"]:
                # Claimed from a word list there is not, with no reason given.
                out.append({"id": item["id"], "from": "OpenCloud", "to": "OpenClaw", "basis": "glossary"})
        return out

    model = Model(fixes)
    outcome = gc.correct(SEGMENTS, "", model)

    assert "词表" in model.systems[0]
    assert [r["from"] for r in outcome.corrections] == ["AlienHunters"]
    assert _texts(outcome.segments)[3] == "我要研究OpenCloud"


def test_a_listed_name_inside_a_longer_word_is_not_that_entry():
    glossary = GLOSSARY + "- code：编程（「code 这种场景」）。转录常听成：科鲁\n"
    glossary += "- subagent：主 agent 派出去做子任务的 agent。转录常听成：subway 群\n"
    assert gc.entry_for("Codex", glossary) is None
    assert gc.entry_for("Agent", glossary) is None
    assert gc.entry_for("竞品调研", "- 黑客级竞品调研：训练营的名字。转录常听成：黑客级镜头调研\n").startswith("- 黑客级")
    assert gc.entry_for("Claude", glossary).startswith("- Claude Code：")


def test_a_spelling_the_word_list_does_not_have_counts_as_context_and_needs_a_reason():
    def fixes(items):
        return [{"id": item["id"], "from": "OpenCloud", "to": "Open Cloud", "basis": "glossary"}
                for item in items if "OpenCloud" in item["text"]]

    outcome = gc.correct(SEGMENTS, GLOSSARY, Model(fixes))

    assert outcome.corrections == []


def test_the_model_sees_the_sentences_around_each_batch(monkeypatch):
    monkeypatch.setattr(gc, "BATCH_CHARS", 20)
    model = Model(lambda items: [])

    gc.correct(SEGMENTS, GLOSSARY, model)

    second = model.asked[1]
    assert second["context_before"] and second["context_before"][-1] == _texts(SEGMENTS)[second["sentences"][0]["id"] - 1]
    assert all(item["id"] > 0 for item in second["sentences"])


# ── only words, never sentences ───────────────────────────────────────────

def _only(fix):
    return lambda items: [{**fix, "id": item["id"]} for item in items if fix["from"] in item["text"]]


def test_a_whole_sentence_cannot_be_rewritten():
    fix = {"from": "嗯这个一定要大家都这么干好吧", "to": "这个大家一定要这样做", "basis": "context", "why": "更通顺"}
    outcome = gc.correct(SEGMENTS, GLOSSARY, Model(_only(fix)))
    assert outcome.corrections == [] and outcome.segments == SEGMENTS


def test_fillers_and_wording_are_not_polished():
    fix = {"from": "嗯", "to": "", "basis": "context", "why": "去掉语气词"}
    outcome = gc.correct(SEGMENTS, GLOSSARY, Model(_only(fix)))
    assert outcome.corrections == []


def test_no_bracket_the_sentence_did_not_have():
    fix = {"from": "OpenCloud", "to": "OpenClaw（龙虾）", "basis": "glossary"}
    outcome = gc.correct(SEGMENTS, GLOSSARY, Model(_only(fix)))
    assert outcome.corrections == []


def test_text_that_is_not_in_the_sentence_cannot_be_replaced():
    def fixes(items):
        return [{"id": items[0]["id"], "from": "Cloud Cool", "to": "Claude Code", "basis": "glossary"}]

    outcome = gc.correct(SEGMENTS, GLOSSARY, Model(fixes))
    assert outcome.corrections == []


def test_case_alone_is_not_a_mishearing():
    fix = {"from": "Codex", "to": "codex", "basis": "context", "why": "大小写"}
    outcome = gc.correct(SEGMENTS, GLOSSARY, Model(_only(fix)))
    assert outcome.corrections == []


def test_a_word_said_twice_changes_only_where_the_model_says_which():
    segments = [{"start": 0.0, "end": 2.0, "text": "OpenCloud不是那个OpenCloud"}]
    unclear = gc.correct(segments, GLOSSARY, Model(_only({"from": "OpenCloud", "to": "OpenClaw", "basis": "glossary"})))
    named = gc.correct(segments, GLOSSARY, Model(_only({"from": "OpenCloud", "to": "OpenClaw", "basis": "glossary", "nth": 2})))

    assert unclear.corrections == []
    assert _texts(named.segments) == ["OpenCloud不是那个OpenClaw"]


# ── the model ─────────────────────────────────────────────────────────────

def test_a_model_that_fails_ends_the_step_without_asking_again(monkeypatch):
    monkeypatch.setattr(gc, "BATCH_CHARS", 20)
    model = Model(fail=True)

    outcome = gc.correct(SEGMENTS, GLOSSARY, model)

    assert outcome.status == "failed" and "usage limit" in outcome.reason
    assert outcome.segments == SEGMENTS
    assert len(model.asked) <= gc.MODEL_WORKERS


def test_an_unreadable_reply_is_asked_once_more():
    model = Model(replies=["好的，我看一下"])

    outcome = gc.correct(SEGMENTS, GLOSSARY, model)

    assert len(model.asked) == 2 and outcome.status == "completed"


def test_no_model_leaves_the_transcript():
    outcome = gc.correct(SEGMENTS, GLOSSARY, None)
    assert outcome.status == "skipped" and outcome.reason == "no_model" and outcome.segments == SEGMENTS


def test_a_cancelled_task_stops_before_the_model_is_asked():
    stop = threading.Event()
    stop.set()
    model = Model()

    outcome = gc.correct(SEGMENTS, GLOSSARY, model, stop=stop)

    assert outcome.status == "cancelled" and model.asked == []


def test_it_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_GLOSSARY_CORRECTION", "off")
    assert not gc.enabled()
    monkeypatch.delenv("FLUENTFLOW_GLOSSARY_CORRECTION")
    assert gc.enabled()


def test_the_subscription_goes_first_and_the_key_takes_over_when_it_fails(monkeypatch):
    from backend.core import ai_summarizer, claude_text

    key_calls: list[str] = []

    def keyed(system, user):
        key_calls.append(user)
        return '{"fixes": []}'

    def subscribed(system, user):
        raise RuntimeError("usage limit")

    monkeypatch.setattr(local_agent, "local_ai_kwargs", lambda: {"provider": "deepseek"})
    monkeypatch.setattr(ai_summarizer, "text_model_chat", lambda **kw: keyed)
    monkeypatch.setattr(claude_text, "subscription_chat", lambda runner=None: (subscribed, "subscription"))

    chat, channel = local_agent._glossary_chat()
    outcome = gc.correct(SEGMENTS, GLOSSARY, chat)

    assert channel == "subscription"
    assert outcome.status == "no_changes" and key_calls


# ── undoing ───────────────────────────────────────────────────────────────

def _corrected_result():
    outcome = gc.correct(SEGMENTS, GLOSSARY, Model())
    original = [dict(seg) for seg in SEGMENTS]
    result = {
        "raw_segments": [dict(seg) for seg in SEGMENTS],
        "display_segments": [dict(seg) for seg in SEGMENTS],
        "corrected_segments": [dict(seg) for seg in SEGMENTS],
        # The original's clock: a minute later, and one more sentence the cut took out.
        "source_raw_segments": [{"start": 50.0, "end": 52.0, "text": "剪掉的开场白"}]
        + [dict(seg, start=seg["start"] + 60, end=seg["end"] + 60) for seg in SEGMENTS],
        "transcript_text": sc.joined(SEGMENTS),
    }
    result["source_display_segments"] = [dict(seg) for seg in result["source_raw_segments"]]
    return gc.onto_result(result, outcome.segments, outcome.corrections), original


def test_every_copy_of_the_transcript_carries_the_changes():
    result, _original = _corrected_result()
    for key in ("raw_segments", "display_segments", "corrected_segments", "source_raw_segments", "source_display_segments"):
        assert "我要研究OpenClaw" in _texts(result[key]), key
    assert "我要研究OpenClaw" in result["transcript_text"]


def test_one_change_is_undone():
    result, _original = _corrected_result()
    target = next(r for r in result[gc.FIELD] if r["from"] == "OpenCloud")

    undone, reverted, missing = gc.revert(result, [target["id"]])

    assert reverted == [target["id"]] and missing == []
    assert "我要研究OpenCloud" in _texts(undone["raw_segments"])
    assert "至少要把Claude Code和Codex" in _texts(undone["raw_segments"])


def test_all_undone_every_copy_reads_exactly_as_before():
    result, original = _corrected_result()
    before_source = [{"start": 50.0, "end": 52.0, "text": "剪掉的开场白"}] + [
        dict(seg, start=seg["start"] + 60, end=seg["end"] + 60) for seg in original]

    undone, reverted, missing = gc.revert(result, None)

    assert len(reverted) == 4 and missing == []
    for key in ("raw_segments", "display_segments", "corrected_segments"):
        assert _texts(undone[key]) == _texts(original), key
    for key in ("source_raw_segments", "source_display_segments"):
        assert _texts(undone[key]) == _texts(before_source), key
    assert undone["transcript_text"] == sc.joined(original)


def test_a_sentence_edited_since_is_reported_not_undone():
    result, _original = _corrected_result()
    result["raw_segments"][3]["text"] = "我要研究别的东西"

    undone, reverted, missing = gc.revert(result, None)

    assert len(missing) == 1 and len(reverted) == 3
    assert "我要研究别的东西" in _texts(undone["raw_segments"])


def test_the_records_move_onto_the_cut_clock_with_the_transcript():
    from backend.core import cut_timeline, survey_cut

    ranges = [cut_timeline.KeptRange(start=0.0, end=4.0, new_start=0.0), cut_timeline.KeptRange(start=6.0, end=14.0, new_start=4.0)]
    result = {"raw_segments": [dict(seg) for seg in SEGMENTS],
              gc.FIELD: [{"id": "x", "start": 8.0, "end": 10.0, "from": "earflow", "to": "DeerFlow"}]}

    moved = survey_cut._onto_cut_clock(result, ranges)

    assert moved[gc.FIELD][0]["start"] == pytest.approx(6.0)
    assert moved[gc.FIELD][0]["source_start"] == 8.0


# ── the user's own edits ──────────────────────────────────────────────────

def test_sentences_the_user_edited_are_left_alone():
    result = gc.remember({"raw_segments": [dict(seg) for seg in SEGMENTS]})
    result["raw_segments"][3]["text"] = "我要研究OpenCloud这个东西"  # the user's own sentence now
    result["transcript_edited"] = True

    protected = gc.edited_by_user(result)
    outcome = gc.correct(result["raw_segments"], GLOSSARY, Model(), protected=protected)

    assert protected == {3}
    assert _texts(outcome.segments)[3] == "我要研究OpenCloud这个东西"
    assert _texts(outcome.segments)[1] == "至少要把Claude Code和Codex"


def test_older_tasks_fall_back_on_the_editors_records():
    result = {"raw_segments": [dict(seg) for seg in SEGMENTS], "transcript_edited": True,
              "transcript_edit_records": [{"index": 4, "before": "字节的earflow", "after": "字节的earflow也可以看"}]}
    assert gc.edited_by_user(result) == {4}
    assert gc.edited_by_user({"raw_segments": SEGMENTS}) == set()


def test_words_put_back_by_an_undo_are_not_taken_for_the_users():
    result, _original = _corrected_result()
    undone, _reverted, _missing = gc.revert(result, None)
    assert gc.edited_by_user(undone) == set()


# ── through the agent ─────────────────────────────────────────────────────

TOKEN = "glossary-token"
AGENT = {"x-fluentflow-access-token": TOKEN, "x-fluentflow-client-id": LOCAL_OWNER_ID}


@pytest.fixture
def agent(monkeypatch, tmp_path):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", TOKEN)
    folder = tmp_path / "训练营"
    folder.mkdir()
    (folder / "FluentFlow 校正文档.md").write_text(GLOSSARY, encoding="utf-8")
    model = Model()
    monkeypatch.setattr(local_agent, "_glossary_chat", lambda: (model, "subscription"))
    app = FastAPI()
    app.include_router(local_agent.router)
    created: list[str] = []

    def make(status="completed", **extra):
        task_id = f"gc-{uuid.uuid4().hex[:8]}"
        result = _attach_result_artifacts(task_id, {
            "filename": "课.mp4", "display_title": "课",
            "raw_segments": [dict(seg) for seg in SEGMENTS], "display_segments": [dict(seg) for seg in SEGMENTS],
            "transcript_text": sc.joined(SEGMENTS), **extra,
        })
        job_store.upsert_job(task_id=task_id, status=status, client_id=LOCAL_OWNER_ID, result=result,
                             metadata={"folder_intake": {"original_path": str(folder / "课.mp4")}})
        created.append(task_id)
        return task_id

    yield TestClient(app), make, model
    job_store.delete_jobs(created)


def _srt(task_id):
    saved = job_store.get_job(task_id)["result"]
    return (_artifact_storage_dir() / task_id / saved["artifacts"]["transcript_srt"]["filename"]).read_text(encoding="utf-8")


def test_an_agent_runs_it_on_a_finished_task_and_the_subtitle_files_follow(agent):
    client, make, model = agent
    task_id = make()

    answer = client.post(f"/agent/v1/tasks/{task_id}/glossary-corrections/run", headers=AGENT).json()

    assert answer["glossary_correction"]["applied"] == 4 and answer["glossary_correction"]["channel"] == "subscription"
    assert GLOSSARY.strip() in model.systems[0]
    saved = job_store.get_job(task_id)["result"]
    assert "我要研究OpenClaw" in saved["transcript_text"]
    assert "我要研究OpenClaw" in _srt(task_id)
    listed = client.get(f"/agent/v1/tasks/{task_id}/glossary-corrections", headers=AGENT).json()
    assert {r["sentence"] for r in listed["corrections"]} == {2, 4, 5, 6}

    undone = client.post(f"/agent/v1/tasks/{task_id}/glossary-corrections/revert", headers=AGENT, json={}).json()

    assert len(undone["reverted"]) == 4
    assert job_store.get_job(task_id)["result"]["transcript_text"] == sc.joined(SEGMENTS)
    assert "我要研究OpenCloud" in _srt(task_id)


def test_running_it_again_leaves_the_users_edits(agent):
    client, make, _model = agent
    edited = [dict(seg) for seg in SEGMENTS]
    task_id = make(transcript_fingerprints=gc.fingerprints(SEGMENTS))
    edited[3]["text"] = "我要研究OpenCloud这个东西"
    job = job_store.get_job(task_id)
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID,
                         result={**job["result"], "raw_segments": edited, "transcript_text": sc.joined(edited)})

    answer = client.post(f"/agent/v1/tasks/{task_id}/glossary-corrections/run", headers=AGENT).json()

    assert answer["glossary_correction"]["left_as_edited"] == 1
    assert "我要研究OpenCloud这个东西" in job_store.get_job(task_id)["result"]["transcript_text"]


def test_running_and_undoing_are_refused_while_the_task_is_being_worked_on(agent):
    client, make, model = agent
    task_id = make(summary_status="pending")

    ran = client.post(f"/agent/v1/tasks/{task_id}/glossary-corrections/run", headers=AGENT)
    undone = client.post(f"/agent/v1/tasks/{task_id}/glossary-corrections/revert", headers=AGENT, json={})

    assert ran.status_code == 409 and ran.json()["detail"]["code"] == "task_busy"
    assert undone.status_code == 409
    assert model.asked == []


def test_an_edit_made_while_it_runs_is_not_overwritten(agent, monkeypatch):
    client, make, _model = agent
    task_id = make()
    real = gc.correct

    def edits_meanwhile(*args, **kwargs):
        outcome = real(*args, **kwargs)
        job = job_store.get_job(task_id)
        job_store.save_result_changes(task_id, job["result"], {**job["result"], "transcript_text": "用户刚改的"})
        return outcome

    monkeypatch.setattr(gc, "correct", edits_meanwhile)

    answer = client.post(f"/agent/v1/tasks/{task_id}/glossary-corrections/run", headers=AGENT)

    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "transcript_moved"
    assert job_store.get_job(task_id)["result"]["transcript_text"] == "用户刚改的"


def test_the_mcp_tools_reach_these_routes(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import fluentflow_mcp_server as mcp

    calls: list[tuple] = []
    monkeypatch.setattr(mcp, "_agent_request", lambda method, path, **kw: calls.append((method, path, kw.get("payload"))) or {"ok": True})

    mcp.list_glossary_corrections("t1")
    mcp.run_glossary_correction("t1")
    mcp.revert_glossary_corrections("t1", ["a"])

    assert calls == [
        ("GET", "/agent/v1/tasks/t1/glossary-corrections", None),
        ("POST", "/agent/v1/tasks/t1/glossary-corrections/run", {}),
        ("POST", "/agent/v1/tasks/t1/glossary-corrections/revert", {"ids": ["a"]}),
    ]
    names = {tool["name"] for tool in mcp.TOOL_DEFINITIONS}
    assert {"list_glossary_corrections", "run_glossary_correction", "revert_glossary_corrections"} <= names


# ── in the pipeline ───────────────────────────────────────────────────────

def test_the_pipeline_stage_never_raises_and_finds_the_word_list(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from backend.core import correction_notes, media_job

    monkeypatch.setattr(correction_notes, "for_task", lambda task_id, client_id=None: GLOSSARY)
    ctx = SimpleNamespace(task_id_value="t", client_id=None, source_type="local", source_filename="a.mp4",
                          event_route="/x", secret_resolver=None, loop=None)

    async def run():
        ctx.loop = asyncio.get_running_loop()
        return await media_job._run_glossary_correction_stage(ctx, segments=SEGMENTS, ai_kwargs=None)

    segments, fields = asyncio.run(run())

    assert segments == SEGMENTS
    assert fields[gc.META]["reason"] == "no_model" and fields[gc.META]["glossary_chars"] == len(GLOSSARY.strip())
    assert gc.FIELD not in fields


def test_cancelling_the_task_stops_the_step_running_in_its_thread(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from backend.core import correction_notes, media_job

    started, stopped = threading.Event(), threading.Event()

    def runs_until_stopped(segments, glossary, chat, stop=None, **_kw):
        started.set()
        if stop.wait(timeout=5):
            stopped.set()
        return gc.GlossaryCorrectionResult(status="cancelled", segments=segments)

    monkeypatch.setattr(gc, "correct", runs_until_stopped)
    monkeypatch.setattr(correction_notes, "for_task", lambda task_id, client_id=None: "")
    ctx = SimpleNamespace(task_id_value="t", client_id=None, source_type="local", source_filename="a.mp4",
                          event_route="/x", secret_resolver=None, loop=None)

    async def run():
        ctx.loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(media_job._run_glossary_correction_stage(ctx, segments=SEGMENTS, ai_kwargs=None))
        while not started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())

    assert stopped.wait(timeout=5)


def test_it_runs_after_the_subtitle_check_and_before_the_note():
    from backend.core import media_job

    source = inspect.getsource(media_job._stream_media_job)
    subtitle = source.index("_run_subtitle_correction_stage(")
    glossary = source.index("_run_glossary_correction_stage(")
    stored = source.index('"raw_segments": segments_payload')
    note = source.index("note_input_text =")
    assert subtitle < glossary < stored < note
