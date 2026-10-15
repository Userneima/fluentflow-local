"""The processing report shown on each task.

Requirements (owner, 2026-10-15):
1. Everything the cut did is in the report: short pauses counted together,
   every long gap (removed or kept for the picture), every stretch of talk
   taken out, quiet words saved from the cut.
2. A model organises it (groups, titles, what is folded, one sentence each);
   the times shown are always the program's, never the model's; whatever the
   model leaves out is still there, in a last folded group.
3. Without a model, or when it answers nonsense, the facts are grouped by kind.
4. What the user changed is said first, as theirs.
5. The user is asked to judge only a first group: what the model marked as
   unsure (with why) and what the rules always ask (removed talk, a single cut
   of a minute or more, a removal the program refused). The rest is only read.
"""

from __future__ import annotations

import pytest

from backend.core import cut_report

SEGMENTS = [
    {"start": 0.0, "end": 5.0, "text": "大家好我们开始"},
    {"start": 20.0, "end": 24.0, "text": "我来演示一下这个页面"},
    {"start": 60.0, "end": 64.0, "text": "能听到吗能听到"},
    {"start": 70.0, "end": 80.0, "text": "下面讲第二部分"},
]
RECORD = {
    "gaps": {
        "short": {"count": 120, "seconds": 95.0},
        "long": [
            {"start": 5.0, "end": 19.0, "seconds": 14.0, "picture": "画面静止", "kept": False, "reason": ""},
            {"start": 24.0, "end": 55.0, "seconds": 31.0, "picture": "画面一直在变", "kept": True, "reason": "在演示操作"},
        ],
    },
    "removed": [{"start": 60.0, "end": 64.0, "reason": "确认声音"}],
    "spoken": [{"start": 66.0, "end": 66.4}],
}


def _ids(report):
    return [item["ref"] for section in report["sections"] for item in section["items"]]


def test_every_thing_the_cut_did_is_a_fact():
    facts = cut_report.facts(RECORD, SEGMENTS)

    assert [f["id"] for f in facts] == ["short", "g1", "g2", "r1", "s1"]
    g2 = next(f for f in facts if f["id"] == "g2")
    assert g2["action"] == "kept" and g2["before"] == "我来演示一下这个页面"
    assert facts[0]["count"] == 120 and facts[0]["default"] == "3 秒以下的停顿，全部剪掉。"
    assert next(f for f in facts if f["id"] == "r1")["said"] == "能听到吗能听到"


def test_the_model_organises_but_times_are_the_programs_and_nothing_is_lost():
    answer = ('{"summary": "主要去掉了确认声音", "sections": ['
              '{"title": "去掉的说话", "folded": false, "items": [{"ref": "r1", "text": "确认能不能听到，去掉"}, {"ref": "zz", "text": "编的"}]},'
              '{"title": "保留的画面", "folded": false, "items": [{"ref": "g2", "text": "在演示", "start": 999}]},'
              '{"title": "零碎", "folded": true, "items": [{"ref": "short", "text": "让节奏更紧凑"}]}]}')

    report = cut_report.organize(cut_report.facts(RECORD, SEGMENTS), ((lambda s, u: answer), "fake"))

    assert report["summary"] == "主要去掉了确认声音" and report["organized_by"] == "fake"
    assert sorted(_ids(report)) == ["g1", "g2", "r1", "s1", "short"]
    first = report["sections"][0]["items"][0]
    assert first["text"] == "确认能不能听到，去掉" and first["start"] == 60.0
    assert report["sections"][1]["items"][0]["start"] == 24.0
    # A count is stated as the program's fact, not in the model's words.
    assert report["sections"][2]["items"][0]["text"] == "3 秒以下的停顿，全部剪掉。"
    last = report["sections"][-1]
    assert last["title"] == "其他处理" and last["folded"] is True and sorted(i["ref"] for i in last["items"]) == ["g1", "s1"]


@pytest.mark.parametrize("chat", [None, ((lambda s, u: "不是 JSON"), "fake"), ((lambda s, u: (_ for _ in ()).throw(RuntimeError())), "fake")])
def test_without_a_useful_model_the_facts_are_grouped_by_kind(chat):
    report = cut_report.organize(cut_report.facts(RECORD, SEGMENTS), chat)

    titles = [s["title"] for s in report["sections"]]
    assert titles == ["需要你判断", "因为画面保留的空白", "剪掉的长空白", "保住的轻声说话", "短停顿"]
    assert [s["folded"] for s in report["sections"]] == [False, False, True, True, True]
    assert sorted(_ids(report)) == ["g1", "g2", "r1", "s1", "short"]


def test_what_the_user_put_back_is_listed_first_as_theirs():
    record = {**RECORD, "removed": [], "user_changes": [
        {"kind": "removed_talk", "start": 60.0, "end": 64.0, "model_said": "removed", "user_said": "kept"},
    ]}

    report = cut_report.organize(cut_report.facts(record, SEGMENTS), None)

    titles = [section["title"] for section in report["sections"]]
    assert titles[0] == "你改过的" and report["sections"][0]["items"][0]["text"] == "你说这段不该剪，已放回。"


def test_only_doubts_and_the_rules_put_items_to_the_user():
    record = {**RECORD, "gaps": {"short": {"count": 1, "seconds": 1.0}, "long": [
        {"start": 5.0, "end": 19.0, "seconds": 14.0, "picture": "画面一直在变", "kept": False, "reason": ""},
        {"start": 100.0, "end": 176.0, "seconds": 76.0, "picture": "画面静止", "kept": False, "reason": ""},
        {"start": 180.0, "end": 190.0, "seconds": 10.0, "picture": "画面静止", "kept": False, "reason": ""},
    ]}}
    answer = ('{"summary": "", "sections": [{"title": "长空白", "folded": false, "items": ['
              '{"ref": "g1", "text": "剪掉", "unsure": true, "doubt": "画面在变，可能是在演示"},'
              '{"ref": "g2", "text": "剪掉"}, {"ref": "g3", "text": "剪掉", "unsure": false}]}]}')

    report = cut_report.organize(cut_report.facts(record, SEGMENTS), ((lambda s, u: answer), "fake"))

    judge = report["sections"][0]
    assert judge["title"] == "需要你判断" and judge["judge"] is True
    doubts = {item["ref"]: item["doubt"] for item in judge["items"]}
    assert doubts["g1"] == "画面在变，可能是在演示"
    assert "1 分" in doubts["g2"]
    assert "r1" in doubts and "g3" not in doubts
    assert all(not s.get("judge") for s in report["sections"][1:])


def test_a_gap_cut_together_with_a_removed_stretch_is_listed_once():
    record = {**RECORD, "gaps": {"short": {}, "long": [
        {"start": 50.0, "end": 60.2, "seconds": 10.2, "picture": "画面静止", "kept": False, "reason": ""},
        {"start": 80.0, "end": 90.0, "seconds": 10.0, "picture": "画面静止", "kept": False, "reason": ""},
    ]}}

    ids = [fact["id"] for fact in cut_report.facts(record, SEGMENTS)]

    assert "r1" in ids and "g2" in ids and "g1" not in ids
