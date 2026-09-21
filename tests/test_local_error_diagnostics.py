from backend.core.local_error_diagnostics import diagnose_error


def test_local_error_diagnostics_cover_local_workflow_failures():
    cases = [
        ("媒体中没有可转录的音轨", "media_audio_stream_missing", "上传包含"),
        ("Queued source file is missing", "source_file_missing", "重新上传"),
        ("HTTP Error 403: Forbidden", "platform_forbidden", "cookies"),
        ("AI summarization returned empty result", "empty_ai_note", "重生笔记"),
        ("lark-cli is not logged in", "lark_cli_login_required", "重新登录"),
    ]

    for raw, code, action in cases:
        diagnosis = diagnose_error(raw)
        assert diagnosis["code"] == code
        assert action in diagnosis["next_action"]


def test_local_diarization_failure_does_not_recommend_remote_processing():
    diagnosis = diagnose_error("No position encodings are defined")

    assert diagnosis["code"] == "local_diarization_too_long"
    assert "关闭说话人区分" in diagnosis["next_action"]
    assert "云端" not in diagnosis["detail"] + diagnosis["next_action"]


def test_local_error_diagnostics_keep_unknown_detail():
    diagnosis = diagnose_error("vendor exploded with code 999")

    assert diagnosis["code"] == "unknown_error"
    assert diagnosis["detail"] == "vendor exploded with code 999"
