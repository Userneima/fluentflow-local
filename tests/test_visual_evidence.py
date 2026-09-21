from __future__ import annotations

from pathlib import Path

from backend.core.ai_summarizer import _coerce_visual_requests, visual_requests_to_frame_segments
from backend.core.visual_evidence import (
    build_visual_evidence_from_note_images,
    build_visual_key_moments,
    inject_visual_evidence_references,
    rewrite_note_image_references,
)


def test_build_visual_evidence_from_agent_selected_note_images() -> None:
    payload = build_visual_evidence_from_note_images(
        "## 核心概念\n\n![流程图展示了 Agent 执行路线](scene_0001.jpg)\n",
        [
            {
                "kind": "frame",
                "filename": "frames/scene_0001.jpg",
                "url": "/jobs/task/artifacts/frame?file=scene_0001.jpg",
                "timestamp_seconds": 12.5,
                "provider": "local_ffmpeg",
            }
        ],
    )

    assert payload["visual_evidence_status"] == "completed"
    assert payload["visual_evidence"][0]["reason"] == "流程图展示了 Agent 执行路线"
    assert payload["visual_evidence"][0]["note_section"] == "核心概念"
    assert payload["visual_evidence"][0]["artifact_url"] == "/jobs/task/artifacts/frame?file=scene_0001.jpg"
    assert payload["visual_artifacts"]["visual_001"]["filename"] == "frames/scene_0001.jpg"


def test_candidate_frame_without_note_image_is_not_final_visual_evidence() -> None:
    payload = build_visual_evidence_from_note_images(
        "## 核心概念\n\n这里没有图片。\n",
        [{"kind": "frame", "filename": "frames/scene_0001.jpg", "url": "/jobs/task/artifacts/frame?file=scene_0001.jpg"}],
    )

    assert payload["visual_evidence_status"] == "unavailable"
    assert payload["visual_evidence"] == []
    assert payload["visual_artifacts"] == {}


def test_note_image_without_alt_text_is_not_promoted() -> None:
    payload = build_visual_evidence_from_note_images(
        "![](scene_0001.jpg)",
        [{"kind": "frame", "filename": "frames/scene_0001.jpg", "url": "/jobs/task/artifacts/frame?file=scene_0001.jpg"}],
    )

    assert payload["visual_evidence_status"] == "unavailable"


def test_rewrite_note_image_references_to_artifact_urls() -> None:
    markdown = "![流程图](scene_0001.jpg)\n\n![未知](missing.jpg)"
    rewritten = rewrite_note_image_references(
        markdown,
        [
            {
                "filename": "frames/scene_0001.jpg",
                "url": "/jobs/task/artifacts/frame?file=scene_0001.jpg",
            }
        ],
    )

    assert "![流程图](/jobs/task/artifacts/frame?file=scene_0001.jpg)" in rewritten
    assert "![未知](missing.jpg)" in rewritten


def test_build_visual_evidence_from_rewritten_artifact_url() -> None:
    payload = build_visual_evidence_from_note_images(
        "## 核心概念\n\n![流程图](/jobs/task/artifacts/frame?file=scene_0001.jpg)\n",
        [
            {
                "filename": "frames/scene_0001.jpg",
                "url": "/jobs/task/artifacts/frame?file=scene_0001.jpg",
            }
        ],
    )

    assert payload["visual_evidence_status"] == "completed"
    assert payload["visual_evidence"][0]["artifact_url"] == "/jobs/task/artifacts/frame?file=scene_0001.jpg"


def test_low_value_cover_image_is_removed_from_final_markdown() -> None:
    payload = build_visual_evidence_from_note_images(
        "## 开场\n\n![封面页展示课程标题](scene_0001.jpg)\n\n这里是正文。",
        [
            {
                "filename": "frames/scene_0001.jpg",
                "url": "/jobs/task/artifacts/frame?file=scene_0001.jpg",
                "timestamp_seconds": 1,
            }
        ],
    )

    assert payload["visual_evidence_status"] == "unavailable"
    assert payload["visual_evidence"] == []
    assert "scene_0001.jpg" not in payload["summary_markdown"]
    assert "这里是正文" in payload["summary_markdown"]


def test_duplicate_visual_hash_keeps_first_relevant_image() -> None:
    payload = build_visual_evidence_from_note_images(
        (
            "## 核心流程\n\n"
            "![流程图展示第一步](scene_0001.jpg)\n\n"
            "![流程图展示同一页重复内容](scene_0002.jpg)\n"
        ),
        [
            {
                "filename": "frames/scene_0001.jpg",
                "url": "/jobs/task/artifacts/frame?file=scene_0001.jpg",
                "timestamp_seconds": 10,
                "visual_hash": "ff00ff00ff00ff00",
            },
            {
                "filename": "frames/scene_0002.jpg",
                "url": "/jobs/task/artifacts/frame?file=scene_0002.jpg",
                "timestamp_seconds": 30,
                "visual_hash": "ff00ff00ff00ff01",
            },
        ],
    )

    assert payload["visual_evidence_status"] == "completed"
    assert len(payload["visual_evidence"]) == 1
    assert payload["visual_evidence"][0]["artifact_url"].endswith("scene_0001.jpg")
    assert "scene_0002.jpg" not in payload["summary_markdown"]


def test_visual_evidence_density_caps_short_notes() -> None:
    markdown = (
        "## 核心概念\n\n"
        "![流程图 A](scene_0001.jpg)\n\n"
        "![结构图 B](scene_0002.jpg)\n\n"
        "![代码片段 C](scene_0003.jpg)\n\n"
        "简短说明。"
    )
    payload = build_visual_evidence_from_note_images(
        markdown,
        [
            {
                "filename": f"frames/scene_{index:04d}.jpg",
                "url": f"/jobs/task/artifacts/frame?file=scene_{index:04d}.jpg",
                "timestamp_seconds": index * 10,
                "visual_hash": visual_hash,
            }
            for index, visual_hash in enumerate(
                ["0000000000000000", "ffffffffffffffff", "00ff00ff00ff00ff"],
                start=1,
            )
        ],
    )

    assert len(payload["visual_evidence"]) == 2
    assert "scene_0003.jpg" not in payload["summary_markdown"]


def test_coerce_visual_requests_clamps_long_windows() -> None:
    requests = _coerce_visual_requests(
        {
            "requests": [
                {
                    "note_section": "核心流程",
                    "start_seconds": 10,
                    "end_seconds": 140,
                    "reason": "这里需要展示流程图",
                    "query": "选择清晰的流程图页面",
                    "priority": "urgent",
                    "max_images": 3,
                }
            ]
        },
        [{"start": 0, "end": 180, "text": "segment"}],
        max_requests=8,
    )

    assert requests[0]["id"] == "vr_001"
    assert requests[0]["priority"] == "medium"
    assert requests[0]["purpose"] == "key_moment"
    assert requests[0]["max_images"] == 2
    assert requests[0]["end_seconds"] - requests[0]["start_seconds"] <= 60


def test_visual_requests_to_frame_segments_preserves_request_context() -> None:
    segments = visual_requests_to_frame_segments([
        {
            "id": "vr_001",
            "note_section": "核心流程",
            "start_seconds": 12,
            "end_seconds": 18,
            "reason": "流程图",
            "query": "清晰流程图",
        }
    ])

    assert segments == [
        {
            "start": 12,
            "end": 18,
            "text": "清晰流程图",
            "visual_request_id": "vr_001",
            "note_section": "核心流程",
            "query": "清晰流程图",
            "reason": "流程图",
            "purpose": None,
        }
    ]


def test_processing_visual_selector_reads_qwen_secret_from_backend_config() -> None:
    # The pipeline receives secret resolution from its composition root, so it
    # stays independent of hosted configuration while retaining the same
    # backend-config lookup behavior.
    pipeline_source = Path("backend/core/media_job.py").read_text(encoding="utf-8")
    local_source = Path("backend/routers/local_processing.py").read_text(encoding="utf-8")

    assert 'visual_api_key = secret_resolver(qwen_api_key, "qwen_api_key")' in pipeline_source
    assert "secret_resolver=resolve_secret" in local_source
    assert 'visual_api_key = (qwen_api_key or "").strip()' not in pipeline_source


def test_inject_visual_evidence_references_near_matching_heading() -> None:
    markdown = "## 核心流程\n\n这里说明流程。\n\n## 结论\n\n这里是结论。"
    injected = inject_visual_evidence_references(
        markdown,
        [
            {
                "note_section": "核心流程",
                "filename": "ts_0004_0.jpg",
                "caption": "流程图展示关键步骤",
            }
        ],
    )

    assert "## 核心流程\n\n![流程图展示关键步骤](ts_0004_0.jpg)" in injected


def test_injected_visual_reference_can_be_promoted() -> None:
    markdown = inject_visual_evidence_references(
        "## 核心流程\n\n这里说明流程。",
        [{"note_section": "核心流程", "filename": "ts_0004_0.jpg", "caption": "流程图展示关键步骤"}],
    )
    rewritten = rewrite_note_image_references(
        markdown,
        [{"filename": "frames/ts_0004_0.jpg", "url": "/jobs/task/artifacts/frame?file=ts_0004_0.jpg"}],
    )
    payload = build_visual_evidence_from_note_images(
        rewritten,
        [{"filename": "frames/ts_0004_0.jpg", "url": "/jobs/task/artifacts/frame?file=ts_0004_0.jpg"}],
    )

    assert payload["visual_evidence_status"] == "completed"
    assert payload["visual_evidence"][0]["note_section"] == "核心流程"


def test_medium_selection_becomes_key_moment_without_inline_injection() -> None:
    selection = {
        "request_id": "vr_001",
        "note_section": "核心流程",
        "filename": "ts_0004_0.jpg",
        "caption": "流程图展示关键步骤",
        "reason": "适合复查流程图，但不必插入正文。",
        "confidence": "medium",
        "purpose": "key_moment",
        "timestamp_seconds": 4.0,
    }
    markdown = inject_visual_evidence_references("## 核心流程\n\n这里说明流程。", [selection])
    evidence_payload = build_visual_evidence_from_note_images(
        markdown,
        [{"filename": "frames/ts_0004_0.jpg", "url": "/jobs/task/artifacts/frame?file=ts_0004_0.jpg"}],
    )
    moments_payload = build_visual_key_moments(
        [selection],
        [{"filename": "frames/ts_0004_0.jpg", "url": "/jobs/task/artifacts/frame?file=ts_0004_0.jpg", "timestamp_seconds": 4.0}],
        visual_evidence=evidence_payload["visual_evidence"],
    )

    assert "![" not in markdown
    assert evidence_payload["visual_evidence"] == []
    assert moments_payload["visual_key_moments_status"] == "completed"
    assert moments_payload["visual_key_moments"][0]["caption"] == "流程图展示关键步骤"
    assert moments_payload["visual_key_moments"][0]["artifact_url"].endswith("ts_0004_0.jpg")


def test_low_confidence_or_low_information_selection_is_not_user_visible() -> None:
    low_confidence = {
        "filename": "low.jpg",
        "caption": "低置信画面",
        "confidence": "low",
        "purpose": "key_moment",
    }
    low_information = {
        "filename": "blank.jpg",
        "caption": "空白画面",
        "confidence": "medium",
        "purpose": "key_moment",
    }
    payload = build_visual_key_moments(
        [low_confidence, low_information],
        [
            {"filename": "frames/low.jpg", "url": "/jobs/task/artifacts/frame?file=low.jpg"},
            {"filename": "frames/blank.jpg", "url": "/jobs/task/artifacts/frame?file=blank.jpg", "low_information": True},
        ],
    )

    assert payload["visual_key_moments"] == []
    assert payload["visual_key_moments_status"] == "unavailable"
