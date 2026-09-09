from backend.core.local_processing_plan import build_processing_plan


def test_local_processing_plan_always_uses_local_transcription():
    plan = build_processing_plan(
        {
            "source": "video",
            "filename": "lesson.mp4",
            "stt_provider": "unexpected-provider",
        }
    )

    assert plan["execution"] == {
        "scope": "local",
        "transcription_tool": "local_whisper",
    }


def test_local_processing_plan_reads_transcript_files_without_stt():
    plan = build_processing_plan(
        {
            "source": "transcript_file",
            "filename": "lesson.srt",
            "transcript_text": "课程字幕",
        }
    )

    assert plan["execution"] == {
        "scope": "local",
        "transcription_tool": "transcript_parser",
    }
