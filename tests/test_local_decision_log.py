from backend.core.local_decision_log import build_decision_log


def test_local_decision_log_explains_only_local_execution():
    log = build_decision_log(
        {
            "source": "video",
            "filename": "lesson.mp4",
            "stt_provider": "unexpected-provider",
        }
    )
    entry = next(item for item in log["entries"] if item["id"] == "execution_route")

    assert entry["decision"] == "本机处理"
    assert "云端" not in entry["reason"]
