from backend.core.artifacts import attach_result_artifacts
from backend.core.local_processing_plan import ensure_processing_plan


def test_artifacts_module_accepts_local_plan_enrichment(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUENTFLOW_ARTIFACT_DIR", str(tmp_path))

    result = attach_result_artifacts(
        "task-local-artifacts",
        {
            "source": "video",
            "filename": "lesson.mp4",
            "transcript_text": "课程内容",
        },
        enrich_result=ensure_processing_plan,
    )

    assert result["processing_plan"]["execution"]["scope"] == "local"
    assert result["artifacts"]["transcript_txt"]["filename"] == "lesson.txt"
