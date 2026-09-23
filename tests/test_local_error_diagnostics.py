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


def test_a_backend_that_lost_its_own_folder_is_not_told_to_retry():
    for raw in ("[Errno 1] Operation not permitted", "后台服务失去了访问自己程序文件夹的权限（macOS 隐私保护），转写没法启动。"):
        diagnosis = diagnose_error(raw)
        assert diagnosis["code"] == "backend_folder_access_lost"
        assert diagnosis["retryable"] is False
        assert "文稿" in diagnosis["next_action"]


def test_a_permission_error_on_a_named_file_is_not_mistaken_for_the_folder():
    diagnosis = diagnose_error("[Errno 1] Operation not permitted: '/Volumes/USB/a.mov'")
    assert diagnosis["code"] != "backend_folder_access_lost"
