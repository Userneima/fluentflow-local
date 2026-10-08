"""Requirements for what the task detail page says about each step.

- The note step says who wrote the note and from what, in a sentence.
- A link task that has not downloaded its video (a fresh one or a retry) does
  not say the material is saved.
- When the event log recorded a step, the timeline uses it and says so.
- The material judgement is in Chinese and calls a recording long only when
  it is.
- Every artifact the cut and frame-note flows write has a Chinese label.
"""

from __future__ import annotations

import re
import uuid

from backend.core.event_logger import log_event
from backend.core.local_task_detail import build_task_detail


def _job(**overrides):
    job = {
        "task_id": uuid.uuid4().hex,
        "status": "completed",
        "stage": "done",
        "source_type": "video",
        "metadata": {},
        "result": {
            "transcript_text": "大家好，今天我们聊一聊找工作的经历。",
            "summary_markdown": "# 笔记\n\n内容",
            "summary_status": "completed",
            "audio_duration_seconds": 180,
        },
    }
    job.update(overrides)
    return job


def _step(detail, step_id):
    return next(step for step in detail["timeline"] if step["id"] == step_id)


def test_the_note_step_says_who_wrote_it_and_from_what():
    pipeline = _job()
    pipeline["result"]["resolved_note_mode"] = "auto"
    frames_from_original = _job()
    frames_from_original["result"].update(
        summary_written_from="source_media_note",
        visual_note={"basis": "transcript_and_frames", "media_source": {"kind": "source_no_cuts"}},
    )
    frames_from_cut = _job()
    frames_from_cut["result"].update(summary_written_from="debreath_media_note", visual_note={"basis": "transcript_only"})

    texts = [_step(build_task_detail(job), "note_generation")["detail"] for job in (pipeline, frames_from_original, frames_from_cut)]

    for text in texts:
        assert "自动选择" not in text, text
        assert re.search(r"(文本模型|Claude).*(转录稿|画面)", text), text
    assert "原录音" in texts[1] and "画面" in texts[1]
    assert "去气口" in texts[2] and "画面" not in texts[2]


def test_a_link_task_waiting_to_download_does_not_say_the_material_is_saved():
    # What a retried link task looks like before its download starts.
    retried = _job(
        status="queued", stage="queued", source_type="video_link",
        metadata={"video_source_url": "https://www.douyin.com/video/1", "retried_from": "old"},
        result=None,
    )

    step = _step(build_task_detail(retried), "source_fetch")

    assert step["status"] == "pending"
    assert "已保存" not in step["detail"]


def test_a_downloaded_link_task_waiting_for_transcription_shows_the_fetch_done():
    downloaded = _job(
        status="queued", stage="queued", source_type="video",
        metadata={"video_source": {"provider": "yt-dlp"}}, result=None,
    )

    assert _step(build_task_detail(downloaded), "source_fetch")["status"] == "completed"


def test_steps_with_recorded_events_are_marked_recorded():
    job = _job()
    for name, stage in (
        ("source_imported", "import"), ("audio_extracted", "audio"),
        ("stt_completed", "stt"), ("transcript_ready", "transcript_ready"),
        ("summary_completed", "summary"), ("task_completed", "done"),
    ):
        log_event(task_id=job["task_id"], event_name=name, stage=stage, success=True, duration_seconds=2.0)

    detail = build_task_detail(job)

    assert detail["data_quality"]["has_recorded_steps"] is True
    assert _step(detail, "transcription")["source"] == "recorded"
    assert _step(detail, "transcription")["finished_at"]
    assert "recorded" in detail["data_quality"]["timeline_sources"]


def test_a_failure_event_fails_the_step_it_happened_in():
    job = _job(status="failed", stage="stt", result=None, error_reason="模型加载失败")
    log_event(task_id=job["task_id"], event_name="source_imported", stage="import", success=True)
    log_event(task_id=job["task_id"], event_name="task_failed", stage="stt", success=False, error_reason="模型加载失败")

    detail = build_task_detail(job)

    assert _step(detail, "source_fetch")["status"] == "completed"
    transcription = _step(detail, "transcription")
    assert transcription["status"] == "failed" and transcription["source"] == "recorded"


def test_a_task_without_events_is_still_inferred():
    detail = build_task_detail(_job())

    assert detail["data_quality"]["has_recorded_steps"] is False


def _material_entry(job):
    entries = build_task_detail(job)["decision_log"]["entries"]
    return next(entry for entry in entries if entry["id"] == "material_classification")


def test_material_judgement_is_chinese_and_a_three_minute_clip_is_not_long():
    job = _job()
    job["result"]["transcript_text"] = "这个概念的原理是这样的，首先我们讲解一个案例，然后总结。"

    entry = _material_entry(job)
    words = " ".join([entry["decision"], entry["reason"], entry.get("impact", ""), *entry["evidence"]])

    assert not re.search(r"[A-Za-z]{3,}", words.replace("AI", "")), words
    assert "长视频" not in words


def test_a_long_recording_is_called_long():
    job = _job()
    job["result"]["transcript_text"] = "我们讲解一个概念。"
    job["result"]["audio_duration_seconds"] = 3600

    entry = _material_entry(job)

    assert "长视频" in entry["reason"] or "超过 30 分钟" in " ".join(entry["evidence"])


def test_cut_and_frame_note_artifacts_have_chinese_labels():
    job = _job()
    job["result"]["artifacts"] = {
        kind: {"filename": f"{kind}.bin"} for kind in ("debreath_cut_list", "debreath_media", "visual_note")
    }

    labels = {item["kind"]: item["label"] for item in build_task_detail(job)["artifacts"]}

    for kind, label in labels.items():
        assert label != kind and re.search(r"[一-鿿]", label), (kind, label)
