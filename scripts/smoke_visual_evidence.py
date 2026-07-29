#!/usr/bin/env python3
"""Smoke test local FFmpeg frame extraction and visual evidence artifact wiring."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.local_keyframe_provider import extract_keyframes  # noqa: E402
from backend.core.visual_evidence import build_visual_evidence_from_note_images, rewrite_note_image_references  # noqa: E402


def _require_bin(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} not found on PATH")
    return path


def _make_test_video(path: Path) -> None:
    ffmpeg = _require_bin("ffmpeg")
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=duration=3:size=320x180:rate=2",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def run_smoke(output_dir: Path) -> dict:
    _require_bin("ffprobe")
    output_dir.mkdir(parents=True, exist_ok=True)
    video_path = output_dir / "visual-evidence-smoke.mp4"
    frame_dir = output_dir / "frames"
    _make_test_video(video_path)

    result = extract_keyframes(
        str(video_path),
        frame_dir,
        segments=[
            {"start": 0.5, "text": "visual smoke opening"},
            {"start": 1.5, "text": "visual smoke middle"},
            {"start": 2.5, "text": "visual smoke ending"},
        ],
        provider="local_ffmpeg",
        scene_threshold=0.05,
        max_scene_frames=3,
        min_gap_seconds=0.5,
    )
    if not result.frames:
        raise RuntimeError("ffmpeg ran, but no candidate frames were extracted")

    frame_artifacts = []
    for index, frame in enumerate(result.frames, start=1):
        frame_path = Path(frame["path"])
        frame_artifacts.append({
            "kind": "frame",
            "filename": f"frames/{frame_path.name}",
            "url": f"/jobs/smoke-visual/artifacts/frame?file={frame_path.name}",
            "timestamp_seconds": frame.get("timestamp_seconds"),
            "provider": frame.get("provider") or result.provider,
            "content_type": "image/jpeg",
            "index": index,
        })

    first_name = Path(frame_artifacts[0]["filename"]).name
    markdown = f"## 视觉证据烟测\n\n![测试画面截图]({first_name})\n"
    rewritten = rewrite_note_image_references(markdown, frame_artifacts)
    visual_payload = build_visual_evidence_from_note_images(rewritten, frame_artifacts, provider=result.provider)
    if visual_payload["visual_evidence_status"] != "completed":
        raise RuntimeError("candidate frame was not promoted to visual evidence")

    return {
        "status": "pass",
        "provider": result.provider,
        "video": str(video_path),
        "frame_count": len(frame_artifacts),
        "first_frame": frame_artifacts[0]["filename"],
        "visual_evidence": visual_payload["visual_evidence"],
        "rewritten_markdown": rewritten,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="Directory for smoke artifacts. Defaults to a temporary directory.")
    args = parser.parse_args()

    if args.output_dir:
        payload = run_smoke(args.output_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="fluentflow-visual-smoke-") as tmp:
            payload = run_smoke(Path(tmp))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
