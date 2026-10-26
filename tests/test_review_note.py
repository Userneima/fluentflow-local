"""The review note: a second note beside the follow-along note (decision 5.37).

Requirements (docs/方案/2026-10-25-复习笔记.md, owner, 2026-10-25 and 10-26):

- Written from the follow-along md as it is on disk (the user's corrections
  and question boxes), never changing it; the transcript only checks facts.
- Lands beside it as ``<名>_复习笔记.md`` with a PDF; says which version of
  the follow-along note it came from; an earlier one is kept, not lost.
- Each source is a link back to the follow-along note's chapter that
  Obsidian can follow, plus the time in the cut video.
- When the output folder was moved and FluentFlow cannot find it, the agent
  passes the md's path; the task is still found from the folder's marker.
- Claude subscription first, the user's key when it fails (decisions 4.12,
  4.14). A failure says why, writes nothing and never fails the task.
- Changing a written review note at the user's word (decision 8.8) reworks
  that note, not the follow-along note, and keeps every link intact.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import claude_text, job_store, note_pdf, review_note
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.routers.local_agent import router as agent_router

STEM = "课_笔记"
BASE = """# 数据库扩容

## 一、为什么要扩容

单台数据库写满了。

### （一）读太多

读请求占九成。

> [!question] 九成是怎么量的？
> 看慢查询日志，按小时统计。

## 二、读写分离

从库复制有延迟。
"""

ANSWER = """# 数据库扩容：复习笔记

## 主线

先拆读，再拆写。

## 一、为什么要扩容

先说单台为什么扛不住。

### （一）读请求占九成

**结论**：读太多。

> [!note] 我的笔记
> 九成是看慢查询日志按小时统计的。

**出处**：〔S1 0:40〕、〔S2〕

## 术语表

- **从库**：只负责被读取的副本。

## 自测题

1. 为什么要扩容？→ [[#（一）读请求占九成|看答案]]

## 还没弄清的

底稿里没有剩下的存疑。

## 出处信息

- 录像日期：未知。
"""

SECTION_ROWS = [
    {"heading": "一、为什么要扩容", "level": 2, "primary": [10.0, 50.0], "others": []},
    {"heading": "（一）读太多", "level": 3, "primary": [20.0, 50.0], "others": []},
    {"heading": "二、读写分离", "level": 2, "primary": [50.0, 90.0], "others": []},
]


def _folder(root: Path, task_id: str | None = None) -> Path:
    folder = root / "课"
    (folder / "过程文件").mkdir(parents=True)
    (folder / f"{STEM}.md").write_text(BASE, encoding="utf-8")
    (folder / "过程文件" / "课.srt").write_text(
        "1\n00:00:20,000 --> 00:00:25,000\n读请求占了九成\n\n", encoding="utf-8",
    )
    if task_id:
        (folder / ".fluentflow-task").write_text(f"{task_id}\t\n", encoding="utf-8")
    return folder


def _task(task_id: str, md: Path | None, *, times: bool = True) -> None:
    result = {
        "display_segments": [{"start": 20.0, "end": 25.0, "text": "转录里说读请求占九成"}],
        "output_folder": {"md_path": str(md) if md else "/gone/课_笔记.md"},
    }
    if times:
        result["note_section_times"] = {"sections": SECTION_ROWS}
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, result=result)


class FakeChat:
    def __init__(self, answer: str = ANSWER, error: Exception | None = None):
        self.answer, self.error, self.calls = answer, error, []

    def __call__(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    # Notes are read only from the user's home; the test's folder is it.
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    printed: list[tuple[str, Path, str]] = []

    def fake_pdf(note, title, directory, stem, *, task_id=None, keep_html_without_browser=True):
        printed.append((note, Path(directory), stem))
        return {"pdf_path": str(Path(directory) / f"{stem}.pdf"), "html_path": None}

    monkeypatch.setattr(note_pdf, "markdown_to_pdf", fake_pdf)
    return printed


def test_written_beside_the_base_with_links_back_times_and_the_base_version(tmp_path, no_browser):
    folder = _folder(tmp_path)
    _task("t-review-1", folder / f"{STEM}.md")
    chat = FakeChat()

    out = review_note.generate("t-review-1", chat=(chat, "claude_code_subscription"))

    target = folder / "课_复习笔记.md"
    assert out["path"] == str(target)
    text = target.read_text(encoding="utf-8")
    # A sub-chapter is reached through its chapter, as Obsidian resolves it.
    assert f"[[{STEM}#一、为什么要扩容|第一章]] 0:40" in text
    assert f"[[{STEM}#一、为什么要扩容#（一）读太多|第一章（一）]] 0:20" in text
    assert "〔S" not in text
    sha = hashlib.sha256(BASE.encode("utf-8")).hexdigest()
    assert f"内容指纹 {sha[:12]}" in text
    assert (folder / f"{STEM}.md").read_text(encoding="utf-8") == BASE
    assert no_browser and no_browser[0][1] == folder and no_browser[0][2] == "课_复习笔记"
    record = job_store.get_job("t-review-1")["result"]["review_note"]
    assert record["base"]["sha256"] == sha and record["written_by"] == "claude_code_subscription"
    assert job_store.get_job("t-review-1")["status"] == "completed"


def test_the_model_sees_the_users_corrections_the_chapters_and_the_transcript(tmp_path):
    root = tmp_path / "系列"
    folder = _folder(root)
    (root / "FluentFlow 校正文档.md").write_text("- 从库：只读副本。转录常听成：重库", encoding="utf-8")
    _task("t-review-2", folder / f"{STEM}.md")
    chat = FakeChat()

    review_note.generate("t-review-2", chat=(chat, "x"))

    system, user = chat.calls[0]
    assert "# 写一份复习笔记" in system and "转录常听成：重库" in system
    assert "看慢查询日志，按小时统计。" in user  # the user's own answer
    assert "〔S2〕 第一章（一）" in user and "0:20–0:50" in user
    assert "转录里说读请求占九成" in user
    assert f"复习笔记全文写 {len(BASE) // 3} 到 {len(BASE) // 2} 字" in user


def test_a_moved_folder_is_reached_by_the_md_path_and_still_finds_its_task(tmp_path):
    folder = _folder(tmp_path, task_id="t-review-3")
    _task("t-review-3", None)  # FluentFlow still thinks the folder is elsewhere

    with pytest.raises(review_note.ReviewNoteError, match="note_path"):
        review_note.generate("t-review-3", chat=(FakeChat(), "x"))

    chat = FakeChat()
    out = review_note.generate(note_path=str(folder / f"{STEM}.md"), chat=(chat, "x"))
    assert out["task_id"] == "t-review-3"
    assert "[[课_笔记#一、为什么要扩容|第一章]] 0:40" in (folder / "课_复习笔记.md").read_text(encoding="utf-8")
    assert "转录里说读请求占九成" in chat.calls[0][1]


def test_without_any_task_the_transcript_beside_the_note_is_used(tmp_path):
    folder = _folder(tmp_path)
    chat = FakeChat()

    out = review_note.generate(note_path=str(folder / f"{STEM}.md"), chat=(chat, "x"))

    assert out["task_id"] is None
    assert "读请求占了九成" in chat.calls[0][1]
    assert "时间未知" in chat.calls[0][1]
    assert "第一章（一）]]" in (folder / "课_复习笔记.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("chat", [
    FakeChat(error=RuntimeError("Claude usage limit reached")),
    FakeChat(answer="# 只写了一半\n\n## 主线\n\n先拆读"),
])
def test_a_failure_says_why_writes_nothing_and_leaves_the_task_alone(tmp_path, chat):
    folder = _folder(tmp_path)
    _task("t-review-4", folder / f"{STEM}.md")
    before = job_store.get_job("t-review-4")

    with pytest.raises(review_note.ReviewNoteError) as raised:
        review_note.generate("t-review-4", chat=(chat, "x"))

    assert "usage limit" in str(raised.value) or "不完整" in str(raised.value)
    assert not (folder / "课_复习笔记.md").exists()
    after = job_store.get_job("t-review-4")
    assert after["status"] == before["status"] == "completed"
    assert "review_note" not in after["result"]


def test_writing_again_keeps_the_earlier_one(tmp_path):
    folder = _folder(tmp_path)
    _task("t-review-5", folder / f"{STEM}.md")
    review_note.generate("t-review-5", chat=(FakeChat(), "x"))
    first = (folder / "课_复习笔记.md").read_text(encoding="utf-8")

    review_note.generate("t-review-5", chat=(FakeChat(ANSWER.replace("先拆读，再拆写。", "先拆写。")), "x"))

    kept = list((folder / "过程文件" / "历史版本").glob("课_复习笔记_*.md"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == first
    assert "先拆写。" in (folder / "课_复习笔记.md").read_text(encoding="utf-8")


def test_the_subscription_comes_first_and_the_key_takes_over_when_it_fails(monkeypatch):
    def broken(system, user):
        raise RuntimeError("login expired")

    asked: dict[str, int] = {}

    def keyed_claude(api_key=None, *, runner=None, max_tokens=claude_text.TEXT_MAX_TOKENS):
        asked["max_tokens"] = max_tokens
        return (lambda system, user: "from the key"), "anthropic_api_key"

    monkeypatch.setattr(claude_text, "preferred_channel", lambda: claude_text.CHANNEL_SUBSCRIPTION)
    monkeypatch.setattr(claude_text, "subscription_chat", lambda runner=None: (broken, "claude_code_subscription"))
    monkeypatch.setattr(claude_text, "claude_chat", keyed_claude)
    from backend.core import ai_summarizer

    monkeypatch.setattr(ai_summarizer, "text_model_chat", lambda **_: None)

    chat, channel = review_note.default_chat()

    assert channel == "claude_code_subscription"
    assert chat("s", "u") == "from the key"
    # A key's default answer length would cut a long review note off.
    assert asked["max_tokens"] >= 32_000


def test_revising_changes_the_review_note_only_and_keeps_every_link(tmp_path):
    folder = _folder(tmp_path, task_id="t-review-6")
    _task("t-review-6", folder / f"{STEM}.md")
    review_note.generate("t-review-6", chat=(FakeChat(), "x"))
    before = (folder / "课_复习笔记.md").read_text(encoding="utf-8")
    revised = ANSWER.replace("### （一）读请求占九成", "### （一）读请求占了九成").replace(
        "[[#（一）读请求占九成|", "[[#（一）读请求占了九成|")
    chat = FakeChat(revised)

    review_note.revise(str(folder / "课_复习笔记.md"), "三级标题改一下", chat=(chat, "x"))

    system, user = chat.calls[0]
    assert "三级标题改一下" in user
    assert "〔S1 0:40〕" in user and "〔S2 0:20〕" in user and f"[[{STEM}#" not in user
    assert "底稿版本" not in user
    after = (folder / "课_复习笔记.md").read_text(encoding="utf-8")
    assert f"[[{STEM}#一、为什么要扩容#（一）读太多|第一章（一）]] 0:20" in after
    assert after.count("- 底稿版本：") == 1
    assert [line for line in before.splitlines() if "底稿版本" in line] == [
        line for line in after.splitlines() if "底稿版本" in line]
    assert (folder / f"{STEM}.md").read_text(encoding="utf-8") == BASE
    assert list((folder / "过程文件" / "历史版本").glob("课_复习笔记_*.md"))


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(agent_router)
    return TestClient(app)


_HEADERS = {"x-fluentflow-access-token": "review-token"}


def test_the_agent_route_writes_it_and_reports_a_failure_with_its_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "review-token")
    folder = _folder(tmp_path, task_id="t-review-7")
    _task("t-review-7", None)
    monkeypatch.setattr(review_note, "default_chat", lambda: (FakeChat(), "claude_code_subscription"))

    ok = _client().post("/agent/v1/review-notes", headers=_HEADERS, json={"note_path": str(folder / f"{STEM}.md")})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert ok.json()["path"] == str(folder / "课_复习笔记.md")

    monkeypatch.setattr(review_note, "default_chat", lambda: (FakeChat(error=RuntimeError("网络断了")), "x"))
    failed = _client().post("/agent/v1/review-notes", headers=_HEADERS, json={"task_id": "t-review-7"})
    assert failed.status_code == 422
    assert failed.json()["detail"]["code"] == "review_note_failed"
    assert "note_path" in failed.json()["detail"]["message"]
    assert job_store.get_job("t-review-7")["status"] == "completed"

    missing = _client().post("/agent/v1/review-notes", headers=_HEADERS, json={"task_id": "no-such-task"})
    assert missing.status_code == 404

    monkeypatch.setattr(review_note, "default_chat", lambda: (FakeChat(ANSWER), "x"))
    revised = _client().post("/agent/v1/review-notes/revise", headers=_HEADERS, json={
        "note_path": str(folder / "课_复习笔记.md"), "instruction": "按线索排"})
    assert revised.status_code == 200 and revised.json()["instruction"] == "按线索排"


def test_on_paper_a_link_back_to_the_follow_along_note_keeps_only_its_words():
    page = note_pdf.printable_html(None, f"**出处**：[[{STEM}#一、为什么要扩容|第一章]] 0:40", "t")
    assert "第一章 0:40" in page and "[[" not in page


def test_the_writing_rules_use_no_example_from_the_interview_they_were_tried_on():
    skill = review_note.skill_text()
    assert not any(word in skill for word in ("罗福莉", "MiMo", "OpenClaw", "MTP", "MLA"))
    for heading in ("## 主线", "## 术语表", "## 自测题", "## 还没弄清的", "## 出处信息"):
        assert f"`{heading}" in skill or heading in skill


# ---- after the review of 5d0de23 (main session, 2026-10-26) -------------------------------


def test_the_second_task_on_a_recording_in_its_own_folder_uses_its_own_material(tmp_path):
    folder = tmp_path / "课"
    (folder / "过程文件").mkdir(parents=True)
    # The recording sits in its own folder: both tasks share it, the second's
    # files carry 「 (2)」 (output_folder.place).
    (folder / ".fluentflow-task").write_text("t-first\t\nt-second\t (2)\n", encoding="utf-8")
    (folder / "课_笔记.md").write_text(BASE, encoding="utf-8")
    second = folder / "课_笔记 (2).md"
    second.write_text(BASE, encoding="utf-8")
    job_store.upsert_job(task_id="t-first", status="completed", client_id=LOCAL_OWNER_ID, result={
        "display_segments": [{"start": 1.0, "end": 2.0, "text": "第一条任务的转录"}],
        "note_section_times": {"sections": SECTION_ROWS},
    })
    later_rows = [dict(row, primary=[row["primary"][0] + 100, row["primary"][1] + 100]) for row in SECTION_ROWS]
    job_store.upsert_job(task_id="t-second", status="completed", client_id=LOCAL_OWNER_ID, result={
        "display_segments": [{"start": 120.0, "end": 125.0, "text": "第二条任务的转录"}],
        "note_section_times": {"sections": later_rows},
    })
    chat = FakeChat()

    out = review_note.generate(note_path=str(second), chat=(chat, "x"))

    assert out["task_id"] == "t-second"
    assert "第二条任务的转录" in chat.calls[0][1] and "第一条任务的转录" not in chat.calls[0][1]
    review = folder / "课_复习笔记 (2).md"
    assert out["path"] == str(review)
    assert "[[课_笔记 (2)#一、为什么要扩容#（一）读太多|第一章（一）]] 2:00" in review.read_text(encoding="utf-8")
    assert "review_note" in job_store.get_job("t-second")["result"]
    assert "review_note" not in job_store.get_job("t-first")["result"]

    chat = FakeChat()
    revised = review_note.revise(str(review), "改一下", chat=(chat, "x"))
    assert revised["task_id"] == "t-second" and "〔S2 2:00〕" in chat.calls[0][1]


def _expanded_sample(table):
    text = "见，〔S1 0:40〕。在 [[#（一）读请求占九成]]〔S2〕，又见〔S3 1:00〕〔S1〕"
    expanded, unknown = review_note.expand_sources(text, table)
    assert not unknown
    return expanded


def test_a_link_goes_to_ids_and_back_letter_for_letter():
    table = review_note.section_table(BASE, STEM, SECTION_ROWS)
    expanded = _expanded_sample(table)
    assert "见，[[课_笔记#" in expanded  # the user's own comma stays

    collapsed, kept = review_note.collapse_sources(expanded, table)

    assert not kept and STEM not in collapsed
    assert review_note.expand_sources(collapsed, table)[0] == expanded


def test_a_heading_obsidian_cannot_follow_gets_words_and_time_and_still_goes_both_ways():
    base = BASE.replace("## 二、读写分离", "## 二、读|写 [分离] #1")
    table = review_note.section_table(base, STEM, SECTION_ROWS)
    expanded, _ = review_note.expand_sources("见〔S3 1:00〕，再看〔S1〕", table)

    assert "第二章 1:00" in expanded and "读|写" not in expanded
    assert expanded.count("[[") == 1  # only the chapter Obsidian can open
    collapsed, kept = review_note.collapse_sources(expanded, table)
    assert "〔S3 1:00〕" in collapsed and not kept
    assert review_note.expand_sources(collapsed, table)[0] == expanded


def test_a_link_to_a_renamed_heading_is_kept_as_it_was_and_not_shown_to_the_model(tmp_path):
    folder = _folder(tmp_path)
    review_note.generate(note_path=str(folder / f"{STEM}.md"), chat=(FakeChat(), "x"))
    review = folder / "课_复习笔记.md"
    old_link = f"[[{STEM}#零、早先的标题|第零章]] 0:05"
    review.write_text(review.read_text(encoding="utf-8").replace("**结论**：读太多。", f"**结论**：读太多。见{old_link}。"), encoding="utf-8")

    class Echo(FakeChat):
        def __call__(self, system, user):
            self.calls.append((system, user))
            return user.split("# 现在的复习笔记\n\n", 1)[1]

    chat = Echo()
    out = review_note.revise(str(review), "不改", chat=(chat, "x"))

    assert "零、早先的标题" not in chat.calls[0][1] and "〔L1〕" in chat.calls[0][1]
    assert f"见{old_link}。" in review.read_text(encoding="utf-8")
    assert out["lost_links"] == []


def test_a_citation_written_loosely_is_read_and_an_invented_one_is_not_written_as_real(tmp_path):
    table = review_note.section_table(BASE, STEM, SECTION_ROWS)
    text, unknown = review_note.expand_sources("〔S1，0:40〕 〔S2 0:20–0:30〕 〔S999〕 〔S2,第二段〕", table)

    assert f"[[{STEM}#一、为什么要扩容|第一章]] 0:40" in text
    assert f"第一章（一）]] 0:20" in text
    assert "S999" not in text and "第二段" not in text
    assert text.count(review_note.UNKNOWN_SOURCE) == 2
    assert unknown == ["〔S999〕", "〔S2,第二段〕"]

    folder = _folder(tmp_path)
    out = review_note.generate(note_path=str(folder / f"{STEM}.md"),
                               chat=(FakeChat(ANSWER.replace("〔S2〕", "〔S2〕、〔S999〕")), "x"))
    assert out["unknown_sources"] == ["〔S999〕"]


def test_only_a_follow_along_note_in_the_users_home_is_read(tmp_path):
    folder = _folder(tmp_path)
    elsewhere = Path(tmp_path).parent / f"{tmp_path.name}-outside"
    elsewhere.mkdir()
    (elsewhere / f"{STEM}.md").write_text(BASE, encoding="utf-8")
    (folder / "密码.md").write_text("不是笔记", encoding="utf-8")
    (folder / "链接_笔记.md").symlink_to(elsewhere / f"{STEM}.md")
    _task("t-review-8", folder / f"{STEM}.md")
    other = tmp_path / "别的" / "课"
    (other.parent).mkdir()
    shutil.copytree(folder, other, ignore=shutil.ignore_patterns("链接_笔记.md"))

    for raw, reason in [
        (elsewhere / f"{STEM}.md", "用户目录"),
        (folder / "链接_笔记.md", "用户目录"),  # a link out of the home
        (folder / "密码.md", "随看笔记"),
    ]:
        with pytest.raises(review_note.ReviewNoteError, match=reason):
            review_note.generate(note_path=str(raw), chat=(FakeChat(), "x"))
    with pytest.raises(review_note.ReviewNoteError, match="对不上"):
        review_note.generate("t-review-8", str(other / f"{STEM}.md"), chat=(FakeChat(), "x"))
    assert not list(elsewhere.glob("*复习笔记*")) and not (folder / "密码_复习笔记.md").exists()


def test_a_pdf_that_failed_is_reported_and_the_old_one_is_not_passed_off_as_new(tmp_path, monkeypatch):
    monkeypatch.undo()  # the real printing, with a browser that fails
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(note_pdf, "_browser", lambda: "/usr/bin/false")
    folder = _folder(tmp_path)
    old_pdf = folder / "课_复习笔记.pdf"
    old_pdf.write_bytes(b"%PDF old")

    out = review_note.generate(note_path=str(folder / f"{STEM}.md"), chat=(FakeChat(), "x"))

    assert out["pdf_path"] is None and "上一版" in out["pdf_error"] and "md 已写好" in out["pdf_error"]
    assert old_pdf.read_bytes() == b"%PDF old"
    assert not list(folder.glob("*.html")) and not list(folder.glob(".*printing*"))
    assert (folder / "课_复习笔记.md").is_file()


def test_the_earlier_review_note_is_kept_even_without_a_process_folder(tmp_path):
    folder = tmp_path / "课"
    folder.mkdir()
    (folder / f"{STEM}.md").write_text(BASE, encoding="utf-8")
    review_note.generate(note_path=str(folder / f"{STEM}.md"), chat=(FakeChat(), "x"))
    first = (folder / "课_复习笔记.md").read_text(encoding="utf-8")

    review_note.generate(note_path=str(folder / f"{STEM}.md"), chat=(FakeChat(ANSWER + "\n多一行"), "x"))

    kept = list((folder / "过程文件" / "历史版本").glob("课_复习笔记_*.md"))
    assert [path.read_text(encoding="utf-8") for path in kept] == [first]


def test_a_failed_write_says_why_and_leaves_the_old_note_whole(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "review-token")
    folder = _folder(tmp_path, task_id="t-review-9")
    _task("t-review-9", folder / f"{STEM}.md")
    review_note.generate("t-review-9", chat=(FakeChat(), "x"))
    first = (folder / "课_复习笔记.md").read_text(encoding="utf-8")

    def disk_full(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(review_note.os, "replace", disk_full)
    monkeypatch.setattr(review_note, "default_chat", lambda: (FakeChat(ANSWER + "\n新的一行"), "x"))
    failed = _client().post("/agent/v1/review-notes", headers=_HEADERS, json={"task_id": "t-review-9"})

    assert failed.status_code == 422 and "No space left" in failed.json()["detail"]["message"]
    assert (folder / "课_复习笔记.md").read_text(encoding="utf-8") == first
    assert not list(folder.glob(".课_复习笔记.*"))


def test_the_record_names_the_key_when_it_finished_what_the_subscription_began(tmp_path, monkeypatch):
    def broken(system, user):
        raise RuntimeError("usage limit")

    monkeypatch.setattr(claude_text, "preferred_channel", lambda: claude_text.CHANNEL_SUBSCRIPTION)
    monkeypatch.setattr(claude_text, "subscription_chat", lambda runner=None: (broken, "claude_code_subscription"))
    monkeypatch.setattr(claude_text, "claude_chat", lambda *a, **k: ((lambda s, u: ANSWER), "anthropic_api_key"))
    from backend.core import ai_summarizer

    monkeypatch.setattr(ai_summarizer, "text_model_chat", lambda **_: None)
    folder = _folder(tmp_path)

    out = review_note.generate(note_path=str(folder / f"{STEM}.md"))

    assert out["written_by"] == "anthropic_api_key"


def test_the_same_note_named_another_way_is_not_written_twice_at_once(tmp_path):
    folder = _folder(tmp_path)
    review_note._running.add(str((folder / f"{STEM}.md").resolve()))
    try:
        with pytest.raises(review_note.ReviewNoteError) as raised:
            review_note.generate(note_path=str(folder / "过程文件" / ".." / f"{STEM}.md"), chat=(FakeChat(), "x"))
    finally:
        review_note._running.clear()
    assert raised.value.code == "already_running"


def test_its_model_call_is_recorded_as_part_of_the_tasks_note(tmp_path):
    """The review note costs a model call like any other step's work: it is
    recorded against the task, in 写笔记 / 复习笔记 (model_usage)."""
    from backend.core import job_store, model_usage

    folder = _folder(tmp_path)
    _task("t-review-usage", folder / f"{STEM}.md")
    fake = FakeChat()

    def recorded(system, user):
        with model_usage.call("deepseek", "deepseek-flash") as call:
            call.input_tokens, call.output_tokens = 900, 300
            return fake(system, user)

    review_note.generate("t-review-usage", chat=(recorded, "x"))

    [row] = job_store.list_model_calls("t-review-usage")
    assert (row["step"], row["item"]) == ("note", "复习笔记")
