"""The processing report shown on each task.

Requirements (owner, 2026-10-15):
1. Everything the cut did is in the report: short pauses counted together,
   every long gap (removed or kept for the picture), every stretch of talk
   taken out, quiet words saved from the cut.
2. A model organises it (groups, titles, what is folded, one sentence each);
   the times shown are always the program's, never the model's; whatever the
   model leaves out is still there, in a last folded group.
3. Without a model, or when it answers nonsense, the facts are grouped by kind.
4. The user can leave an opinion on one item or on the whole report.
"""

from __future__ import annotations

import uuid

import pytest

from backend.core import cut_report, job_store, survey_cut

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
    assert "120 处" in facts[0]["default"]
    assert next(f for f in facts if f["id"] == "r1")["said"] == "能听到吗能听到"


def test_the_model_organises_but_times_are_the_programs_and_nothing_is_lost():
    answer = ('{"summary": "主要去掉了确认声音", "sections": ['
              '{"title": "去掉的说话", "folded": false, "items": [{"ref": "r1", "text": "确认能不能听到，去掉"}, {"ref": "zz", "text": "编的"}]},'
              '{"title": "保留的画面", "folded": false, "items": [{"ref": "g2", "text": "在演示", "start": 999}]}]}')

    report = cut_report.organize(cut_report.facts(RECORD, SEGMENTS), ((lambda s, u: answer), "fake"))

    assert report["summary"] == "主要去掉了确认声音" and report["organized_by"] == "fake"
    assert sorted(_ids(report)) == ["g1", "g2", "r1", "s1", "short"]
    first = report["sections"][0]["items"][0]
    assert first["text"] == "确认能不能听到，去掉" and first["start"] == 60.0
    assert report["sections"][1]["items"][0]["start"] == 24.0
    last = report["sections"][-1]
    assert last["title"] == "其他处理" and last["folded"] is True and sorted(i["ref"] for i in last["items"]) == ["g1", "s1", "short"]


@pytest.mark.parametrize("chat", [None, ((lambda s, u: "不是 JSON"), "fake"), ((lambda s, u: (_ for _ in ()).throw(RuntimeError())), "fake")])
def test_without_a_useful_model_the_facts_are_grouped_by_kind(chat):
    report = cut_report.organize(cut_report.facts(RECORD, SEGMENTS), chat)

    titles = [s["title"] for s in report["sections"]]
    assert titles == ["去掉的说话", "因为画面保留的空白", "剪掉的长空白", "保住的轻声说话", "短停顿"]
    assert [s["folded"] for s in report["sections"]] == [False, False, True, True, True]
    assert sorted(_ids(report)) == ["g1", "g2", "r1", "s1", "short"]


def test_opinions_are_kept_on_one_item_or_the_whole():
    task_id = f"cr-{uuid.uuid4().hex[:8]}"
    job_store.upsert_job(task_id=task_id, status="completed", result={
        "debreath": {"survey": {"report": {"sections": []}}},
    })

    survey_cut.feedback(task_id, "这段其实在讲重点", ref="g1")
    survey_cut.feedback(task_id, "整体剪得有点狠")
    with pytest.raises(survey_cut.ReviewError):
        survey_cut.feedback(task_id, "  ")

    entries = job_store.get_job(task_id)["result"]["debreath"]["survey"]["feedback"]
    assert [(e["ref"], e["text"]) for e in entries] == [("g1", "这段其实在讲重点"), (None, "整体剪得有点狠")]
