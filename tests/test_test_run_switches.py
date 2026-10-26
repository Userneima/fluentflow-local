"""Test runs on real data: a budget on model calls and the fake model.

Requirements (owner, 2026-10-26, decision 8.11), in the user's terms:

- A service started with a budget (calls, and money at API prices, the
  subscription included) refuses a model call once the budget is used up.
  The refused call is recorded; the step it was for ends failed and says
  「超出这次测试的模型预算」, even a step that otherwise goes on without its
  model. Without a budget nothing changes.
- A service started with the fake model answers every model call with fixed
  content in the provider's shape: no request leaves the machine and no
  ``claude`` command line is started. Each call is still recorded, under the
  provider "fake", at no cost. A whole task run this way reaches the end of
  every step it takes, and its note lands in the output folder with its PDF.
- A new exit that calls a model and does not answer from the fake model is
  caught.
- ``/health`` and the settings page's 需要你处理 say when either is on.
"""

from __future__ import annotations

import ast
import asyncio
import socket
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import ai_client, ai_summarizer, claude_code_note as ccn, claude_text, claude_vision, cut_judge
from backend.core import cut_report, fake_model, job_store, media_job, model_usage, note_coverage, note_pdf
from backend.core import note_section_times, output_folder, survey_cut, tail_trim, task_steps, visual_note_job
from backend.core import task_flow as tf, workflow_status
from backend.core.result_artifacts import _attach_result_artifacts
from backend.routers import local_system

TRANSCRIPT_LINES = [f"第{i}句，今天讲竞品调研的方法和步骤。" for i in range(40)]
TRANSCRIPT = "\n".join(TRANSCRIPT_LINES)
SEGMENTS = [{"start": float(i * 6), "end": float(i * 6) + 5.5, "text": text} for i, text in enumerate(TRANSCRIPT_LINES)]


@pytest.fixture(autouse=True)
def fresh_budget(monkeypatch):
    for name in (*model_usage.BUDGET_ENV.values(), fake_model.ENV):
        monkeypatch.delenv(name, raising=False)
    model_usage.reset_budget()
    yield
    model_usage.reset_budget()


def _task_id() -> str:
    return f"switch-{uuid.uuid4().hex[:8]}"


# ── the far side of every exit: any request reaching it fails the test ──────

class Reached:
    """Records every attempt to reach a model, a network or the command line."""

    def __init__(self):
        self.attempts: list[str] = []

    def stop(self, what: str):
        self.attempts.append(what)
        raise AssertionError(f"a real request was made: {what}")


@pytest.fixture
def reached(monkeypatch):
    found = Reached()

    def no_connect(_sock, address, *_a, **_k):
        found.stop(f"network {address}")

    class NoOpenAI:
        """Building a client sends nothing; a request through it would."""

        def __init__(self, *, base_url=None, **_kwargs):
            self.base_url = base_url or "https://api.deepseek.com"
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **_k: found.stop("OpenAI request")))
            self.models = SimpleNamespace(list=lambda **_k: found.stop("OpenAI model list"))

    class NoAnthropic:
        def __init__(self, **_kwargs):
            self.messages = SimpleNamespace(stream=lambda **_k: found.stop("Anthropic stream"))
            self.models = SimpleNamespace(list=lambda **_k: found.stop("Anthropic model list"))

    real_popen = subprocess.Popen

    def popen(command, *args, **kwargs):
        if Path(str(command[0] if isinstance(command, (list, tuple)) else command)).name == "claude":
            found.stop("claude command line")
        return real_popen(command, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", no_connect)
    monkeypatch.setattr(ai_client, "OpenAI", NoOpenAI)
    monkeypatch.setattr(claude_vision.anthropic, "Anthropic", NoAnthropic)
    monkeypatch.setattr(ccn, "_run_tracked", lambda command, **_k: found.stop(f"command line {command[:2]}"))
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(ccn, "cli_path", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr(local_system, "_http_request", lambda *a, **_k: found.stop(f"HTTP {a[:2]}"))
    # The real choice of who judges the cut and who checks the note: with the
    # far side guarded, nothing real can be reached through them.
    monkeypatch.setattr(cut_judge, "choose", cut_judge.find)
    return found


def _owner_setup(monkeypatch):
    """The owner's machine: the Claude subscription first, DeepSeek as the key."""
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "subscription")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("AI_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")


# ── the fake model: a whole task through the real flow ─────────────────────

class FakeModelTask:
    """``task_flow`` with each step's model work done by the code that does
    it, through the real exits and the channels the owner's settings pick:
    the transcript's corrections, the cut's judgement, its ending and its
    report, the note, its check and its section times. Transcribing and
    rendering are local work with no model and are given."""

    def __init__(self, task_id: str, folder: Path):
        self.task_id = task_id
        self.source = folder / "kickoff.mp4"
        self.source.write_bytes(b"original")

    async def pipeline(self, ctx):
        ctx.on_transcribed({"segments": SEGMENTS})
        ctx.loop = asyncio.get_running_loop()
        await media_job._run_glossary_correction_stage(ctx, segments=SEGMENTS, ai_kwargs={})
        result = _attach_result_artifacts(self.task_id, {
            "task_id": self.task_id, "filename": self.source.name, "display_title": self.source.stem,
            "transcript_text": TRANSCRIPT, "raw_segments": SEGMENTS, "display_segments": SEGMENTS,
        })
        job_store.upsert_job(
            task_id=self.task_id, status="completed", stage="done", progress=100, result=result,
            metadata={"folder_intake": {"chosen_with": "folder_path", "original_path": str(self.source)}},
        )

    def survey_and_cut(self, task_id, client_id, on_plan=None):
        judge = cut_judge.choose()
        answer = survey_cut.parse_answer(
            _in_part(task_steps.CUT, "判断剪哪里", lambda: judge.text(survey_cut.SYSTEM, "[0] 大家好")))
        assert answer == {"keep_gaps": [], "remove": []}
        ends = tail_trim._ask(tail_trim._default_chat()[0], "录像总长 240 秒。\n[230] 好，今天就到这里。")
        assert tail_trim._answer(ends) == (None, "假模型：不判断结尾。")
        facts = [{"id": "1", "action": "removed", "kind": "short_gaps", "count": 3, "seconds": 2.0,
                  "default": "停顿太短"}]
        report = _in_part(task_steps.CUT, "整理处理报告",
                          lambda: cut_report.organize(facts, survey_cut._default_chat()))
        assert [section["title"] for section in report["sections"]][0] == "处理记录（假模型）"
        _patch(task_id, debreath={"status": "completed"})

    def write_note(self, task_id, client_id, *, on_local_work_done=None, planned_ranges=None):
        if on_local_work_done:
            on_local_work_done()
        draft = ccn.write_visual_note(TRANSCRIPT, [])
        checked = note_coverage.check_against_transcript(visual_note_job.coverage_checker()[0], TRANSCRIPT,
                                                         draft.markdown, chunk_chars=400)
        assert checked.checked
        _patch(task_id, summary_markdown=draft.markdown, summary_status="completed")
        assert note_section_times.align(task_id)["state"] in {"ready", "unchanged"}


def _in_part(step, label, fn):
    with model_usage.part_of(step, label):
        return fn()


def _patch(task_id, **changes):
    job = job_store.get_job(task_id) or {}
    result = dict(job.get("result") or {})
    result.update(changes)
    job_store.update_job_result(task_id, result)


async def _no(*_args, **_kwargs):
    return False


def _run_fake_task(monkeypatch, tmp_path, task_id):
    monkeypatch.setenv(fake_model.ENV, "1")
    _owner_setup(monkeypatch)
    flow = FakeModelTask(task_id, tmp_path)
    pdfs: list[Path] = []

    def export_pdf(task_id, *, client_id=None, output_dir=None, filename=None):
        # The browser that prints the PDF is local work; stood in for here,
        # run for real in the test on a copy of the real data.
        path = Path(output_dir) / f"{filename}.pdf"
        path.write_bytes(b"%PDF fake")
        pdfs.append(path)
        return {"task_id": task_id, "pdf_path": str(path), "html_path": None}

    async def deliver(ctx):
        output_folder.sync(ctx.task_id_value)

    monkeypatch.setattr(note_pdf, "export_pdf", export_pdf)
    monkeypatch.setattr(tf, "_run_pipeline", flow.pipeline)
    monkeypatch.setattr(tf, "_agent_judges_cut", _no)
    monkeypatch.setattr(tf.local_intake_flow, "survey_first_enabled", lambda: True)
    monkeypatch.setattr(tf.local_intake_flow, "note_is_wanted", lambda *_a: True)
    monkeypatch.setattr(tf.local_intake_flow, "mark_note_running", lambda *_a: None)
    monkeypatch.setattr(tf.local_intake_flow, "write_note", flow.write_note)
    monkeypatch.setattr(tf.local_intake_flow, "compress_original", lambda *_a: None)
    monkeypatch.setattr(tf.survey_cut, "survey_and_cut", flow.survey_and_cut)
    monkeypatch.setattr(tf.visual_note_job, "put_back_onto_original", lambda *_a, **_k: False)
    monkeypatch.setattr(tf, "_deliver_to_output_folder", deliver)
    monkeypatch.setattr(media_job.correction_notes, "for_task", lambda *_a: "竞品调研")
    job_store.upsert_job(task_id=task_id, status="queued", stage="queued", progress=0)
    ctx = SimpleNamespace(
        task_id_value=task_id, client_id=None, do_lark=False, note_deferred_to_visual_note=True,
        secret_resolver=lambda value, _name: value, source_type="video", source_filename="kickoff.mp4",
        event_route="/test",
    )
    asyncio.run(asyncio.wait_for(tf.run_pipeline_then_note(asyncio.Event(), ctx), 20))
    return flow, pdfs


@pytest.mark.real_coverage_checker
def test_with_the_fake_model_a_whole_task_ends_every_step_without_one_real_request(monkeypatch, tmp_path, reached):
    task_id = _task_id()
    flow, pdfs = _run_fake_task(monkeypatch, tmp_path, task_id)

    assert reached.attempts == []
    steps = task_steps.statuses(task_steps.steps_of([task_id])[task_id])
    assert {name: steps[name] for name in (task_steps.TRANSCRIBE, task_steps.CORRECT, task_steps.CUT, task_steps.NOTE)} \
        == dict.fromkeys((task_steps.TRANSCRIBE, task_steps.CORRECT, task_steps.CUT, task_steps.NOTE), task_steps.DONE)
    assert not [name for name, status in steps.items() if status not in (task_steps.DONE, task_steps.SKIPPED)]
    rows = job_store.list_model_calls(task_id)
    items = {row["item"] for row in rows}
    assert {"词表纠错", "判断剪哪里", "判断结尾", "整理处理报告", "写正文", "查漏", "对齐分段时间"} <= items
    assert {row["provider"] for row in rows} == {model_usage.FAKE} and all(row["ok"] for row in rows)
    usage = model_usage.summarize(task_id)["total"]
    assert usage["fake_calls"] == len(rows) and usage["cost"] == {} and usage["subscription_calls"] == 0
    folder = output_folder.sync(task_id)
    md = Path(folder["md_path"])
    assert md.parent.parent == flow.source.parent and fake_model.NOTE.splitlines()[0] in md.read_text(encoding="utf-8")
    assert pdfs and pdfs[0].parent == md.parent


@pytest.mark.real_coverage_checker
def test_the_whole_task_check_is_not_vacuous_without_the_fake_model(monkeypatch, tmp_path, reached):
    """With the switch off, the same task tries to reach a model at once."""
    task_id = _task_id()
    monkeypatch.setenv(fake_model.ENV, "0")
    with pytest.raises(Exception):
        _owner_setup(monkeypatch)
        FakeModelTask(task_id, tmp_path).survey_and_cut(task_id, None)
    assert reached.attempts


# ── the fake model: each exit on its own ───────────────────────────────────

def _exit_text_model():
    return ai_summarizer.text_model_chat(provider="deepseek")("system", "user")


def _exit_anthropic_key_text():
    return claude_text._api_key_chat("test-anthropic-key", "claude-opus-5-5")("只回 JSON 对象", "user")


def _exit_anthropic_key_note():
    return claude_vision.write_visual_note(TRANSCRIPT, [], api_key="test-anthropic-key", model="claude-opus-5-5").markdown


def _exit_subscription():
    return claude_text._subscription_chat("opus", None)("system", "user")


def _exit_cut_judge_key():
    return cut_judge._api_key("test-anthropic-key")("system", "user", [])


def _exit_cut_judge_picture_model():
    return cut_judge._openai_compatible("https://vision.example/v1", "k", "vision-1")("system", "user", [])


def _exit_key_check():
    return local_system._check_anthropic_key("test-anthropic-key")["message"]


EXITS = [_exit_text_model, _exit_anthropic_key_text, _exit_anthropic_key_note, _exit_subscription,
         _exit_cut_judge_key, _exit_cut_judge_picture_model, _exit_key_check]


@pytest.mark.parametrize("ask", EXITS, ids=lambda fn: fn.__name__[6:])
def test_every_exit_answers_from_the_fake_model_and_records_it(monkeypatch, reached, ask):
    _owner_setup(monkeypatch)
    monkeypatch.setenv(fake_model.ENV, "1")
    task_id = _task_id()
    with model_usage.scope(task_id, task_steps.NOTE, "写正文"):
        answer = ask()
    assert answer and reached.attempts == []
    rows = job_store.list_model_calls(task_id)
    assert [(row["provider"], row["billing"], bool(row["ok"])) for row in rows] == [(model_usage.FAKE, model_usage.FAKE, True)]


def _with_fake_branch(source: str) -> list[str]:
    """Each ``with model_usage.call(...) as X`` block in ``source`` that never
    asks ``X.fake``: an exit that would send a real request with the fake
    model on."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            expr = item.context_expr
            if not (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "call"
                    and isinstance(expr.func.value, ast.Name) and expr.func.value.id == "model_usage"):
                continue
            name = item.optional_vars.id if isinstance(item.optional_vars, ast.Name) else None
            asks = name is not None and any(
                isinstance(test, ast.If) and any(
                    isinstance(part, ast.Attribute) and part.attr == "fake"
                    and isinstance(part.value, ast.Name) and part.value.id == name
                    for part in ast.walk(test.test))
                for statement in node.body for test in ast.walk(statement))
            if not asks:
                found.append(str(node.lineno))
    return found


def test_every_exit_in_the_code_asks_whether_the_fake_model_answers():
    root = Path(__file__).resolve().parents[1] / "backend"
    found = []
    for path in sorted(root.rglob("*.py")):
        found += [f"{path.name}:{line}" for line in _with_fake_branch(path.read_text(encoding="utf-8"))]
    assert found == []


def test_the_guard_catches_an_exit_that_never_asks():
    new_exit = ("def ask(client):\n    with model_usage.call('deepseek', 'm') as call:\n"
                "        response = client.chat.completions.create(model='m', messages=[])\n"
                "        call.openai(response)\n    return response\n")
    assert _with_fake_branch(new_exit) == ["2"]


# ── the fake model's answers are in the shape each request asks for ────────

@pytest.mark.parametrize("item,system,user,check", [
    ("判断剪哪里", survey_cut.SYSTEM, "[0] 大家好", lambda text: survey_cut.parse_answer(text)),
    ("判断结尾", tail_trim.SYSTEM, "[1] 好", lambda text: tail_trim._answer(text)),
    ("翻译", "", '[{"index": 3, "text": "hello"}]',
     lambda text: ai_summarizer._extract_json_array(text)[0]["start_index"] == 3),
    ("对齐分段时间", "", "## 一〔S1〕\n## 二〔S2〕\n[0:00:05] 第一句\n[0:03:40] 最后",
     lambda text: note_section_times.parse_answer(text, [{"index": 0}, {"index": 1}], 240.0)[1]["primary"] == [5.0, 220.0]),
    ("查漏后补写", "", "--- 笔记 ---\n\n# 原来的笔记\n\n--- 没写到的知识点 ---\n\n- 一点",
     lambda text: text == "# 原来的笔记"),
])
def test_the_fake_answers_parse_where_they_are_read(item, system, user, check):
    with model_usage.scope("t", task_steps.NOTE, item):
        assert check(fake_model.reply(system, user))


def test_the_fake_note_is_long_enough_to_be_kept_and_has_sections():
    from backend.core import visual_note_check

    assert visual_note_check.too_short_reason(fake_model.NOTE, TRANSCRIPT * 3) is None
    assert fake_model.NOTE.count("\n## ") >= 2


# ── the budget ─────────────────────────────────────────────────────────────

class FakeDeepSeek:
    """DeepSeek's endpoint, answering and reporting usage."""

    def __init__(self, seen, **_kwargs):
        self.base_url = "https://api.deepseek.com"
        self.seen = seen
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, *, model, messages, **_kwargs):
        self.seen.append(model)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"fixes": []}'))],
            usage=SimpleNamespace(prompt_tokens=100_000, completion_tokens=10_000, prompt_cache_hit_tokens=0),
        )


def _deepseek(monkeypatch, seen):
    _owner_setup(monkeypatch)
    monkeypatch.setattr(ai_client, "OpenAI", lambda **kwargs: FakeDeepSeek(seen, **kwargs))


def test_past_the_call_limit_the_next_call_is_refused_and_recorded(monkeypatch):
    seen: list[str] = []
    _deepseek(monkeypatch, seen)
    monkeypatch.setenv("FLUENTFLOW_TEST_MAX_CALLS", "1")
    task_id = _task_id()
    chat = ai_summarizer.text_model_chat(provider="deepseek")
    with model_usage.scope(task_id, task_steps.CORRECT, "词表纠错"):
        chat("system", "user")
        with pytest.raises(model_usage.BudgetExceeded, match=model_usage.BUDGET_EXCEEDED):
            chat("system", "user")
    assert len(seen) == 1, "the refused call reached the provider"
    rows = job_store.list_model_calls(task_id)
    assert [bool(row["ok"]) for row in rows] == [True, False]
    assert model_usage.BUDGET_EXCEEDED in rows[1]["error"]
    assert model_usage.summarize(task_id)["total"]["failure"] == model_usage.BUDGET_EXCEEDED


def test_a_step_that_goes_on_without_its_model_still_ends_failed_for_the_budget(monkeypatch):
    """The glossary correction never fails a task on a model error; refused
    for the budget, its step still says so."""
    seen: list[str] = []
    _deepseek(monkeypatch, seen)
    monkeypatch.setenv("FLUENTFLOW_TEST_MAX_CALLS", "0")
    task_id = _task_id()
    job_store.upsert_job(task_id=task_id, status="running", stage="transcribing", progress=50)
    task_steps.start(task_id, task_steps.CORRECT)
    from backend.core import glossary_correction

    outcome = glossary_correction.correct(SEGMENTS, "竞品调研", ai_summarizer.text_model_chat(provider="deepseek"))
    assert outcome.status == "failed"
    task_steps.finish(task_id, task_steps.CORRECT, task_steps.DONE)
    step = task_steps.steps_of([task_id])[task_id][0]
    assert (step["status"], step["error_reason"]) == (task_steps.FAILED, model_usage.BUDGET_EXCEEDED)
    assert seen == []


def test_the_money_limit_counts_subscription_calls_at_api_price(monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_TEST_MAX_USD", "0.4")

    def runner(command, **_kwargs):
        event = {"type": "result", "result": "好的", "total_cost_usd": 0.42,
                 "usage": {"input_tokens": 10, "output_tokens": 5}, "modelUsage": {"claude-opus-5-5": {"outputTokens": 5}}}
        import json

        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(event), stderr="")

    monkeypatch.setattr(ccn, "cli_path", lambda: "/usr/local/bin/claude")
    chat = claude_text._subscription_chat("opus", runner)
    task_id = _task_id()
    with model_usage.scope(task_id, task_steps.NOTE, "查漏"):
        chat("system", "user")
        with pytest.raises(Exception, match=model_usage.BUDGET_EXCEEDED):
            chat("system", "user")
    assert model_usage.spent()["USD"] > 0.4


def test_a_money_limit_holds_back_only_calls_billed_in_its_currency(monkeypatch):
    """A yuan limit used up stops DeepSeek, not Claude."""
    seen: list[str] = []
    _deepseek(monkeypatch, seen)
    monkeypatch.setenv("FLUENTFLOW_TEST_MAX_CNY", "0")
    monkeypatch.setattr(ccn, "cli_path", lambda: "/usr/local/bin/claude")

    def runner(command, **_kwargs):
        import json

        event = {"type": "result", "result": "好的", "total_cost_usd": 0.01, "usage": {"input_tokens": 1, "output_tokens": 1}}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(event), stderr="")

    with model_usage.scope(_task_id(), task_steps.NOTE, "查漏"):
        assert claude_text._subscription_chat("opus", runner)("system", "user") == "好的"
        with pytest.raises(model_usage.BudgetExceeded):
            ai_summarizer.text_model_chat(provider="deepseek")("system", "user")
    assert seen == []


def test_without_a_budget_nothing_is_refused(monkeypatch):
    seen: list[str] = []
    _deepseek(monkeypatch, seen)
    chat = ai_summarizer.text_model_chat(provider="deepseek")
    with model_usage.scope(_task_id(), task_steps.CORRECT):
        for _ in range(5):
            chat("system", "user")
    assert len(seen) == 5 and model_usage.budget() is None


def test_fake_calls_count_against_no_budget(monkeypatch, reached):
    _owner_setup(monkeypatch)
    monkeypatch.setenv(fake_model.ENV, "1")
    monkeypatch.setenv("FLUENTFLOW_TEST_MAX_CALLS", "0")
    with model_usage.scope(_task_id(), task_steps.CORRECT):
        _exit_text_model()
    assert model_usage.spent()["calls"] == 0


# ── saying it is on ────────────────────────────────────────────────────────

def test_health_and_the_settings_page_say_when_a_test_switch_is_on(monkeypatch):
    app = FastAPI()
    app.include_router(local_system.router)
    client = TestClient(app)
    assert client.get("/health").json()["test_run"] == {"fake_model": False, "budget": None, "spent": None}
    assert not [item for item in workflow_status.workflow_status()["attention"] if item["id"].startswith(("fake", "test"))]

    monkeypatch.setenv(fake_model.ENV, "1")
    monkeypatch.setenv("FLUENTFLOW_TEST_MAX_CALLS", "3")
    run = client.get("/health").json()["test_run"]
    assert run["fake_model"] is True and run["budget"] == {"calls": 3.0} and run["spent"]["calls"] == 0
    shown = {item["id"]: item for item in workflow_status.workflow_status()["attention"]}
    assert "假模型" in shown["fake-model"]["text"]["zh"] and shown["fake-model"]["action"] == "none"
    assert "上限" in shown["test-budget"]["text"]["zh"]


# ── the copy a test run starts on ──────────────────────────────────────────

def _real_data(tmp_path: Path) -> Path:
    import json
    import sqlite3

    real = tmp_path / "FluentFlow"
    (real / "models" / "speaker").mkdir(parents=True)
    with sqlite3.connect(real / "fluentflow_jobs.sqlite") as db:
        db.execute("create table job_steps (task_id text, step_type text, status text)")
        db.executemany("insert into job_steps values (?, ?, ?)",
                       [("a", "task_step:cut", "running"), ("a", "task_step:note", "pending"), ("b", "task_step:note", "done")])
    (real / "fluentflow_config.json").write_text(json.dumps(
        {"preferences": {"export_to_lark": True}, "secrets": {"deepseek_api_key": "k"}}), encoding="utf-8")
    (real / "recommended_models.json").write_text("{}", encoding="utf-8")
    return real


def _statuses(database: Path) -> list[str]:
    import sqlite3

    with sqlite3.connect(database) as db:
        return [row[0] for row in db.execute("select status from job_steps order by rowid")]


def test_the_test_copy_closes_open_steps_in_the_copy_only_and_prints_a_capped_start(tmp_path, capsys):
    import json

    from scripts import prepare_test_copy

    real = _real_data(tmp_path)
    before = (real / "fluentflow_jobs.sqlite").read_bytes()
    into = tmp_path / "copy"
    assert prepare_test_copy.main(["--source", str(real), "--into", str(into), "--fake", "--port", "8013"]) == 0

    assert (real / "fluentflow_jobs.sqlite").read_bytes() == before
    assert _statuses(real / "fluentflow_jobs.sqlite") == ["running", "pending", "done"]
    assert _statuses(into / "fluentflow_jobs.sqlite") == ["skipped", "skipped", "done"]
    assert (into / "models").is_symlink() and (into / "models").resolve() == (real / "models").resolve()
    config = json.loads((into / "fluentflow_config.json").read_text(encoding="utf-8"))
    assert config["preferences"]["export_to_lark"] is False and config["secrets"] == {"deepseek_api_key": "k"}
    assert json.loads((real / "fluentflow_config.json").read_text(encoding="utf-8"))["preferences"]["export_to_lark"]
    printed = capsys.readouterr().out
    for part in (f"FLUENTFLOW_DATA_DIR={into.resolve()}", "FLUENTFLOW_FAKE_MODEL=1", "FLUENTFLOW_TEST_COPY=1",
                 "FLUENTFLOW_TEST_MAX_CALLS=40", "FLUENTFLOW_TEST_MAX_USD=1", "--port 8013"):
        assert part in printed


def test_the_test_copy_refuses_to_go_inside_the_real_data(tmp_path):
    from scripts import prepare_test_copy

    real = _real_data(tmp_path)
    with pytest.raises(SystemExit):
        prepare_test_copy.main(["--source", str(real), "--into", str(real / "copy")])
