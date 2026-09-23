"""The whole flow once, with real ffmpeg and the real storage layout.

The mocked tests prove the decisions; this proves the plumbing, which is where
this flow can go wrong invisibly. Three joins are only real here:

1. the cut lands in its own directory and the remapped subtitles land beside it,
   under the names the result records, and the download routes serve all of them;
2. the frames the note is written from are extracted from the *cut* file, by real
   ffmpeg, into the task's frame directory;
3. what actually reaches Claude carries those frame paths and the remapped
   timestamps.

Nothing is sent anywhere. The media is synthetic — solid colour blocks and a tone
with silent stretches, built here by ffmpeg — and the Claude Code call goes to a
stub that returns the envelope the real program returns, so the request is
asserted rather than delivered. That is the point of running it this way: the
assembled command and prompt are checked without an outbound request or a
credential, on material that came from nobody's recording.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.core.claude_code_note as ccn
import backend.core.debreath_job as dj
import backend.core.visual_note_job as vn
from backend.core.local_request_scope import LOCAL_OWNER_ID
from backend.core.job_store import get_job, upsert_job
from backend.core.local_keyframe_provider import extract_keyframes
from backend.core.result_artifacts import VISUAL_NOTE_KIND
from backend.core.storage_paths import _source_storage_dir
from backend.local_main import create_local_app

TASK = "task-e2e-cut-then-note"

# 12 seconds, three solid colours so real scene detection has real changes to
# find, and a tone that goes silent from 4s to 7s.
SEGMENTS = [
    {"start": 0.0, "end": 3.5, "text": "开场先讲这门课要解决什么问题"},
    {"start": 4.5, "end": 6.5, "text": "这一句整段都在安静的那一段里"},
    {"start": 8.0, "end": 11.5, "text": "最后看这一页上的公式"},
]

CLI_NOTE = {
    "note_markdown": "## 课程要点\n\n![这一页上的公式](note_0001.jpg)\n\n推导过程见画面。",
    "basis_note": "第一张画面看得清，其余是同一块底色。",
}


def _build_media(target: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    built = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i",
            "color=c=red:s=320x240:r=30:d=12,"
            "drawbox=enable='between(t,4,8)':c=green:t=fill,"
            "drawbox=enable='gt(t,8)':c=blue:t=fill",
            "-f", "lavfi", "-i",
            "sine=frequency=440:duration=12:sample_rate=48000,"
            "volume=enable='between(t,4,7)':volume=0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", "-y", str(target),
        ],
        capture_output=True, text=True, timeout=180,
    )
    assert built.returncode == 0, built.stderr[-400:]


@pytest.fixture()
def synthetic_task(monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    source_dir = _source_storage_dir() / TASK
    source_dir.mkdir(parents=True, exist_ok=True)
    source = source_dir / "source.mp4"
    _build_media(source)
    upsert_job(
        task_id=TASK,
        status="completed",
        # The one owner the local edition's routes read, so the download
        # assertions below go through the real guard rather than around it.
        client_id=LOCAL_OWNER_ID,
        result={
            "task_id": TASK,
            "filename": "lecture.mp4",
            "summary_markdown": "原来那份根据完整录像写的笔记",
            "display_segments": list(SEGMENTS),
        },
    )
    vn.release(TASK)
    yield source
    vn.release(TASK)


def _stub_cli(monkeypatch) -> dict[str, object]:
    """Stands in for the ``claude`` program: records the call, returns its shape."""
    seen: dict[str, object] = {}
    monkeypatch.setenv("FLUENTFLOW_VISUAL_NOTE_CHANNEL", "subscription")
    monkeypatch.setattr(ccn, "cli_path", lambda: "/usr/bin/true")

    def runner(command, **kwargs):
        seen["command"] = list(command)
        seen["prompt"] = kwargs.get("input") or ""
        seen["env"] = dict(kwargs.get("env") or {})
        # The streamed shape the CLI is asked for: the frames the agent opened are
        # tool calls, and only the last line is the answer.
        opened = [
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": f"/frames/note_{n:04d}.jpg"}},
            ]}})
            for n in (1, 2)
        ]
        answer = json.dumps({
            "type": "result",
            "result": json.dumps(CLI_NOTE, ensure_ascii=False),
            "model": "claude-opus-5",
        }, ensure_ascii=False)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="\n".join([json.dumps({"type": "system", "subtype": "init"}), *opened, answer]),
            stderr="",
        )

    def write(transcript, frames, **kwargs):
        return ccn.write_visual_note(transcript, frames, runner=runner, **kwargs)

    seen["write"] = write
    return seen


def test_cut_the_media_then_write_the_note_from_it(synthetic_task, monkeypatch):
    source = synthetic_task

    # ── step one: the cut, with real ffmpeg ────────────────────────────────
    cut = dj.run_debreath(TASK, min_silence_seconds=0.5)
    debreath = dj.debreath_state(cut["result"])

    assert debreath["status"] == dj.STATUS_COMPLETED and debreath["rendered"] is True
    assert debreath["render_verified"] is True, debreath.get("render")
    assert 2.0 < debreath["plan"]["removed_seconds"] < 3.5, debreath["plan"]

    artifacts = cut["result"]["artifacts"]
    for kind in (dj.CUT_LIST_KIND, dj.MEDIA_KIND, dj.TRANSCRIPT_KIND):
        filename = artifacts[kind]["filename"]
        assert filename.startswith("debreath/"), f"{kind} is not in the cut directory"
        assert dj.artifact_target_path(TASK, filename).is_file(), kind
    assert source.is_file(), "the recording that was uploaded is never touched"

    # The subtitles that came out belong to the cut file, and say what was lost.
    timeline = debreath["transcript_timeline"]
    assert timeline["source"] == "debreath_cut_list_remap"
    assert timeline["dropped_segments"] == 1, "that line's audio was removed"
    srt = dj.artifact_target_path(TASK, artifacts[dj.TRANSCRIPT_KIND]["filename"]).read_text("utf-8")
    assert "这一句整段都在安静的那一段里" not in srt
    assert "最后看这一页上的公式" in srt

    # ── step two: the note, from the cut file ──────────────────────────────
    stub = _stub_cli(monkeypatch)
    done = vn.run_visual_note(
        TASK,
        keyframe_extractor=extract_keyframes,
        writer=stub["write"],
    )
    state = vn.visual_note_state(done["result"])

    assert state["status"] == vn.STATUS_COMPLETED
    assert state["media_source"]["kind"] == vn.MEDIA_CUT
    assert state["media_source"]["filename"] == artifacts[dj.MEDIA_KIND]["filename"]
    assert state["subtitle_timeline"]["source"] == "debreath_cut_list_remap"
    assert state["frames_sent"], "real ffmpeg found no frames in the cut file"

    # Frames really came out of the cut file, into the task's own frame directory.
    for frame in state["frames_sent"]:
        path = dj.artifact_target_path(TASK, f"frames/{frame['filename']}")
        assert path.is_file() and path.stat().st_size > 0

    # ── what would have reached Claude ────────────────────────────────────
    prompt = str(stub["prompt"])
    assert "最后看这一页上的公式" in prompt
    assert "这一句整段都在安静的那一段里" not in prompt, "its audio is not in the file it read"
    assert "[00:0" in prompt or "[00:1" in prompt
    first = state["frames_sent"][0]["filename"]
    assert first in prompt, "each attached picture is named so a citation can resolve"
    assert '"type": "image"' in prompt, "the pictures ride in the message, not behind a tool"
    command = list(stub["command"])
    assert "--add-dir" not in command, (
        "with the pictures attached there is nothing to read, so no directory is opened"
    )
    assert "ANTHROPIC_API_KEY" not in dict(stub["env"]), (
        "a key in the environment would pay for a run the page said the subscription would"
    )

    # ── the note is this task's note, and the old one is recoverable ───────
    result = get_job(TASK)["result"]
    assert result["summary_markdown"].startswith("## 课程要点")
    assert result["summary_written_from"] == vn.SUMMARY_WRITTEN_FROM
    assert vn.visual_note_state(result)["replaced_note"]["previous_markdown"] == (
        "原来那份根据完整录像写的笔记"
    )
    assert state["basis"] == vn.BASIS_BOTH, "the note cited a frame that was really sent"

    # ── everything is downloadable through the ordinary routes ─────────────
    client = TestClient(create_local_app())
    for kind in (dj.CUT_LIST_KIND, dj.MEDIA_KIND, dj.TRANSCRIPT_KIND, VISUAL_NOTE_KIND):
        response = client.get(f"/jobs/{TASK}/artifacts/{kind}")
        assert response.status_code == 200, f"{kind}: {response.status_code}"
        assert response.content, kind
    served = client.get(f"/jobs/{TASK}/artifacts/{dj.TRANSCRIPT_KIND}").text
    assert "最后看这一页上的公式" in served
    original = client.get(f"/jobs/{TASK}/source")
    assert original.status_code == 200
    assert len(original.content) != dj.artifact_target_path(
        TASK, artifacts[dj.MEDIA_KIND]["filename"]
    ).stat().st_size, "the two downloads are two different files"


def test_the_note_can_be_switched_back_and_forth_without_another_run(
    synthetic_task, monkeypatch
):
    dj.run_debreath(TASK, min_silence_seconds=0.5)
    stub = _stub_cli(monkeypatch)
    vn.run_visual_note(TASK, keyframe_extractor=extract_keyframes, writer=stub["write"])

    restored = vn.restore_previous_note(TASK)
    assert restored["result"]["summary_markdown"] == "原来那份根据完整录像写的笔记"
    assert restored["result"]["summary_written_from"] is None

    again = vn.use_generated_note(TASK)
    assert again["result"]["summary_markdown"].startswith("## 课程要点")
    assert again["result"]["summary_written_from"] == vn.SUMMARY_WRITTEN_FROM


def test_a_re_cut_after_a_declined_one_becomes_the_file_the_note_reads(
    synthetic_task, monkeypatch
):
    """The declined-then-re-cut path, which is the one faintly recorded material
    takes.

    A pre-transcription cut whose spot check finds a cut too close to speech
    declines its own render and records used_for_transcription=False, so the
    transcript belongs to the recording. Re-cutting with a longer minimum silence
    then produces a real cut file — and that file, not the recording, is what the
    note has to be written from. Leaving the flag at False sent the note step
    looking for a recording that is not kept in the artifact store, and it
    reported "the cut file is no longer on this machine" about a file sitting
    right there.
    """
    import backend.core.visual_note_job as vn

    # The state a declined pre-transcription cut leaves behind.
    dj._store(
        TASK,
        client_id=None,
        state={
            "status": dj.STATUS_COMPLETED,
            "stage": "done",
            "rendered": False,
            "used_for_transcription": False,
            "not_used_reason": "抽查发现剪掉的部分只比紧邻的说话声低一点点，这次按原文件转写。",
        },
    )

    cut = dj.run_debreath(TASK, min_silence_seconds=0.5)
    state = dj.debreath_state(cut["result"])

    assert state["rendered"] is True
    assert state["used_for_transcription"] is True, (
        "a run that produced a cut file is the file to read from"
    )
    assert not state.get("not_used_reason"), "the declined run's reason is stale now"

    media = vn.cut_media(TASK, cut["result"])
    assert media is not None, "the note has a file to read"
    assert media.unchanged is False, "and it is the cut file, not the recording"
    assert media.path.is_file()
