"""The task's note, written from the de-breathed version of its recording.

The flow is: cut the breath gaps first, then write the note from the file that
came out. Four things are guarded here, and each of them is something that would
be invisible to a user if it broke.

First, the media. Frames must come from the cut file, and a task with no cut file
must be refused with its first step named — never served by quietly reading the
original recording, because then the note describes a file the user does not
have.

Second, the clock. The transcript's timestamps belong to the recording before it
was shortened, so they go through the cut list. A line whose audio was removed is
dropped and counted rather than left pointing at somebody else's sentence, and a
missing cut list refuses instead of sending the original's timings.

Third, the claim about pictures is measured, not asserted. ``basis`` comes from
counting image references that resolve to frames actually sent, an audio-only
task is recorded as transcript-only, and no missing credential is ever allowed to
degrade into a note from another provider.

Fourth, the note it replaces is never lost. The flow's whole point is that there
is one note, so it takes over ``summary_markdown`` — and the displaced note stays
on the record and can be put back, including one somebody hand-edited.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.core.claude_vision as cv
import backend.core.debreath_job as dj
import backend.core.visual_note_job as vn
from backend.core.claude_vision import FrameInput, VisualNoteDraft
from backend.core.result_artifacts import artifact_target_path
from backend.local_main import create_local_app

TASK = "task-visual-note"

SEGMENTS = [
    {"start": 0.0, "end": 5.0, "text": "今天讲最小二乘法"},
    {"start": 12.0, "end": 18.0, "text": "这一句整段都是被剪掉的空白"},
    {"start": 65.0, "end": 70.0, "text": "看这张幻灯片上的公式"},
]

# One 10-20s cut, so 65.0 in the original lands at 55.0 in the cut file and the
# 12-18s line has no audio left at all.
CUT_LIST = {
    "cut_list_version": "1",
    "cuts": [{"start": 10.0, "end": 20.0}],
    "keeps": [{"start": 0.0, "end": 10.0}, {"start": 20.0, "end": 100.0}],
}


@pytest.fixture(autouse=True)
def no_ambient_key(monkeypatch):
    """Each test states its own credential situation.

    Both ways of reaching Claude are switched off here, including the Claude
    Code login on the developer's own machine. Otherwise a machine that happens
    to be signed in would quietly satisfy the refusal tests below, and they
    would stop testing anything on exactly the machine they matter on.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FLUENTFLOW_VISUAL_NOTE_MODEL", raising=False)
    # Unit tests pass a synthetic API key to the runner. Select that channel
    # explicitly so their result does not depend on whether this Mac has the
    # Claude Code CLI installed.
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "api_key")
    monkeypatch.setenv("FLUENTFLOW_CLAUDE_CLI", "/nonexistent/claude")
    vn.release(TASK)
    yield
    vn.release(TASK)


def _cut_media(task_id: str, name: str = "debreath/lecture_debreath.mp4") -> Path:
    """A cut file where a real render would have left one, plus its cut list."""
    media = artifact_target_path(task_id, name)
    media.write_bytes(b"pretend cut video")
    cut_list = artifact_target_path(task_id, "debreath/lecture_cut_list.json")
    cut_list.write_text(json.dumps(CUT_LIST), encoding="utf-8")
    return media


@pytest.fixture()
def job_store(monkeypatch, tmp_path):
    """A finished task that has already been de-breathed, with a real cut file."""
    _cut_media(TASK)
    state: dict[str, object] = {
        "task_id": TASK,
        "status": "completed",
        "client_id": "anonymous",
        "source_filename": "lecture.mp4",
        "result": {
            "task_id": TASK,
            "filename": "lecture.mp4",
            "summary_markdown": "原来的文字笔记",
            "display_segments": list(SEGMENTS),
            "debreath": {
                "status": "completed",
                "stage": "done",
                "rendered": True,
                "render_verified": True,
                "media_filename": "debreath/lecture_debreath.mp4",
                "plan": {"cut_count": 1, "removed_seconds": 10.0},
            },
            "artifacts": {
                dj.MEDIA_KIND: {
                    "kind": dj.MEDIA_KIND,
                    "filename": "debreath/lecture_debreath.mp4",
                },
                dj.CUT_LIST_KIND: {
                    "kind": dj.CUT_LIST_KIND,
                    "filename": "debreath/lecture_cut_list.json",
                },
            },
        },
    }

    def fake_get_job(task_id: str, **_: object):
        return dict(state) if task_id == TASK else None

    def fake_update(task_id: str, result: dict, **_: object):
        if task_id != TASK:
            return None
        state["result"] = result
        return dict(state)

    monkeypatch.setattr(vn, "get_job", fake_get_job)
    monkeypatch.setattr(vn, "update_job_result", fake_update)
    return state


@pytest.fixture()
def key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")


def _fake_extractor(tmp_path: Path, count: int = 3):
    """Stands in for ffmpeg: writes `count` plausible JPEGs into the scratch dir."""
    seen: dict[str, object] = {}

    def extract(video_path, output_dir, segments=None, **kwargs):
        seen["video_path"] = str(video_path)
        seen["segments"] = segments
        seen["anchor_seconds"] = kwargs.get("anchor_seconds")
        frames = []
        for index in range(count):
            path = Path(output_dir) / f"scene_{index + 1:04d}.jpg"
            path.write_bytes(b"\xff\xd8\xff" + bytes([index]) * 32)
            frames.append({
                "path": str(path),
                "timestamp_seconds": 30.0 * index,
                "source": "scene",
                "scene_threshold": 0.05,
                "low_information": index == count - 1,  # one blank frame, dropped
            })
        return vn_result(frames)

    extract.seen = seen  # type: ignore[attr-defined]
    return extract


def vn_result(frames, skipped_reason=None):
    from backend.core.local_keyframe_provider import KeyframeExtractionResult

    return KeyframeExtractionResult(
        provider="local_ffmpeg", frames=frames, skipped_reason=skipped_reason
    )


def _writer(markdown: str, basis_note: str = "两张幻灯片看得清"):
    seen: dict[str, object] = {}

    def write(transcript, frames, **kwargs):
        seen["transcript"] = transcript
        seen["frames"] = list(frames)
        seen["api_key"] = kwargs.get("api_key")
        return VisualNoteDraft(
            markdown=markdown,
            basis_note=basis_note,
            model="claude-opus-5",
            frames_sent=list(frames),
            transcript_chars=len(transcript),
        )

    write.seen = seen  # type: ignore[attr-defined]
    return write


def _run(tmp_path, markdown, *, frames=3, extractor=None, writer=None, **kwargs):
    return vn.run_visual_note(
        TASK,
        client_id="anonymous",
        api_key="sk-ant-test-not-a-real-key",
        keyframe_extractor=extractor or _fake_extractor(tmp_path, frames),
        writer=writer or _writer(markdown),
        **kwargs,
    )


# ── the note is written from the cut file, not the recording ────────────────

def test_the_cue_times_reach_the_frame_pass_on_the_cut_files_clock(job_store, tmp_path):
    """Subtitle cues place the looks — and they are the *cut* file's cues.

    The fixture cuts 10–20s away, so the line spoken at 65.0 in the recording is at
    55.0 in the file the frames come from, and the line at 12.0 has no audio left
    at all. Handing over the recording's times would aim every anchor a little
    later than the picture it was meant to land on, and nothing would look wrong.

    ``segments`` stays None deliberately: that parameter grabs three frames per
    window, which for a lecture's cues is thousands of grabs.
    """
    extractor = _fake_extractor(tmp_path)
    _run(tmp_path, "![幻灯片](note_0001.jpg)\n\n正文。", extractor=extractor)

    assert extractor.seen["anchor_seconds"] == [0.0, 55.0]
    assert extractor.seen["segments"] is None


def test_the_frames_come_out_of_the_cut_file(job_store, tmp_path):
    """The one substitution that would make the whole flow a lie: reading the
    original recording while the page says the shortened one."""
    extractor = _fake_extractor(tmp_path)
    updated = _run(tmp_path, "![幻灯片](note_0001.jpg)\n\n正文。", extractor=extractor)

    assert extractor.seen["video_path"].endswith("debreath/lecture_debreath.mp4")
    media = vn.visual_note_state(updated["result"])["media_source"]
    assert media["kind"] == vn.MEDIA_CUT and media["has_video"] is True
    assert media["artifact_kind"] == dj.MEDIA_KIND


def test_a_task_that_has_not_been_de_breathed_is_refused_with_its_first_step(job_store):
    job_store["result"].pop("debreath")
    job_store["result"]["artifacts"] = {}

    described = vn.describe(TASK, job_store, api_key="sk-ant-test")

    assert described["eligible"] is False
    assert "去气口" in described["reason"]
    assert described["media"] is None


def test_a_plan_only_run_is_told_to_render_before_a_note_can_be_written(job_store):
    job_store["result"]["debreath"] = {
        "status": "completed", "rendered": False, "plan": {"cut_count": 12}
    }
    job_store["result"]["artifacts"].pop(dj.MEDIA_KIND)

    described = vn.describe(TASK, job_store, api_key="sk-ant-test")

    assert described["eligible"] is False
    assert "剪辑表" in described["reason"] and "成品" in described["reason"]


def test_a_cut_file_that_is_gone_says_so_instead_of_reading_the_original(job_store):
    artifact_target_path(TASK, "debreath/lecture_debreath.mp4").unlink()

    described = vn.describe(TASK, job_store, api_key="sk-ant-test")

    assert described["eligible"] is False
    assert "剪后的文件" in described["reason"]


def test_nothing_to_cut_means_the_recording_is_the_cut_version_and_says_so(
    job_store, monkeypatch, tmp_path
):
    """A well-edited recording must not dead end the flow.

    With no silence worth removing there is no second file, because the two would
    be identical. That is stated rather than hidden: the note records that it read
    the original on the original's clock.
    """
    source = tmp_path / "clean.mp4"
    source.write_bytes(b"pretend clean video")
    monkeypatch.setattr(vn, "find_source_file", lambda _t: source)
    job_store["result"]["debreath"] = {
        "status": "completed", "rendered": False, "plan": {"cut_count": 0}
    }
    job_store["result"]["artifacts"].pop(dj.MEDIA_KIND)

    described = vn.describe(TASK, job_store, api_key="sk-ant-test")

    assert described["media"]["kind"] == vn.MEDIA_SOURCE_UNCHANGED
    assert described["media"]["unchanged"] is True
    assert any("没有找到可剪的空白" in item for item in described["media_warnings"])
    assert described["subtitle_timeline"]["source"] == "source_timeline"


# ── the subtitles are moved onto the cut file's clock ───────────────────────

def test_the_transcript_that_goes_out_is_on_the_cut_files_clock(job_store, tmp_path):
    writer = _writer("![幻灯片](note_0001.jpg)\n\n正文。")
    updated = _run(tmp_path, "", writer=writer)

    transcript = writer.seen["transcript"]
    assert "[00:55] 看这张幻灯片上的公式" in transcript, "65s in the original, 55s after the cut"
    assert "[01:05]" not in transcript, "that is the original recording's clock"
    assert "这一句整段都是被剪掉的空白" not in transcript, "its audio is not in the file"

    timeline = vn.visual_note_state(updated["result"])["subtitle_timeline"]
    assert timeline["source"] == "debreath_cut_list_remap"
    assert timeline["dropped_segments"] == 1 and timeline["segments"] == 2


def test_a_missing_cut_list_refuses_rather_than_sending_the_original_timings(job_store):
    artifact_target_path(TASK, "debreath/lecture_cut_list.json").unlink()

    described = vn.describe(TASK, job_store, api_key="sk-ant-test")

    assert described["eligible"] is False
    assert "剪辑表" in described["reason"]


def test_lines_that_lost_their_audio_are_reported_before_the_button(job_store, key):
    described = vn.describe(TASK, job_store, api_key=None)

    assert any("落在被剪掉的段里" in item for item in described["media_warnings"])


def test_a_talk_too_long_for_one_request_is_said_before_anything_is_paid_for(
    job_store, key, monkeypatch
):
    """The preview is free and the run is not, so this is where the number that
    changes has to appear. It is no longer a warning that content will go missing
    — the recording is split until it fits — but how many requests this will take
    is still the user's to know before paying for them."""
    monkeypatch.setattr(cv, "MAX_TRANSCRIPT_CHARS", 20)

    described = vn.describe(TASK, job_store, api_key=None)

    assert described["transcript_parts"] > 1
    assert described["transcript_chars_dropped"] == 0, "nothing is left out any more"
    assert not described["transcript_covered_until"]
    assert any("分" in item and "段" in item for item in described["media_warnings"])
    assert any("覆盖是完整的" in item for item in described["media_warnings"])


def test_a_talk_that_fits_says_nothing_about_length(job_store, key):
    described = vn.describe(TASK, job_store, api_key=None)

    assert described["transcript_parts"] == 1
    assert described["transcript_chars_dropped"] == 0
    assert not any("一次请求装不下" in item for item in described["media_warnings"])


def test_a_recording_too_long_for_one_request_is_no_longer_reported_as_unread(job_store, tmp_path):
    """This used to be the "note covers half a lecture, say so on its face" test.
    It is now the test that there is no such note: the recording is split until it
    fits, so nothing is left unread and a leftover count would tell the reader the
    note is short when it is complete."""
    import backend.core.claude_vision as _cv
    original = _cv.MAX_TRANSCRIPT_CHARS
    _cv.MAX_TRANSCRIPT_CHARS = 20
    try:
        updated = _run(tmp_path, "", writer=_recording_writer())
    finally:
        _cv.MAX_TRANSCRIPT_CHARS = original

    state = vn.visual_note_state(updated["result"])
    assert state["transcript_chars_dropped"] == 0
    assert not state["transcript_covered_until"]


def test_a_writer_that_truncates_on_its_own_still_has_it_recorded(job_store, tmp_path):
    """The split removes the product's own truncation, not a writer's. A channel
    that drops content for its own reasons still has to be able to say so, or the
    silent-half-note failure comes back through a different door."""
    def short_writer(transcript, frames, **_kwargs):
        return VisualNoteDraft(
            markdown="正文。",
            basis_note="",
            model="claude-opus-5",
            frames_sent=list(frames),
            transcript_chars=10,
            transcript_chars_dropped=25,
            transcript_covered_until="0:55",
        )

    updated = _run(tmp_path, "", writer=short_writer)

    state = vn.visual_note_state(updated["result"])
    assert state["transcript_chars_dropped"] == 25
    assert state["transcript_covered_until"] == "0:55"


# ── the "it read the pictures" claim is measured, not asserted ──────────────

def test_a_note_that_cites_frames_records_both_as_its_basis(job_store, tmp_path):
    updated = _run(tmp_path, "## 最小二乘法\n\n![幻灯片上的正规方程](note_0001.jpg)\n\n推导见板书。")

    state = vn.visual_note_state(updated["result"])
    assert state["basis"] == vn.BASIS_BOTH
    assert [frame["filename"] for frame in state["frames_cited"]] == ["note_0001.jpg"]
    assert len(state["frames_sent"]) == 2, "the blank frame is dropped before sending"


def test_the_result_says_where_the_pictures_came_from_and_how_hard_it_looked(job_store, tmp_path):
    """Samples and real scene changes produce identical-looking notes.

    Without this, a lecture summarised from evenly spaced snapshots is
    indistinguishable in the result from one built on detected changes, and a
    threshold that had to be relaxed twice looks like one that never moved.
    """
    updated = _run(tmp_path, "## 笔记\n\n![幻灯片](note_0001.jpg)\n")

    sent = vn.visual_note_state(updated["result"])["frames_sent"]
    assert {frame["source"] for frame in sent} == {"scene"}
    assert {frame["scene_threshold"] for frame in sent} == {0.05}


def test_a_note_that_cites_nothing_is_recorded_as_transcript_only(job_store, tmp_path):
    """The model saw the frames and found nothing worth citing. That is a real
    answer, and it must not be labelled as a note made from the pictures."""
    updated = _run(tmp_path, "## 最小二乘法\n\n只讲了公式，没有可用画面。")

    state = vn.visual_note_state(updated["result"])
    assert state["basis"] == vn.BASIS_TRANSCRIPT_ONLY
    assert state["frames_cited"] == []
    assert state["frames_sent"], "which is different from having sent nothing"


def test_a_reference_to_a_frame_nobody_sent_does_not_count_as_a_picture(job_store, tmp_path):
    updated = _run(tmp_path, "![我编的图](note_9999.jpg)\n\n正文。")

    state = vn.visual_note_state(updated["result"])
    assert state["basis"] == vn.BASIS_TRANSCRIPT_ONLY
    assert state["frames_cited"] == []


def test_the_subtitles_and_the_real_image_bytes_both_enter_the_request(tmp_path):
    """The one thing that would make the whole feature a lie is a request that
    carried the transcript and no pictures (or the reverse)."""
    frame = tmp_path / "note_0001.jpg"
    frame.write_bytes(b"\xff\xd8\xff" + b"pretend-jpeg" * 4)

    content = cv.build_user_content(
        "[00:00] 今天讲最小二乘法",
        [FrameInput(filename="note_0001.jpg", path=frame, timestamp_seconds=12.0)],
    )

    text = "\n".join(block["text"] for block in content if block["type"] == "text")
    images = [block for block in content if block["type"] == "image"]
    assert "今天讲最小二乘法" in text
    assert "note_0001.jpg" in text and "00:12" in text
    assert len(images) == 1
    import base64

    assert base64.standard_b64decode(images[0]["source"]["data"]) == frame.read_bytes()


# ── audio has no pictures, and the record says so ───────────────────────────

def test_a_pure_audio_task_is_written_from_its_subtitles_and_recorded_as_such(
    job_store, tmp_path
):
    """The audio case goes through — it just cannot claim a screen was read.

    The earlier version of this feature refused audio outright. Now the cut audio
    is real material and gets a note; what must never happen is that note carrying
    a label implying something looked at a picture.
    """
    for name in ("debreath/lecture_debreath.mp4", "debreath/lecture_cut_list.json"):
        artifact_target_path(TASK, name).unlink()
    _cut_media(TASK, "debreath/talk_debreath.m4a")
    job_store["result"]["artifacts"][dj.MEDIA_KIND]["filename"] = "debreath/talk_debreath.m4a"
    job_store["result"]["debreath"]["media_filename"] = "debreath/talk_debreath.m4a"

    described = vn.describe(TASK, job_store, api_key="sk-ant-test")
    assert described["media"]["has_video"] is False
    assert described["frame_budget"] == 0, "promising frames for audio is the lie"

    def explode(*_a, **_k):
        raise AssertionError("audio has no frames to extract")

    updated = _run(tmp_path, "## 会议要点\n\n只有声音。", extractor=explode)

    state = vn.visual_note_state(updated["result"])
    assert state["frames_sent"] == [] and state["basis"] == vn.BASIS_TRANSCRIPT_ONLY
    assert state["media_source"]["has_video"] is False
    assert state["markdown"].startswith("## 会议要点")


# ── no silent substitution ─────────────────────────────────────────────────

def test_with_no_key_it_refuses_and_asks_for_the_key(job_store, monkeypatch, tmp_path):
    """The key is what a release may ask for; a claude.ai login is not.

    This used to name the login first, because logging in once is less to ask
    than creating and pasting a key. That ordering belonged to a private tool.
    A build handed to other people cannot reach for their subscription on its
    own, so the refusal asks for the thing the user owns.
    """
    monkeypatch.delenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", raising=False)
    monkeypatch.setenv("FLUENTFLOW_CLAUDE_CLI", str(tmp_path / "no-claude-here"))

    described = vn.describe(TASK, job_store, api_key=None)

    assert described["eligible"] is False
    assert described["credential_configured"] is False
    assert "Anthropic API Key" in described["reason"]
    assert "subscription" not in described["reason"]


def test_a_machine_with_claude_code_is_not_offered_the_subscription(job_store, monkeypatch, tmp_path):
    """The channel stays reachable by name, but a public build only asks for a key."""
    monkeypatch.delenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", raising=False)
    fake_cli = tmp_path / "claude"
    fake_cli.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("FLUENTFLOW_CLAUDE_CLI", str(fake_cli))

    described = vn.describe(TASK, job_store, api_key=None)

    assert described["eligible"] is False
    assert "Anthropic API Key" in described["reason"]
    assert "subscription" not in described["reason"]
    assert "Claude Code" not in described["reason"]


def test_forcing_the_key_channel_still_names_the_key(job_store, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "api_key")

    described = vn.describe(TASK, job_store, api_key=None)

    assert described["eligible"] is False
    assert "Anthropic API Key" in described["reason"]


def test_other_providers_keys_do_not_stand_in_for_a_claude_one(job_store, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-qwen")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

    described = vn.describe(TASK, job_store, api_key=None)

    assert described["eligible"] is False, "a note nobody looked at pictures for is not this feature"


def test_a_missing_sdk_refuses_with_the_install_command(monkeypatch):
    monkeypatch.setattr(cv, "anthropic", None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    reason = cv.unavailable_reason()

    assert reason and "pip install" in reason


def test_an_unfinished_task_is_refused_by_its_current_state(job_store, key):
    job_store["status"] = "running"

    described = vn.describe(TASK, job_store, api_key=None)

    assert described["eligible"] is False and "running" in described["reason"]


# ── the note it replaces is never lost ─────────────────────────────────────

def test_the_finished_note_becomes_the_tasks_note_and_the_old_one_is_kept(
    job_store, tmp_path
):
    """This is the whole point of the flow: one note, describing the file the
    user now has. The one it displaces stays on the record."""
    updated = _run(tmp_path, "![幻灯片](note_0001.jpg)\n\n新的笔记。")

    result = updated["result"]
    assert result["summary_markdown"].startswith("![幻灯片]")
    assert result["summary_written_from"] == vn.SUMMARY_WRITTEN_FROM
    assert not result.get("summary_edited"), "nobody typed this"
    state = vn.visual_note_state(result)
    assert state["promoted"] is True
    assert state["replaced_note"]["previous_markdown"] == "原来的文字笔记"


def test_a_caller_that_asks_not_to_replace_leaves_the_existing_note_alone(
    job_store, tmp_path
):
    updated = _run(tmp_path, "![幻灯片](note_0001.jpg)\n\n新的笔记。", replace_note=False)

    assert updated["result"]["summary_markdown"] == "原来的文字笔记"
    assert updated["result"].get("summary_written_from") is None
    assert vn.visual_note_state(updated["result"])["promoted"] is False


def test_a_hand_edited_note_can_be_put_back_after_a_rewrite(job_store, tmp_path):
    job_store["result"]["summary_markdown"] = "我自己改过的笔记"
    job_store["result"]["summary_edited"] = True

    _run(tmp_path, "![幻灯片](note_0001.jpg)\n\n新的笔记。")
    restored = vn.restore_previous_note(TASK, client_id="anonymous")

    result = restored["result"]
    assert result["summary_markdown"] == "我自己改过的笔记"
    assert result["summary_edited"] is True, "it was hand-edited before and still is"
    assert result["summary_written_from"] is None, "the restored note is not from the cut file"
    state = vn.visual_note_state(result)
    assert state["promoted"] is False
    assert state["markdown"].startswith("![幻灯片]"), "and the new note is still readable"


def test_the_new_note_can_be_chosen_again_without_paying_for_another_run(
    job_store, tmp_path
):
    _run(tmp_path, "![幻灯片](note_0001.jpg)\n\n新的笔记。")
    vn.restore_previous_note(TASK, client_id="anonymous")

    again = vn.use_generated_note(TASK, client_id="anonymous")

    assert again["result"]["summary_markdown"].startswith("![幻灯片]")
    assert vn.visual_note_state(again["result"])["promoted"] is True
    replaced = vn.visual_note_state(again["result"])["replaced_note"]
    assert replaced["previous_markdown"] == "原来的文字笔记", "still restorable"


def test_restoring_with_nothing_to_restore_is_refused_in_words(job_store):
    with pytest.raises(vn.VisualNoteError, match="没有可以恢复"):
        vn.restore_previous_note(TASK, client_id="anonymous")


def test_a_failed_run_records_itself_and_leaves_the_note_untouched(job_store, tmp_path):
    def explode(*_a, **_k):
        raise cv.ClaudeVisionError("Anthropic 接口暂时限流，稍后再试。")

    with pytest.raises(vn.VisualNoteError, match="限流"):
        _run(tmp_path, "", writer=explode)

    state = vn.visual_note_state(job_store["result"])
    assert state["status"] == vn.STATUS_FAILED and "限流" in state["error"]
    assert job_store["result"]["summary_markdown"] == "原来的文字笔记"
    assert TASK not in vn._running, "and the slot is released"


def test_an_unexpected_failure_still_clears_the_running_state(job_store, tmp_path):
    def explode(*_a, **_k):
        raise ValueError("something nobody predicted")

    with pytest.raises(ValueError):
        _run(tmp_path, "", writer=explode)

    assert vn.visual_note_state(job_store["result"])["status"] == vn.STATUS_FAILED
    assert job_store["result"]["summary_markdown"] == "原来的文字笔记"
    assert TASK not in vn._running


def test_extraction_that_finds_nothing_refuses_without_paying_for_a_request(
    job_store, tmp_path
):
    called: list[str] = []

    def empty(*_a, **_k):
        return vn_result([], skipped_reason="disabled")

    def writer(*_a, **_k):
        called.append("paid")
        raise AssertionError("must not reach the model")

    with pytest.raises(vn.VisualNoteError, match="没能从剪后的视频里抽出画面"):
        _run(tmp_path, "", extractor=empty, writer=writer)
    assert called == []
    assert job_store["result"]["summary_markdown"] == "原来的文字笔记"


def test_a_second_run_on_the_same_task_is_refused_rather_than_queued(job_store):
    vn.claim(TASK)
    with pytest.raises(vn.VisualNoteError, match="正在生成中"):
        vn.claim(TASK)


def test_a_note_left_running_by_a_killed_process_is_cleared_at_startup(monkeypatch):
    rows = [{
        "task_id": TASK,
        "client_id": "anonymous",
        "result": {"visual_note": {"status": vn.STATUS_RUNNING}},
    }]
    saved: dict[str, object] = {}

    monkeypatch.setattr(vn, "list_jobs_by_statuses", lambda *_a, **_k: rows)
    monkeypatch.setattr(
        vn, "update_job_result", lambda task_id, result, **_k: saved.update(result) or True
    )

    assert vn.recover_stranded_notes() == 1
    assert saved["visual_note"]["status"] == vn.STATUS_FAILED
    assert "重新发起" in saved["visual_note"]["error"]


# ── the downloadable note ──────────────────────────────────────────────────

def test_the_note_is_written_as_a_downloadable_file_with_serveable_image_links(
    job_store, tmp_path
):
    updated = _run(tmp_path, "![幻灯片](note_0001.jpg)\n\n正文。")

    artifact = updated["result"]["artifacts"][vn.VISUAL_NOTE_KIND]
    assert artifact["filename"] == vn.VISUAL_NOTE_ARTIFACT_FILENAME
    written = artifact_target_path(TASK, artifact["filename"]).read_text("utf-8")
    assert f"/jobs/{TASK}/artifacts/frame?file=note_0001.jpg" in written, (
        "a bare filename would not resolve for anyone reading the note"
    )


# ── the route refuses in the response the caller is waiting on ─────────────

@pytest.fixture()
def client():
    return TestClient(create_local_app())


def test_the_route_exists_on_the_local_edition(client):
    paths = {route.path for route in client.app.routes}
    assert "/jobs/{task_id}/visual-note" in paths


def test_a_preview_costs_nothing_and_still_reports_why_it_cannot_run(client):
    from backend.core.job_store import upsert_job

    upsert_job(
        task_id="preview-task",
        status="completed",
        client_id="anonymous",
        result={"task_id": "preview-task", "filename": "talk.m4a"},
    )

    response = client.post("/jobs/preview-task/visual-note", json={"preview": True})

    assert response.status_code == 200
    body = response.json()
    assert body["preview"] is True and body["eligible"] is False
    assert "去气口" in body["reason"], "the flow's first step is what is missing"
    assert body["will_replace_note"] is True, "said before the button, not after"


def test_an_ineligible_task_is_refused_with_the_reason_not_accepted(client):
    from backend.core.job_store import upsert_job

    upsert_job(
        task_id="refuse-task",
        status="completed",
        client_id="anonymous",
        result={"task_id": "refuse-task", "filename": "talk.m4a"},
    )

    response = client.post("/jobs/refuse-task/visual-note", json={})

    assert response.status_code == 409
    assert "去气口" in response.json()["detail"]


def test_an_unknown_task_is_a_404(client):
    assert client.post("/jobs/nope/visual-note", json={"preview": True}).status_code == 404


# ── a recording longer than one request still gets a note that covers it ─────

def _recording_writer():
    """A writer that records each call, and answers with that part's own text."""
    calls: list[dict] = []

    def write(transcript, frames, **kwargs):
        part = kwargs.get("part")
        calls.append({
            "transcript": transcript,
            "frames": list(frames),
            "part": part,
        })
        label = f"第{part.index}段" if part is not None else "整篇"
        return VisualNoteDraft(
            markdown=f"## {label}\n\n{label}的内容。",
            basis_note="看过画面",
            model="claude-opus-5",
            frames_sent=list(frames),
            transcript_chars=len(transcript),
        )

    write.calls = calls  # type: ignore[attr-defined]
    return write


def test_a_transcript_too_long_for_one_request_is_written_in_parts(job_store, tmp_path, monkeypatch):
    """Truncating produced the worst kind of wrong result: a note that stops two
    thirds of the way through and reads like a finished one. The recording is not
    too long to write about, only too long for one request."""
    monkeypatch.setattr(vn.claude_vision, "MAX_TRANSCRIPT_CHARS", 20)
    writer = _recording_writer()

    updated = _run(tmp_path, "", writer=writer)

    assert len(writer.calls) > 1, "one call per part, not one truncated call"
    assert all(call["part"] is not None for call in writer.calls)
    assert [call["part"].index for call in writer.calls] == list(
        range(1, len(writer.calls) + 1)
    ), "parts are written in recording order"
    note = vn.visual_note_state(updated["result"])["markdown"]
    for call in writer.calls:
        assert f"第{call['part'].index}段的内容" in note, "every part reaches the finished note"


def test_nothing_is_reported_as_dropped_once_the_whole_thing_was_written(job_store, tmp_path, monkeypatch):
    """The "N characters did not fit" number is the truncation warning. Splitting
    is what makes it zero — leaving it set would tell the reader the note is
    short when it is complete."""
    monkeypatch.setattr(vn.claude_vision, "MAX_TRANSCRIPT_CHARS", 20)

    updated = _run(tmp_path, "", writer=_recording_writer())

    state = vn.visual_note_state(updated["result"])
    assert state.get("transcript_chars_dropped", 0) == 0
    assert not state.get("transcript_covered_until")


def test_a_short_recording_is_still_one_request(job_store, tmp_path):
    """Nearly every recording fits. Splitting one that does not need it would pay
    for a second request and break the note into sections for no reason."""
    writer = _recording_writer()

    _run(tmp_path, "", writer=writer)

    assert len(writer.calls) == 1
    assert writer.calls[0]["part"].total == 1
