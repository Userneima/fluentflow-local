"""What the page can show while FluentFlow writes a note (user feedback,
2026-11-09: minutes of a spinner and 「正在写笔记」 with nothing else).

Written from what the page must be able to say:
- a long text-model note: how many stretches have been read for points, then
  the chapters chosen (their titles), which chapters are written, and the text
  of each chapter as it is written; then that it is unifying and checking;
- a short text-model note: its text as it streams in;
- a Claude note in parts: which part is being written, of how many, over which
  stretch of the recording, and the parts already written;
- the task list and the editor read it from the task's activity, and only
  while the note is being written.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import backend.core.ai_summarizer as summ
from backend.core import ai_client, model_usage, note_progress, task_activity, task_steps
from backend.core.ai_prompts import _CHAPTER_EVIDENCE_SYSTEM, _CHAPTER_NOTE_SYSTEM, _CHAPTER_OUTLINE_SYSTEM

TASK = "task-progress-1"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    note_progress.reset()
    monkeypatch.setattr(summ, "_get_client", lambda **_k: object())
    monkeypatch.setattr(summ, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("FLUENTFLOW_NOTE_DEADLINE_SECONDS", raising=False)
    monkeypatch.delenv("FLUENTFLOW_NOTE_MODE", raising=False)
    yield
    note_progress.reset()


def _stream(text: str) -> None:
    """What a streamed answer does: hands over the text so far, twice."""
    shown = note_progress.sink()
    if shown is not None:
        shown(text[: len(text) // 2])
        shown(text)


def test_a_long_text_note_shows_its_reading_its_chapters_and_their_text(monkeypatch):
    seen: dict[str, object] = {}
    chapters = {"开场": "## 开场\n\n最小二乘法的定义。", "推导": "## 推导\n\n求导令其为零。"}

    def chat(client, model, system, user, *, temperature=0.3):
        if system.startswith(_CHAPTER_EVIDENCE_SYSTEM[:40]):
            seg = json.loads(user)[0]["segment_id"]
            return json.dumps([{"text": f"{seg} 的要点", "source_segment_ids": [seg], "importance": 3}],
                              ensure_ascii=False)
        if system == _CHAPTER_OUTLINE_SYSTEM:
            seen["while_planning"] = note_progress.snapshot(TASK)
            return json.dumps([
                {"title": "开场", "used_evidence_ids": ["E001"]},
                {"title": "推导", "used_evidence_ids": ["E002"]},
            ], ensure_ascii=False)
        if system.startswith(_CHAPTER_NOTE_SYSTEM[:40]):
            title = json.loads(user)["title"]
            seen.setdefault("while_writing", note_progress.snapshot(TASK))
            _stream(chapters[title])
            return chapters[title]
        if "没写到的知识点" not in user and user.startswith("## 开场"):
            seen["while_unifying"] = note_progress.snapshot(TASK)
            seen["draft_while_unifying"] = note_progress.draft(TASK)
            return user  # the style pass
        seen.setdefault("while_checking", note_progress.snapshot(TASK))
        return '{"missing_ids": []}'

    monkeypatch.setattr(summ, "_chat", chat)
    summ.summarize_transcript_with_metadata(
        "讲座内容。\n" * 4000, note_mode="chapter_coverage", provider="deepseek", task_id=TASK,
    )

    planning = seen["while_planning"]
    assert planning["stage"] == note_progress.PLANNING
    assert planning["read_total"] >= 2 and planning["read_done"] == planning["read_total"]
    writing = seen["while_writing"]
    assert writing["stage"] == note_progress.WRITING
    assert [chapter["title"] for chapter in writing["chapters"]] == ["开场", "推导"]
    assert seen["while_unifying"]["stage"] == note_progress.UNIFYING
    assert all(chapter["done"] for chapter in seen["while_unifying"]["chapters"])
    # Both chapters, in their order, as they were written.
    draft = seen["draft_while_unifying"]
    assert draft.index("最小二乘法的定义") < draft.index("求导令其为零")
    assert seen["while_checking"]["stage"] == note_progress.CHECKING
    # Done: the page reads it as finishing, and no writer is on it any more.
    assert note_progress.snapshot(TASK)["stage"] == note_progress.FINISHING
    assert TASK not in note_progress.live_task_ids()


def test_a_short_text_note_shows_its_text_while_it_streams(monkeypatch):
    halfway: list[str] = []
    note = "# 标题\n\n## 一、定义\n\n" + "最小二乘法是一种拟合方法。\n" * 20

    def chat(client, model, system, user, *, temperature=0.3):
        if user.startswith("短讲座"):
            shown = note_progress.sink()
            shown(note[:30])
            halfway.append(note_progress.draft(TASK))
            shown(note)
            return note
        return '{"missing": []}'

    monkeypatch.setattr(summ, "_chat", chat)
    summ.summarize_transcript_with_metadata("短讲座。" * 100, provider="deepseek", task_id=TASK)

    assert halfway == [note[:30].strip()]


def test_the_task_of_the_call_is_used_when_the_caller_names_none(monkeypatch):
    stages: list[str] = []

    def chat(client, model, system, user, *, temperature=0.3):
        stages.append(note_progress.snapshot(TASK)["stage"])
        return "# 笔记\n\n" + "正文。\n" * 30

    monkeypatch.setattr(summ, "_chat", chat)
    with model_usage.scope(TASK, task_steps.NOTE):
        summ.summarize_transcript_with_metadata("短讲座。" * 100, provider="deepseek")

    assert stages[0] == note_progress.WRITING


def test_outside_a_task_nothing_is_recorded_and_nothing_streams(monkeypatch):
    sinks: list[object] = []

    def chat(client, model, system, user, *, temperature=0.3):
        sinks.append(note_progress.sink())
        return "# 笔记\n\n" + "正文。\n" * 30

    monkeypatch.setattr(summ, "_chat", chat)
    summ.summarize_transcript_with_metadata("短讲座。" * 100, provider="deepseek")

    assert sinks and all(sink is None for sink in sinks)
    assert note_progress.live_task_ids() == []


class _Chunk(SimpleNamespace):
    pass


class _StreamingClient:
    """An OpenAI-compatible client that answers in pieces, the last carrying usage."""

    base_url = "https://api.deepseek.com/v1"

    def __init__(self, pieces, *, refuse_streaming=False):
        self.pieces = pieces
        self.refuse_streaming = refuse_streaming
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if kwargs.get("stream"):
            if self.refuse_streaming:
                error = RuntimeError("stream_options is not supported")
                error.status_code = 400
                raise error
            chunks = [_Chunk(choices=[SimpleNamespace(delta=SimpleNamespace(content=piece), finish_reason=None)],
                             usage=None) for piece in self.pieces]
            chunks.append(_Chunk(choices=[], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5)))
            return iter(chunks)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="".join(self.pieces)), finish_reason="stop")],
            usage=None,
        )


def test_a_call_made_while_a_note_is_shown_streams_and_returns_the_whole_answer():
    client = _StreamingClient(["## 一、", "定义", "\n\n内容"])
    grown: list[str] = []
    note_progress.start(TASK, "text")
    with note_progress.showing(TASK, "draft"):
        answer = ai_client._chat(client, "deepseek-chat", "写笔记", "转录", on_text=grown.append)

    assert answer == "## 一、定义\n\n内容"
    assert grown[0] == "## 一、" and grown[-1] == answer
    assert client.requests[0]["stream"] is True


def test_a_call_with_nothing_to_show_does_not_stream(monkeypatch):
    client = _StreamingClient(["整段回答"])
    assert ai_client._chat(client, "deepseek-chat", "查漏", "笔记") == "整段回答"
    assert "stream" not in client.requests[0]


def test_a_provider_that_refuses_streaming_is_asked_again_without_it():
    client = _StreamingClient(["整段回答"], refuse_streaming=True)
    grown: list[str] = []
    assert ai_client._chat(client, "deepseek-chat", "写笔记", "转录", on_text=grown.append) == "整段回答"
    assert [bool(request.get("stream")) for request in client.requests] == [True, False]
    assert grown == ["整段回答"]


def test_a_claude_note_in_parts_shows_which_part_over_which_stretch_and_the_parts_done():
    note_progress.start(TASK, "claude")
    note_progress.part(TASK, index=1, total=3, starts_at="00:00", ends_at="20:00")
    first = note_progress.snapshot(TASK)
    note_progress.part_done(TASK, index=1, text="## 一、开场\n\n第一部分。")
    note_progress.part(TASK, index=2, total=3, starts_at="20:00", ends_at="40:00")
    second = note_progress.snapshot(TASK)

    assert (first["part_index"], first["parts_total"], first["part_starts_at"], first["part_ends_at"]) == (
        1, 3, "00:00", "20:00")
    assert second["parts_done"] == 1 and second["part_index"] == 2
    assert second["seconds_per_part"] is not None
    assert note_progress.draft(TASK) == "## 一、开场\n\n第一部分。"


def test_the_activity_carries_the_progress_only_while_the_note_is_written(monkeypatch):
    job = {"task_id": TASK, "status": "completed", "result": {"summary_status": "pending"}}
    note_progress.start(TASK, "text")
    note_progress.stage(TASK, note_progress.PLANNING)
    writing = task_activity.activity(job, steps=[]).as_dict()
    assert writing["current_step"] == task_steps.NOTE
    assert writing["note_progress"]["stage"] == note_progress.PLANNING

    done = {"task_id": TASK, "status": "completed", "result": {"summary_status": "completed"}}
    assert task_activity.activity(done, steps=[]).as_dict()["note_progress"] is None


def test_the_editor_reads_the_progress_and_the_text_so_far_of_a_note_being_written(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.local_main import app
    from backend.routers import local_jobs

    writing = {"task_id": TASK, "status": "completed", "result": {"summary_status": "pending"}}
    monkeypatch.setattr(local_jobs, "get_job", lambda task_id, client_id=None: dict(writing))
    note_progress.start(TASK, "text")
    note_progress.chapters(TASK, ["开场"])
    note_progress.text(TASK, "chapter:0", "## 开场\n\n写到一半")

    client = TestClient(app)
    body = client.get(f"/jobs/{TASK}/note-progress").json()
    assert body["progress"]["chapters"] == [{"title": "开场", "done": False}]
    assert body["draft"] == "## 开场\n\n写到一半"

    writing["result"] = {"summary_status": "completed"}
    assert client.get(f"/jobs/{TASK}/note-progress").json() == {"progress": None, "draft": ""}


def test_the_fake_model_goes_through_a_long_notes_stages_and_streams_it(monkeypatch):
    """A test run on the fake model shows the same stages a real one does."""
    monkeypatch.setenv("FLUENTFLOW_FAKE_MODEL", "1")
    monkeypatch.setenv("FLUENTFLOW_FAKE_MODEL_ANSWER_SECONDS", "0")
    shown: list[str] = []
    real_text = note_progress.text
    monkeypatch.setattr(note_progress, "text", lambda task_id, key, value: (shown.append(key), real_text(task_id, key, value)))

    result = summ.summarize_transcript_with_metadata(
        "讲座内容。\n" * 4000, note_mode="chapter_coverage", provider="deepseek", task_id=TASK,
    )

    assert "假模型的第一章" in result.markdown and "假模型的第二章" in result.markdown
    assert {"chapter:0", "chapter:1"} <= set(shown)
    assert [chapter["title"] for chapter in note_progress.snapshot(TASK)["chapters"]] == ["假模型的第一章", "假模型的第二章"]
