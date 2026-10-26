"""Every model call is recorded against the task and step it worked for.

Requirements (owner, 2026-10-25 plan, docs/方案/2026-10-25-任务步骤清单和模型用量记账.md;
2026-10-26 go-ahead), in the user's terms:

- Each call to a model is one record: which task, which step (and which part
  of it), which provider and model, paid by key or by subscription, the
  prompt's tokens and how many of them came from the cache, the answer's
  tokens, how long it took, whether it worked.
- A whole task run through its steps leaves one record per call, every one
  of them in a step; none is "not in a step".
- A call that cannot be put in a step is still recorded, as not in a step.
- Money is worked out when shown, from prices read on the provider's own page;
  DeepSeek's peak and off-peak prices go by when the call was made. A model
  without a price shows tokens and no money. A subscription call costs nothing
  per call; the command line's own dollar figure is shown as the API price of
  the same tokens, never as money spent.
- The processing record and an agent's task answers carry each step's calls,
  tokens and money, and the task's total.
- A record that cannot be written never fails the call or the task.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import ai_client, ai_summarizer, claude_code_note as ccn, claude_text, claude_vision
from backend.core import cut_judge, job_store, media_job, model_usage, note_coverage, tail_trim, task_steps
from backend.core import task_flow as tf, task_queue as tq, task_submission as ts  # noqa: F401
from backend.core.local_task_detail import build_task_detail
import backend.routers.local_agent as local_agent

_counter = iter(range(10_000))


@pytest.fixture()
def task_id():
    return f"usage-{next(_counter)}"


def _rows(task_id):
    return job_store.list_model_calls(task_id)


# ── the fakes: the providers at the far side of each exit ───────────────────

class FakeDeepSeek:
    """Stands in for DeepSeek's OpenAI-compatible endpoint. It answers each
    FluentFlow request in the shape that request asks for, and reports usage
    the way DeepSeek does (the prompt counts its cache hits)."""

    def __init__(self, seen, *, api_key=None, base_url=None, **_kwargs):
        self.base_url = base_url or "https://api.deepseek.com"
        self.seen = seen
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    @staticmethod
    def answer(system: str) -> str:
        if system.startswith("你会收到一段语音识别转录稿"):
            return json.dumps({"fixes": []})
        if system == tail_trim.SYSTEM:
            return json.dumps({"ends_at": None, "reason": "讲完就结束了"}, ensure_ascii=False)
        if "查漏" in system:
            return json.dumps({"points": [{"point": "开场", "in_note": True, "source": "大家好"}]}, ensure_ascii=False)
        return "好的"

    def create(self, *, model, messages, **_kwargs):
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        self.seen.append(("deepseek", model))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.answer(system)))],
            usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=300, prompt_cache_hit_tokens=1000),
        )


def _deepseek(monkeypatch, seen):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("AI_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.setattr(ai_client, "OpenAI", lambda **kwargs: FakeDeepSeek(seen, **kwargs))


def _cli_runner(seen):
    """Stands in for the ``claude`` command line: its result event, usage and
    all, answering each request as the provider above would."""

    def run(command, **_kwargs):
        seen.append(("subscription", command[command.index("--model") + 1]))
        system = command[command.index("--system-prompt") + 1] if "--system-prompt" in command else ""
        event = {
            "type": "result",
            "result": FakeDeepSeek.answer(system),
            "usage": {"input_tokens": 50, "cache_read_input_tokens": 4000, "cache_creation_input_tokens": 900,
                      "output_tokens": 700},
            "modelUsage": {"claude-opus-5-5": {"outputTokens": 700}},
            "total_cost_usd": 0.42,
        }
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(event, ensure_ascii=False), stderr="")

    return run


# ── a whole task, its step bodies doing what they do, the models faked ──────

TRANSCRIPT = "大家好，今天讲竞品调研。\n" * 40
SEGMENTS = [{"start": float(i), "end": float(i) + 0.9, "text": f"第{i}句，今天讲竞品调研。"} for i in range(6)]


class WholeTask:
    """The real flow (``task_flow``) with each step's body doing the model
    work it does, through the real exits and the real thread hand-overs:
    the corrections in the pipeline's worker thread and the glossary's pool,
    the cut's judgement on the cut's worker thread and the deadline thread,
    the note on the note's thread, its check in the coverage pool."""

    def __init__(self, task_id, seen, *, side_by_side):
        self.task_id = task_id
        self.seen = seen
        self.side_by_side = side_by_side

    async def pipeline(self, ctx):
        ctx.on_transcribed({"segments": SEGMENTS})
        ctx.loop = asyncio.get_running_loop()
        await media_job._run_glossary_correction_stage(ctx, segments=SEGMENTS, ai_kwargs={})
        job_store.upsert_job(task_id=self.task_id, status="completed", stage="done", progress=100,
                             result={"task_id": self.task_id, "transcript_text": TRANSCRIPT})

    def survey_and_cut(self, task_id, client_id, on_plan=None):
        chat = ai_summarizer.text_model_chat(provider="deepseek")
        tail_trim._ask(chat, "录像总长 600 秒。\n[590] 好，今天就到这里。")
        if self.side_by_side and on_plan is not None:
            on_plan([SimpleNamespace(start=0.0, end=600.0)])
        _patch(task_id, debreath={"status": "completed"})

    def write_note(self, task_id, client_id, *, on_local_work_done=None, planned_ranges=None):
        if on_local_work_done:
            on_local_work_done()
        write = claude_text._subscription_chat("opus", _cli_runner(self.seen))
        note = write("按技能写笔记", TRANSCRIPT)
        check = ai_summarizer.text_model_chat(provider="deepseek")
        note_coverage.check_against_transcript(check, TRANSCRIPT, note, chunk_chars=400)
        _patch(task_id, summary_markdown="# 笔记\n\n" + note, summary_status="completed")


def _patch(task_id, **changes):
    job = job_store.get_job(task_id) or {}
    result = dict(job.get("result") or {})
    result.update(changes)
    job_store.update_job_result(task_id, result)


def _run_whole_task(monkeypatch, task_id, *, side_by_side=False):
    seen: list[tuple[str, str]] = []
    _deepseek(monkeypatch, seen)
    # The owner's setup: the subscription first for the errands that take it.
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "subscription")
    monkeypatch.setattr(ccn, "cli_path", lambda: "/usr/bin/true")
    monkeypatch.setattr(ccn, "unavailable_reason", lambda: None)
    monkeypatch.setattr(ccn, "_run_tracked", _cli_runner(seen))
    flow = WholeTask(task_id, seen, side_by_side=side_by_side)
    monkeypatch.setattr(tf, "_run_pipeline", flow.pipeline)
    monkeypatch.setattr(tf, "_agent_judges_cut", _no)
    monkeypatch.setattr(tf.local_intake_flow, "survey_first_enabled", lambda: True)
    monkeypatch.setattr(tf.local_intake_flow, "note_is_wanted", lambda *_a: True)
    monkeypatch.setattr(tf.local_intake_flow, "mark_note_running", lambda *_a: None)
    monkeypatch.setattr(tf.local_intake_flow, "write_note", flow.write_note)
    monkeypatch.setattr(tf.local_intake_flow, "compress_original", lambda *_a: None)
    monkeypatch.setattr(tf.survey_cut, "survey_and_cut", flow.survey_and_cut)
    monkeypatch.setattr(tf.visual_note_channel, "resolve_channel",
                        lambda *_a, **_k: SimpleNamespace(available=side_by_side))
    monkeypatch.setattr(tf.visual_note_job, "put_back_onto_original", lambda *_a, **_k: False)
    monkeypatch.setattr(tf, "_deliver_to_output_folder", _no)
    monkeypatch.setattr(media_job.correction_notes, "for_task", lambda *_a: "竞品调研")
    job_store.upsert_job(task_id=task_id, status="queued", stage="queued", progress=0)
    ctx = SimpleNamespace(
        task_id_value=task_id, client_id=None, do_lark=False, note_deferred_to_visual_note=True,
        secret_resolver=lambda value, _name: value, source_type="video", source_filename="kickoff.mp4",
        event_route="/test",
    )
    asyncio.run(asyncio.wait_for(tf.run_pipeline_then_note(asyncio.Event(), ctx), 20))
    return seen


async def _no(*_args, **_kwargs):
    return False


def _problems(task_id, seen) -> list[str]:
    """What the processing record gets wrong about the calls the providers saw."""
    rows = _rows(task_id)
    found = []
    if len(rows) != len(seen):
        found.append(f"the providers answered {len(seen)} calls, {len(rows)} were recorded")
    loose = [row for row in rows if not row["step"]]
    if loose:
        found.append(f"{len(loose)} recorded calls are not in a step: {[(r['provider'], r['item']) for r in loose]}")
    return found


@pytest.mark.parametrize("side_by_side", [False, True], ids=["cut-then-note", "cut-and-note-together"])
def test_a_whole_task_records_every_call_and_puts_each_in_its_step(monkeypatch, task_id, side_by_side):
    seen = _run_whole_task(monkeypatch, task_id, side_by_side=side_by_side)

    assert seen, "the task called no model; the scenario proves nothing"
    assert _problems(task_id, seen) == []
    usage = model_usage.summarize(task_id)
    by_step = {step["step"]: step for step in usage["steps"]}
    assert set(by_step) == {"correct", "cut", "note"}
    assert [item["item"] for item in by_step["correct"]["items"]] == ["词表纠错"]
    assert [item["item"] for item in by_step["cut"]["items"]] == ["判断结尾"]
    assert {item["item"] for item in by_step["note"]["items"]} == {"查漏"}
    assert by_step["note"]["subscription_calls"] == 1
    assert usage["total"]["calls"] == len(seen)
    assert task_steps.status_of(task_id, task_steps.NOTE) == task_steps.DONE


def test_an_exit_that_stops_recording_is_caught_by_the_whole_task_check(monkeypatch, task_id):
    """The check above is not vacuous: with the text-model exit sending its
    requests without recording them, it fails."""

    def unrecorded(client, provider, model, **kwargs):
        return client.chat.completions.create(model=model, **kwargs)

    monkeypatch.setattr(ai_client, "_send", unrecorded)
    seen = _run_whole_task(monkeypatch, task_id)

    problems = _problems(task_id, seen)
    assert problems and "were recorded" in problems[0]


def test_a_thread_started_without_carrying_the_step_records_its_call_outside_any_step(monkeypatch, task_id):
    seen: list = []
    _deepseek(monkeypatch, seen)
    chat = ai_summarizer.text_model_chat(provider="deepseek")
    loose_before = len([row for row in job_store.list_model_calls() if row["task_id"] is None])
    with model_usage.scope(task_id, task_steps.NOTE):
        bare = threading.Thread(target=lambda: chat("系统", "你好"))
        bare.start()
        bare.join()
        carried = threading.Thread(target=model_usage.carry(lambda: chat("系统", "你好")))
        carried.start()
        carried.join()

    lost = [row for row in job_store.list_model_calls() if row["task_id"] is None and row["step"] is None]
    assert len(lost) == loose_before + 1, "the call made on the bare thread is kept, as not in a step"
    assert [row["step"] for row in _rows(task_id)] == ["note"]
    unattributed = model_usage.summarize("", rows=lost)["steps"][0]
    assert unattributed["label"] == "没归到步骤"


# ── each exit records one row, answered or not ─────────────────────────────

def test_the_text_model_exit_records_tokens_and_the_cache(monkeypatch, task_id):
    seen: list = []
    _deepseek(monkeypatch, seen)
    with model_usage.scope(task_id, task_steps.CORRECT, "翻译"):
        reply = ai_summarizer.text_model_chat(provider="deepseek")("系统", "你好")

    assert reply == "好的"
    [row] = _rows(task_id)
    assert (row["provider"], row["model"], row["billing"]) == ("deepseek", "deepseek-flash", "key")
    assert (row["input_tokens"], row["cached_input_tokens"], row["output_tokens"]) == (1200, 1000, 300)
    assert (row["step"], row["item"], row["ok"]) == ("correct", "翻译", True)
    assert row["duration_ms"] is not None


def test_a_call_that_fails_is_recorded_as_failed_and_still_raises(monkeypatch, task_id):
    class Refusing(FakeDeepSeek):
        def create(self, **_kwargs):
            raise RuntimeError("Insufficient Balance")

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(ai_client, "OpenAI", lambda **kwargs: Refusing([], **kwargs))
    with model_usage.scope(task_id, task_steps.NOTE), pytest.raises(RuntimeError):
        ai_summarizer.text_model_chat(provider="deepseek", model="deepseek-flash")("系统", "你好")

    [row] = _rows(task_id)
    assert row["ok"] is False and "Insufficient Balance" in row["error"]
    assert row["input_tokens"] is None, "nothing reported is not recorded as zero"
    step = model_usage.summarize(task_id)["steps"][0]
    assert (step["failed_calls"], step["failure"]) == (1, "账户余额不足")
    assert step["unpriced_calls"] == 0, "a refused call cost nothing to price"


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get_final_message(self):
        return self.message


def _claude_message(text="好的"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=100, cache_read_input_tokens=2000, cache_creation_input_tokens=300,
                              output_tokens=50),
    )


class FakeAnthropic:
    def __init__(self, text="好的", **_kwargs):
        self.messages = SimpleNamespace(stream=lambda **_request: FakeStream(_claude_message(text)))


def test_the_anthropic_key_exits_record_the_prompt_with_its_cache(monkeypatch, task_id):
    monkeypatch.setattr(claude_vision.anthropic, "Anthropic", FakeAnthropic)
    with model_usage.scope(task_id, task_steps.CORRECT):
        claude_text._api_key_chat("sk-test", "claude-opus-5-5")("系统", "你好")
    with model_usage.scope(task_id, task_steps.CUT):
        cut_judge._api_key("sk-test")("系统", "你好", [])

    rows = _rows(task_id)
    assert [(r["step"], r["provider"], r["billing"]) for r in rows] == [
        ("correct", "anthropic", "key"), ("cut", "anthropic", "key"),
    ]
    # Anthropic counts cached tokens apart from the input; the record keeps one prompt.
    assert (rows[0]["input_tokens"], rows[0]["cached_input_tokens"], rows[0]["cache_write_tokens"]) == (2400, 2000, 300)


def test_the_key_note_exit_records_its_call(task_id):
    note = json.dumps({"note_markdown": "# 笔记", "basis_note": ""}, ensure_ascii=False)
    with model_usage.scope(task_id, task_steps.NOTE):
        claude_vision.write_visual_note("大家好", [], api_key="sk-test", model="claude-opus-5-5",
                                        client=FakeAnthropic(note))

    [row] = _rows(task_id)
    assert (row["model"], row["output_tokens"], row["step"]) == ("claude-opus-5-5", 50, "note")


def test_the_picture_model_the_cut_judge_calls_directly_is_recorded(monkeypatch, task_id):
    import openai

    seen: list = []
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: FakeDeepSeek(seen, **kwargs))
    with model_usage.scope(task_id, task_steps.CUT):
        cut_judge._openai_compatible("https://api.moonshot.cn/v1", "sk-test", "kimi-k2.6")("系统", "你好", [])
        cut_judge._openai_compatible("https://relay.example.com/v1", "sk-test", "mimo-v2.6-flash")("系统", "你好", [])

    known, relay = _rows(task_id)
    assert (known["provider"], known["model"], known["input_tokens"]) == ("kimi", "kimi-k2.6", 1200)
    assert relay["provider"] == "relay.example.com", "a relay is named by its address"


def test_the_subscription_exit_records_tokens_and_the_cli_figure_as_api_price(task_id):
    with model_usage.scope(task_id, task_steps.NOTE):
        ccn._run_cli(["claude", "-p", "--model", "opus"], "你好", _cli_runner([]))

    [row] = _rows(task_id)
    assert (row["billing"], row["model"]) == ("subscription", "claude-opus-5-5")
    assert row["reported_cost_usd"] == pytest.approx(0.42)
    total = model_usage.summarize(task_id)["total"]
    assert total["cost"] == {}, "a subscription call is not money spent"
    assert total["reported_cost_usd"] == pytest.approx(0.42)
    assert total["subscription_calls"] == 1


def test_a_record_that_cannot_be_written_leaves_the_call_alone(monkeypatch, task_id):
    seen: list = []
    _deepseek(monkeypatch, seen)

    def broken(**_values):
        raise OSError("disk full")

    monkeypatch.setattr(job_store, "insert_model_call", broken)
    with model_usage.scope(task_id, task_steps.NOTE):
        assert ai_summarizer.text_model_chat(provider="deepseek")("系统", "你好") == "好的"


# ── money ───────────────────────────────────────────────────────────────────

def _row(**values):
    return {"provider": "deepseek", "model": "deepseek-flash", "billing": "key", "input_tokens": 1_000_000,
            "cached_input_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0, "ok": True, **values}


def _at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).isoformat()


def test_deepseek_is_priced_in_yuan_at_peak_on_weekday_peak_hours_and_off_peak_otherwise():
    # 2026-10-26 is a Monday; peak is 09:00-12:00 and 14:00-18:00 Beijing time on weekdays.
    assert model_usage.cost(_row(created_at=_at(2026, 10, 26, 2, 30))) == ("CNY", pytest.approx(2.0))
    assert model_usage.cost(_row(created_at=_at(2026, 10, 26, 5))) == ("CNY", pytest.approx(1.0))
    assert model_usage.cost(_row(created_at=_at(2026, 10, 26, 10))) == ("CNY", pytest.approx(1.0))
    assert model_usage.cost(_row(created_at=_at(2026, 10, 31, 2))) == ("CNY", pytest.approx(1.0)), "Saturday"


def test_cache_hits_and_answers_have_their_own_prices():
    row = _row(input_tokens=1_000_000, cached_input_tokens=1_000_000, output_tokens=1_000_000,
               created_at=_at(2026, 10, 26, 5))
    assert model_usage.cost(row) == ("CNY", pytest.approx(0.02 + 4))
    claude = _row(provider="anthropic", model="claude-opus-5-5", input_tokens=3_000_000,
                  cached_input_tokens=1_000_000, cache_write_tokens=1_000_000, output_tokens=1_000_000,
                  created_at=_at(2026, 10, 26, 5))
    assert model_usage.cost(claude) == ("USD", pytest.approx(4 + 0.2 + 5 + 20))


def test_money_in_two_currencies_is_kept_apart(task_id):
    rows = [
        _row(created_at=_at(2026, 10, 26, 5), step="note"),
        _row(provider="anthropic", model="claude-opus-5-5", input_tokens=1_000_000, created_at=_at(2026, 10, 26, 5),
             step="note"),
    ]
    assert model_usage.summarize(task_id, rows=rows)["total"]["cost"] == {
        "CNY": pytest.approx(1.0), "USD": pytest.approx(4.0),
    }


def test_a_model_without_a_price_shows_tokens_and_no_money(task_id):
    rows = [
        _row(model="deepseek-flash", created_at=_at(2026, 10, 26, 5), step="note"),
        _row(provider="kimi", model="kimi-k2.6", created_at=_at(2026, 10, 26, 5), step="note"),
    ]
    assert model_usage.cost(rows[1]) is None
    assert model_usage.cost(_row(created_at=_at(2026, 10, 1, 5))) is None, "before the price was read"
    total = model_usage.summarize(task_id, rows=rows)["total"]
    assert total["cost"] == {"CNY": pytest.approx(1.0)}
    assert total["unpriced_calls"] == 1
    assert total["input_tokens"] == 2_000_000


def test_the_summary_lists_steps_in_the_tasks_order_with_the_loose_calls_last(task_id):
    rows = [
        _row(step="note", created_at=_at(2026, 10, 26, 5)),
        _row(step=None, created_at=_at(2026, 10, 26, 5)),
        _row(step="correct", item="词表纠错", created_at=_at(2026, 10, 26, 5)),
    ]
    steps = model_usage.summarize(task_id, rows=rows)["steps"]
    assert [(s["step"], s["label"]) for s in steps] == [
        ("correct", "修正转录稿"), ("note", "写笔记"), ("unattributed", "没归到步骤"),
    ]


def test_the_price_table_names_its_source_and_the_day_each_price_was_read():
    for provider in model_usage.prices()["providers"].values():
        assert provider["source"].startswith("https://")
        assert provider["checked"]
        assert provider["currency"] in {"CNY", "USD"}
        assert all(entry["from"] for entry in provider["prices"])


# ── where it is shown ──────────────────────────────────────────────────────

def test_the_processing_record_and_the_agents_task_answers_carry_the_usage(monkeypatch, task_id):
    job_store.upsert_job(task_id=task_id, status="completed", client_id="agent", result={"task_id": task_id})
    job_store.insert_model_call(task_id=task_id, step="note", provider="deepseek", model="deepseek-flash",
                                billing="key", input_tokens=10, output_tokens=5, ok=True)
    detail = build_task_detail(job_store.get_job(task_id))
    assert detail["model_usage"]["total"]["calls"] == 1

    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "usage-token")
    monkeypatch.setattr(local_agent, "_local_client_scope", lambda _request: "agent")
    app = FastAPI()
    app.include_router(local_agent.router)
    client = TestClient(app)
    headers = {"x-fluentflow-access-token": "usage-token"}
    task = client.get(f"/agent/v1/tasks/{task_id}", headers=headers).json()["task"]
    assert task["model_usage"]["steps"][0]["label"] == "写笔记"
    package = client.get(f"/agent/v1/tasks/{task_id}/package", headers=headers).json()
    assert package["model_usage"]["total"]["output_tokens"] == 5


def test_no_model_is_called_outside_the_exits_that_record_it():
    """A new place that calls a model directly would spend money nobody sees.
    Every request to a provider in the backend sits inside ``model_usage.call``."""
    from pathlib import Path
    import re

    root = Path(__file__).resolve().parents[1] / "backend"
    request = re.compile(r"\.chat\.completions\.create\(|\.messages\.(?:create|stream)\(|/v1/messages")
    unrecorded = []
    for path in root.rglob("*.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines):
            if not request.search(line) or line.lstrip().startswith("#"):
                continue
            before = "\n".join(lines[max(0, number - 6):number + 1])
            if "model_usage.call(" not in before:
                unrecorded.append(f"{path.relative_to(root)}:{number + 1}")
    assert unrecorded == []
    # The subscription's one exit records every pass of the command line.
    assert "model_usage.call(" in Path(root / "core/claude_code_note.py").read_text(encoding="utf-8")
