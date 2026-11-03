"""Pointers where the note leaves the order of the recording (backend/core/note_pointers.py).

What the reader needs (王愉超, 2026-10-19 and 10-23): reading the note along
with the video, at the place the video reaches content the note put in a far
chapter, a callout names that chapter and links to it; content found by
reading on a little gets none; a hand-written pointer is not doubled; one he
deletes does not come back; adding one never sends the note to be aligned
again.
"""

from __future__ import annotations

import uuid

import pytest

from backend.core import job_store, note_pointers, note_section_times, note_versions
from backend.core.local_request_scope import LOCAL_OWNER_ID

SEGMENTS = [{"start": float(i * 10), "end": float(i * 10 + 10), "text": f"第{i}句"} for i in range(100)]


def _note(*chapters: str, tail: str = "") -> str:
    parts = ["# 访谈", ""]
    for chapter in chapters:
        parts += [f"## {chapter}", "", f"{chapter}的正文。", ""]
    return "\n".join(parts) + tail


def _table(markdown: str, spans: dict[str, tuple]) -> dict:
    """A section times table: heading -> (start, end) or (start, end, others)."""
    rows = []
    for section in note_section_times.note_sections(markdown):
        span = spans[section["heading"]]
        rows.append({
            "id": uuid.uuid4().hex[:8], "heading": section["heading"], "level": section["level"],
            "body_hash": section["body_hash"], "primary": [float(span[0]), float(span[1])],
            "others": [list(map(float, o)) for o in (span[2] if len(span) > 2 else [])],
        })
    return {"timeline": "debreath_media", "cut_list_updated_at": None, "sections": rows}


MOVED = _note("一、开场", "二、第一个话题", "三、第二个话题", "四、第三个话题", "五、挪到后面的话题")
MOVED_SPANS = {
    "一、开场": (0, 100), "二、第一个话题": (100, 200), "五、挪到后面的话题": (200, 400),
    "三、第二个话题": (400, 500), "四、第三个话题": (500, 600),
}


def test_content_moved_far_down_gets_a_pointer_where_the_video_reaches_it():
    note, added = note_pointers.with_pointers(MOVED, _table(MOVED, MOVED_SPANS))

    assert len(added) == 1
    pointer = "> [!tip] 视频 3:20 起讲的「挪到后面的话题」在[[#五、挪到后面的话题|第五章]]。"
    # At the end of the chapter being read when the video gets there.
    assert f"二、第一个话题的正文。\n\n{pointer}\n\n## 三、第二个话题" in note
    assert note.count("[!tip]") == 1


def test_content_found_by_reading_on_a_little_gets_none():
    note = _note("一、开场", "二、后讲的", "三、先讲的")
    spans = {"一、开场": (0, 100), "三、先讲的": (100, 200), "二、后讲的": (200, 300)}

    assert note_pointers.with_pointers(note, _table(note, spans)) == (note, [])


def test_the_video_coming_back_to_a_passed_chapter_gets_a_pointer_a_passing_mention_none():
    note = _note("一、训练", "二、组织", "三、团队", "四、管理", "五、结尾")
    back = {
        "一、训练": (0, 100, [(410, 500)]), "二、组织": (100, 200), "三、团队": (200, 300),
        "四、管理": (300, 410), "五、结尾": (500, 600),
    }
    passing = {**back, "一、训练": (0, 100, [(410, 440)]), "四、管理": (300, 410)}

    pointed, added = note_pointers.with_pointers(note, _table(note, back))
    assert len(added) == 1
    assert "四、管理的正文。\n\n> [!tip] 视频 6:50 起讲的「训练」在[[#一、训练|第一章]]。" in pointed
    assert note_pointers.with_pointers(note, _table(note, passing))[1] == []


def test_a_sub_section_is_named_by_chapter_and_linked_through_it():
    note = "\n".join([
        "## 一、开场", "", "开场。", "",
        "## 二、话题", "", "话题。", "",
        "## 三、另一章", "", "### （一）主线", "", "主线。", "", "### （二）支线", "", "支线。", "",
        "### （三）挪上来的", "", "挪上来的。", "",
        "## 四、也有主线", "", "### （一）主线", "", "同名的小节。", "",
    ])
    spans = {
        "一、开场": (0, 100), "二、话题": (100, 200), "三、另一章": (100, 500), "（一）主线": (200, 300),
        "（二）支线": (300, 400), "（三）挪上来的": (400, 500), "四、也有主线": (500, 600),
    }
    table = _table(note, spans)
    # The video: 一, then 四's （一）, then 二 and 三 in order.
    for row in table["sections"]:
        if row["heading"] == "（一）主线" and row["body_hash"] == note_section_times.body_hash("同名的小节。"):
            row["primary"] = [100.0, 200.0]
    for row in table["sections"]:
        if row["heading"] == "二、话题":
            row["primary"] = [200.0, 250.0]
        if row["heading"] == "四、也有主线":
            row["primary"] = [100.0, 200.0]

    pointed, added = note_pointers.with_pointers(note, table)

    assert len(added) == 1
    # （一） repeats across chapters: the link goes through the chapter.
    assert "开场。\n\n> [!tip] 视频 1:40 起讲的「主线」在[[#四、也有主线#（一）主线|第四章（一）]]。" in pointed


def test_closing_sections_gather_from_everywhere_and_point_nowhere():
    note = _note("一、开场", "二、话题", "三、另一个", "决定", "待办", "出处")
    spans = {
        "一、开场": (0, 100), "二、话题": (100, 200), "三、另一个": (200, 300),
        "决定": (50, 150, [(210, 290)]), "待办": (120, 180, [(10, 90)]), "出处": (0, 5),
    }

    assert note_pointers.with_pointers(note, _table(note, spans))[1] == []


def test_a_hand_written_pointer_is_not_doubled():
    note = MOVED.replace(
        "二、第一个话题的正文。\n",
        "二、第一个话题的正文。\n\n> [!tip] 这里视频讲的话题在[[#五、挪到后面的话题|第五章]]。\n",
    )

    assert note_pointers.with_pointers(note, _table(note, MOVED_SPANS)) == (note, [])


def test_a_pointer_the_user_deleted_is_not_written_again():
    table = _table(MOVED, MOVED_SPANS)
    _, added = note_pointers.with_pointers(MOVED, table)

    assert note_pointers.with_pointers(MOVED, table, written=added) == (MOVED, [])


def test_a_pointer_leaves_every_section_s_hash_as_it_was():
    note, _ = note_pointers.with_pointers(MOVED, _table(MOVED, MOVED_SPANS))

    assert note_section_times.note_sections(note) == note_section_times.note_sections(MOVED)


def test_a_pointer_goes_before_the_part_marker_closing_the_section():
    note = MOVED.replace("## 三、第二个话题", "<!-- 第 2/2 部分（03:20–10:00） -->\n\n## 三、第二个话题")

    pointed, _ = note_pointers.with_pointers(note, _table(note, MOVED_SPANS))

    assert "二、第一个话题的正文。\n\n> [!tip] 视频 3:20" in pointed
    assert "第五章]]。\n\n<!-- 第 2/2 部分" in pointed


_CREATED: list[str] = []


@pytest.fixture
def task():
    task_id = f"ptr-{uuid.uuid4().hex[:8]}"
    _CREATED.append(task_id)
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, result={
        "task_id": task_id, "summary_markdown": MOVED, "summary_status": "completed",
        "display_segments": SEGMENTS, "playback_media_kind": "debreath_media", "debreath": {},
    })
    yield task_id
    job_store.delete_jobs(_CREATED)
    _CREATED.clear()


class Model:
    """Answers each tagged heading with its time in MOVED_SPANS."""

    def __init__(self):
        self.calls = 0

    def __call__(self, system: str, prompt: str) -> str:
        import json
        import re

        self.calls += 1
        note = prompt.split("=== 笔记 ===")[1].split("=== 转录稿 ===")[0]
        rows = []
        for line in note.splitlines():
            found = re.search(r"^## (.*?)  〔(S\d+)〕", line)
            if found:
                start, end = MOVED_SPANS[found.group(1)]
                clock = lambda s: f"0:{s // 60:02d}:{s % 60:02d}"  # noqa: E731
                rows.append({"id": found.group(2), "primary": {"start": clock(start), "end": clock(end)}, "others": []})
        return json.dumps({"sections": rows}, ensure_ascii=False)


def test_once_the_times_are_matched_the_note_gets_its_pointers_and_costs_no_second_match(task):
    model = Model()

    answer = note_section_times.align(task, chat=(model, "claude_code_subscription", "opus"))

    result = job_store.get_job(task)["result"]
    assert answer["pointers"] == 1
    assert "[[#五、挪到后面的话题|第五章]]" in result["summary_markdown"]
    assert note_versions.changed_by(result) == note_versions.POINTERS
    # The pointer is not a change to the sections: nothing left to match.
    assert note_section_times.missing_sections(result) == ([], False)
    assert note_section_times.align(task, chat=(model, "claude_code_subscription", "opus"))["state"] == "unchanged"
    assert model.calls == 1


def test_deleting_the_pointer_in_the_note_keeps_it_deleted(task):
    model = Model()
    note_section_times.align(task, chat=(model, "claude_code_subscription", "opus"))
    job = job_store.get_job(task)
    job_store.save_result_changes(task, job["result"], {**job["result"], "summary_markdown": MOVED})

    assert note_pointers.add_to_task(task) == {"added": 0}
    assert job_store.get_job(task)["result"]["summary_markdown"] == MOVED


def test_a_sub_section_heading_used_once_is_linked_by_itself():
    note = "\n".join([
        "## 一、开场", "", "开场。", "", "## 二、话题", "", "话题。", "", "## 三、中间", "", "中间。", "",
        "## 四、后面", "", "### （一）挪下来的", "", "挪下来的。", "",
    ])
    spans = {"一、开场": (0, 100), "（一）挪下来的": (100, 200), "二、话题": (200, 300), "三、中间": (300, 400),
             "四、后面": (100, 200)}

    pointed, _ = note_pointers.with_pointers(note, _table(note, spans))

    assert "在[[#（一）挪下来的|第四章（一）]]。" in pointed


def test_feishu_resolves_a_link_through_a_chapter_to_the_sub_section():
    from backend.core import feishu_note

    converted = feishu_note.to_feishu("见[[#四、也有主线#（一）主线|第四章（一）]]。")

    assert [(link.heading, link.label) for link in converted.links] == [("（一）主线", "第四章（一）")]
