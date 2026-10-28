"""The user's own agent writes the task's note; FluentFlow needs no model key.

What has to hold, written from the requirement rather than the code:

- With no Anthropic or text-model key configured, an agent can still get the
  material for a finished task and save a note for it.
- The material is what the frame note would send: the transcript on the clock of
  the file the note describes, the frames from that file as paths the agent can
  open, and the note skill followed by the fixed contract.
- Saving makes it the task's note and the old note can be put back for free.
- Which frames the note cites is measured from the markdown, and the note says an
  agent wrote it.
- A draft too short for its transcript, or the wrong number of parts, is refused
  and the task's note is left as it was.
- Asking for the material twice does not extract the frames again: the note the
  task has may cite them.
"""

from __future__ import annotations

import pytest

import backend.core.agent_note as an
import backend.core.visual_note_job as vn
from backend.core import note_skills
from backend.core.result_artifacts import artifact_target_path
from backend.core.result_schema import NOTE_FROM_AGENT
from test_visual_note_job import TASK, _fake_extractor, job_store, no_ambient_key  # noqa: F401

# Inside the fixture's kept span (0-10s and 20-100s), long enough that the check applies.
LONG_SEGMENTS = [
    {"start": float(at), "end": float(at + 8), "text": "这一句讲的是最小二乘法的推导过程和它的前提条件，" * 8}
    for at in (0, 25, 40, 55, 70, 85)
]


def _material(tmp_path, **kwargs):
    return an.material(TASK, client_id="anonymous", keyframe_extractor=_fake_extractor(tmp_path), **kwargs)


def test_material_needs_no_key_and_carries_transcript_frames_and_rules(job_store, tmp_path):
    material = _material(tmp_path)

    assert material["total_parts"] == 1
    # 65.0 in the recording is 55.0 in the cut file the note describes.
    assert "[00:55] 看这张幻灯片上的公式" in material["transcript"]
    assert "这一句整段都是被剪掉的空白" not in material["transcript"]
    assert material["frames"], "a video task hands over its frames"
    for frame in material["frames"]:
        assert artifact_target_path(TASK, f"frames/{frame['filename']}").is_file()
        assert frame["path"].endswith(frame["filename"])
    rules = note_skills.load_note_skill().rules.strip()
    assert material["instructions"].startswith(rules)
    assert "固定要求" in material["instructions"]
    assert "save_agent_note" in material["instructions"]


def test_asking_twice_does_not_extract_the_frames_again(job_store, tmp_path):
    first = _material(tmp_path)
    calls = []

    def must_not_run(*args, **kwargs):
        calls.append(args)
        return []

    second = an.material(TASK, client_id="anonymous", keyframe_extractor=must_not_run)

    assert calls == []
    assert [f["filename"] for f in second["frames"]] == [f["filename"] for f in first["frames"]]


def test_an_unfinished_task_is_refused_with_a_reason(job_store, tmp_path):
    job_store["status"] = "processing"

    with pytest.raises(an.AgentNoteError, match="只有已完成的任务"):
        _material(tmp_path)


def test_a_part_out_of_range_is_refused(job_store, tmp_path):
    with pytest.raises(an.AgentNoteError, match="1 到 1"):
        _material(tmp_path, part=2)


def test_saving_makes_it_the_note_and_the_old_one_comes_back(job_store, tmp_path):
    material = _material(tmp_path)
    cited = material["frames"][0]["filename"]

    updated = an.save(
        TASK,
        note_markdown=f"# 最小二乘法\n\n![幻灯片上的公式]({cited})\n\n正文。",
        basis_note="公式来自截图",
        model="claude-opus-5-5",
        client_id="anonymous",
    )

    result = updated["result"]
    assert result["summary_markdown"].startswith("# 最小二乘法")
    assert cited not in result["summary_markdown"].split("](")[0], "image target is rewritten to a served URL"
    assert result["summary_written_from"] == NOTE_FROM_AGENT
    state = vn.visual_note_state(result)
    assert state["channel"] == an.CHANNEL_AGENT
    assert state["model"] == "claude-opus-5-5"
    assert state["basis"] == vn.BASIS_BOTH
    assert [f["filename"] for f in state["frames_cited"]] == [cited]

    restored = vn.restore_previous_note(TASK, client_id="anonymous")
    assert restored["result"]["summary_markdown"] == "原来的文字笔记"


def test_the_note_says_an_agent_replaced_the_previous_one_and_a_restore_says_so_too(job_store, tmp_path):
    # Owner, 2026-10-22: every kept version of a note says what wrote over it.
    from backend.core import note_versions

    _material(tmp_path)
    updated = an.save(TASK, note_markdown="# 笔记\n\n正文。", client_id="anonymous")
    assert note_versions.changed_by(updated["result"]) == note_versions.AGENT_NOTE

    restored = vn.restore_previous_note(TASK, client_id="anonymous")
    assert note_versions.changed_by(restored["result"]) == note_versions.RESTORE


def test_a_frame_the_agent_made_up_does_not_count_as_a_picture(job_store, tmp_path):
    _material(tmp_path)

    updated = an.save(TASK, note_markdown="# 笔记\n\n![编的](note_9999.jpg)\n\n正文。", client_id="anonymous")

    assert vn.visual_note_state(updated["result"])["basis"] == vn.BASIS_TRANSCRIPT_ONLY


def test_a_draft_too_short_for_its_transcript_is_refused_and_nothing_changes(job_store, tmp_path):
    job_store["result"]["display_segments"] = LONG_SEGMENTS

    with pytest.raises(an.AgentNoteError, match="没有替换现有笔记"):
        an.save(TASK, note_markdown="# 太短\n\n一句话。", client_id="anonymous")

    assert job_store["result"]["summary_markdown"] == "原来的文字笔记"


def test_the_wrong_number_of_parts_is_refused(job_store, tmp_path):
    with pytest.raises(an.AgentNoteError, match="没有收到笔记内容"):
        an.save(TASK, client_id="anonymous")
    with pytest.raises(an.AgentNoteError):
        an.save(TASK, part_notes=["# 一", "# 二"], client_id="anonymous")

    assert job_store["result"]["summary_markdown"] == "原来的文字笔记"


def test_mcp_exposes_both_tools():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import fluentflow_mcp_server as mcp

    names = {tool["name"] for tool in mcp.TOOL_DEFINITIONS}
    assert {"get_agent_note_material", "save_agent_note"} <= names
    assert set(mcp.TOOL_FUNCTIONS) == names


def test_a_finished_task_without_a_note_points_the_agent_at_writing_it():
    from backend.core.local_agent_package import _local_extra_next_actions

    without = {"task_id": "t1", "status": "completed", "result": {"summary_markdown": "", "summary_status": "skipped"}}
    with_note = {"task_id": "t1", "status": "completed", "result": {"summary_markdown": "# 笔记", "summary_status": "completed"}}
    writing = {"task_id": "t1", "status": "completed", "result": {"summary_markdown": "", "summary_status": "pending"}}

    assert [a["action"] for a in _local_extra_next_actions(without, {})] == ["get_agent_note_material"]
    assert _local_extra_next_actions(with_note, {}) == []
    assert _local_extra_next_actions(writing, {}) == []


def test_mcp_server_reads_the_token_the_installer_wrote(monkeypatch, tmp_path):
    """Registering the MCP server needs no secret: it reads the repository .env."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import fluentflow_mcp_server as mcp

    (tmp_path / ".env").write_text("FOO=1\nFLUENTFLOW_ACCESS_TOKEN=abc123\n", encoding="utf-8")
    monkeypatch.setattr(mcp, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("FLUENTFLOW_ACCESS_TOKEN", raising=False)
    mcp._token_from_project_env()
    import os
    assert os.environ["FLUENTFLOW_ACCESS_TOKEN"] == "abc123"

    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "from-client-config")
    mcp._token_from_project_env()
    assert os.environ["FLUENTFLOW_ACCESS_TOKEN"] == "from-client-config", "the client's own value wins"


def test_an_edited_note_keeps_counting_the_frames_it_already_cited(job_store, tmp_path):
    """An agent fixing the task's own note hands back the served frame URLs it already had."""
    material = _material(tmp_path)
    name = material["frames"][0]["filename"]

    updated = an.save(
        TASK, note_markdown=f"# 笔记\n\n![公式](/jobs/{TASK}/artifacts/frame?file={name})\n\n正文。", client_id="anonymous",
    )

    state = vn.visual_note_state(updated["result"])
    assert [f["filename"] for f in state["frames_cited"]] == [name]


def test_a_task_with_only_a_transcript_gets_a_note_from_the_transcript(job_store, tmp_path):
    """YouTube captions, a subtitle file, a pasted transcript: no recording to read, still a note."""
    job_store["result"].pop("debreath")
    job_store["result"]["artifacts"] = {}

    material = _material(tmp_path)
    assert material["has_video"] is False and material["frames"] == []
    assert "[01:05] 看这张幻灯片上的公式" in material["transcript"], "the transcript's own clock"

    updated = an.save(TASK, note_markdown="# 最小二乘法\n\n正文。", client_id="anonymous")
    state = vn.visual_note_state(updated["result"])
    assert updated["result"]["summary_written_from"] == NOTE_FROM_AGENT
    assert state["basis"] == vn.BASIS_TRANSCRIPT_ONLY
    assert state["media_source"]["kind"] == "transcript_only"


def test_default_save_never_resolves_a_background_checker(job_store, tmp_path, monkeypatch):
    _material(tmp_path)
    monkeypatch.setattr(vn, "coverage_checker", lambda *_a, **_k: pytest.fail("unexpected model check"))
    saved = an.save(TASK, note_markdown="# 笔记\n\n正文。", client_id="anonymous")
    assert vn.visual_note_state(saved["result"])["coverage"]["parts"][0]["checked"] is False


def test_plain_transcript_without_media_can_be_read_and_saved(job_store, monkeypatch):
    monkeypatch.setattr(vn, "cut_media", lambda *_: None)
    job_store["result"] = {"transcript_text": "研究问题应当明确。", "summary_markdown": ""}
    material = an.material(TASK, client_id="anonymous", keyframe_extractor=lambda *_a, **_k: pytest.fail("no media to extract"))
    assert material["transcript"] == "研究问题应当明确。"
    assert material["frames"] == [] and material["has_video"] is False
    saved = an.save(TASK, note_markdown="# 研究问题\n\n研究问题应当明确。", client_id="anonymous",
                    base_version=material["note_version"])
    assert saved["result"]["summary_written_from"] == NOTE_FROM_AGENT


def test_the_note_step_is_timed_from_the_material_to_the_save(job_store, tmp_path):
    # The task page's time for the note (task_steps.timings): an agent's note
    # runs from its read of the material to its save, and then reads done.
    from backend.core import job_store as store, task_steps

    task_steps.plan(TASK)
    task_steps.finish(TASK, task_steps.NOTE, task_steps.SKIPPED)
    material = _material(tmp_path)
    task_steps_after_read = task_steps.steps_of([TASK])[TASK]
    note = next(s for s in task_steps_after_read if s["step_type"].endswith(":note"))
    assert note["result"][task_steps.AGENT_STARTED]
    an.save(TASK, note_markdown="# 笔记\n\n" + "正文。" * 20, client_id="anonymous",
            base_version=material["note_version"])
    rows = task_steps.timings(store.get_job(TASK) or {}, task_steps.steps_of([TASK])[TASK])["rows"]
    (row,) = [row for row in rows if row["step"] == "note"]
    assert row["by_agent"] is True and row["status"] == task_steps.DONE and row["running"] is False
