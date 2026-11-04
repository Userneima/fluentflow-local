"""Size budgets for what agents read, so the tools do not grow back.

The MCP tool audit (docs/方案/2026-11-04-MCP工具体检.md, decisions 8.17,
8.18, 8.20) found every oversized answer on long recordings only: a 3.5-hour
interview's task status carried a 15,000-character table, a corrections list
reached 92,950 characters and Claude Code saved it to a file. Tests on short
clips never reach those sizes, so this one builds a recording as long as the
largest real one and reads it through the tools.

When a budget fails, shorten what grew first. Raise a number only with the
reason in a comment beside it, and record it in the decision log.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import job_store, note_section_times as times
from backend.core.local_request_scope import LOCAL_OWNER_ID as OWNER
from backend.routers.local_agent import router
from scripts import fluentflow_mcp_server as mcp

# Claude Code keeps a tool result inline up to 50,000 characters and saves a
# longer one to a file (measured 2026-11-04, decision 8.17). A tool may
# declare a larger anthropic/maxResultSizeChars; then that is its limit.
HOST_INLINE_CHARS = 50_000

# What every agent pays to load the tool list, and the longest single tool.
# Measured after the audit: 52,499 in all, submit_video_link 3,251.
ALL_DEFINITIONS_CHARS = 55_000
ONE_DEFINITION_CHARS = 3_500

# The largest real task on record (2026-11-04): a 3.5-hour interview, a note of
# 45,703 characters in 113 sections, and a series with 244 word-list fixes,
# half of them reverted, and a cut report with 194 folded items.
SECTIONS = 113
SECTION_BODY = "这一节讲的是训练和推理之间的取舍，以及卡的分配比例怎么定。" * 13
SENTENCES = 2_600
GLOSSARY_FIXES = 244
SUBTITLE_FIXES = 200
FOLDED_ITEMS = 194


def _definition_chars(definition: dict) -> int:
    return len(json.dumps(definition, ensure_ascii=False))


def test_the_tool_list_stays_within_its_budget():
    sizes = {d["name"]: _definition_chars(d) for d in mcp.TOOL_DEFINITIONS}
    too_long = {name: size for name, size in sizes.items() if size > ONE_DEFINITION_CHARS}
    assert not too_long, f"tool definitions over {ONE_DEFINITION_CHARS} characters: {too_long}"
    total = sum(sizes.values())
    assert total <= ALL_DEFINITIONS_CHARS, f"all tool definitions: {total} > {ALL_DEFINITIONS_CHARS}"


def _note() -> str:
    parts = ["# 三个半小时的访谈"]
    for i in range(SECTIONS):
        parts.append(f"## {i + 1}. 第 {i + 1} 节\n{SECTION_BODY}")
    return "\n\n".join(parts)


def _long_result(task_id: str) -> dict:
    segments = [
        {"start": i * 4.8, "end": i * 4.8 + 4.5, "text": f"第{i}句，讲到智能体框架和后训练的关系。"}
        for i in range(SENTENCES)
    ]
    line = "- 黑客级竞品调研：本期训练营的名字。转录常听成：黑客级镜头调研、性格级"
    glossary = [
        {"id": f"g{i}", "at": i % 40, "start": i * 30.0, "end": i * 30.0 + 2, "from": "镜头", "to": "竞品",
         "basis": "glossary", "entry": line, "why": "训练营名称是黑客级竞品调研",
         "sentence_before": "要做黑客级镜头调研的启动", "sentence_after": "要做黑客级竞品调研的启动",
         "segment_index": i * 10, "source_start": i * 30.0, "source_end": i * 30.0 + 2, "reverted": i % 2 == 0}
        for i in range(GLOSSARY_FIXES)
    ]
    subtitles = [
        {"id": f"s{i}", "start": i * 40.0, "end": i * 40.0 + 3, "from": "GOM", "to": "GLM-5",
         "line": "我们用 GLM-5 跑了一遍评测", "at": i, "reverted": False}
        for i in range(SUBTITLE_FIXES)
    ]
    report = {
        "summary": "剪掉了长停顿和试音，保住了说得很轻的几句。",
        "sections": [
            {"title": "需要你判断", "judge": True, "folded": False,
             "items": [{"ref": f"j{i}", "key": f"k{i}", "kind": "gap", "action": "removed",
                        "text": "这段停顿里有一句说得很轻的话，剪掉了。", "start": i * 60.0, "end": i * 60.0 + 8}
                       for i in range(9)]},
            {"title": "其他处理", "folded": True,
             "items": [{"ref": f"o{i}", "key": f"ko{i}", "kind": "gap", "action": "removed",
                        "text": "一段 1.2 秒的停顿，前后没有话，剪掉。" * 3, "start": i * 50.0, "end": i * 50.0 + 1.2}
                       for i in range(FOLDED_ITEMS)]},
        ],
    }
    result = {
        "task_id": task_id, "summary_status": "completed", "summary_markdown": _note(),
        "raw_segments": segments, "display_segments": segments,
        "glossary_corrections": glossary, "subtitle_corrections": subtitles,
        "debreath": {"cut_list_updated_at": "cut-1", "survey": {"report": report}},
        "playback_media_kind": "debreath_media",
    }
    sections = times.note_sections(result["summary_markdown"])
    spans = {s["index"]: {"primary": [s["index"] * 90.0, s["index"] * 90.0 + 80], "others": [[1.0, 2.0]]}
             for s in sections}
    result[times.RESULT_KEY] = times.alignment_table(result, sections, spans, whole=True)
    return result


@pytest.fixture
def long_task(monkeypatch):
    monkeypatch.setattr(times, "_enabled", False)
    task_id = uuid.uuid4().hex
    result = _long_result(task_id)
    assert len(result["summary_markdown"]) > 40_000 and len(result[times.RESULT_KEY]["sections"]) == SECTIONS
    job_store.upsert_job(task_id=task_id, status="completed", client_id=OWNER, result=result)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "budget-token")
    headers = {"x-fluentflow-access-token": "budget-token"}

    def bridge(method, path, **kw):
        response = client.request(method, path, json=kw.get("payload"), headers=headers)
        assert response.status_code < 400, (path, response.text[:300])
        return response.json()

    monkeypatch.setattr(mcp, "_agent_request", bridge)
    yield task_id
    job_store.delete_jobs([task_id])


# The read tools whose answer grows with the recording, and the arguments
# that read the long task. A new tool like them belongs here.
READS = [
    ("get_task", {}),
    ("get_task_package", {}),
    ("list_transcript_corrections", {"source": "glossary"}),
    ("list_transcript_corrections", {"source": "subtitles"}),
    ("get_cut_report", {}),
]


@pytest.mark.parametrize(("name", "arguments"), READS, ids=[f"{n}:{a.get('source', '')}" for n, a in READS])
def test_a_long_recording_fits_in_one_result(long_task, name, arguments):
    definition = next(d for d in mcp.TOOL_DEFINITIONS if d["name"] == name)
    limit = (definition.get("_meta") or {}).get("anthropic/maxResultSizeChars") or HOST_INLINE_CHARS
    answer = mcp._call_tool(name, {"task_id": long_task, **arguments})
    assert not answer.get("isError"), answer["content"][0]["text"][:300]
    size = len(answer["content"][0]["text"])
    assert size <= limit, f"{name} {arguments}: {size} characters > {limit}"


def test_task_status_stays_light_on_a_long_recording(long_task):
    # get_task is the lookup agents call most (107 times on record); it is
    # meant as light status, so its budget is far below the host's limit.
    size = len(mcp._call_tool("get_task", {"task_id": long_task})["content"][0]["text"])
    assert size <= 6_000, f"get_task: {size} characters"
