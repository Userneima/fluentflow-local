#!/usr/bin/env python3
"""Generate a note-mode evaluation sample from one transcript.

This is the "Runner" half of the note-quality eval loop. Given a transcript or
subtitle file, it produces a candidate note for each requested mode
(``high_fidelity`` and ``chapter_coverage`` by default) using the SAME
production note-generation code path (``summarize_transcript_with_metadata``),
then lays the outputs out exactly how ``scripts/evaluate_note_modes.py`` expects
them.

Workflow (two commands, offline):

    # 1. generate both candidate notes for one transcript
    ./venv/bin/python scripts/generate_note_sample.py \
        --input "path/to/lecture.srt" --name lecture_01

    # 2. build the gold set and score the modes
    ./venv/bin/python scripts/evaluate_note_modes.py \
        --sample-dir data/eval_samples/lecture_01

Accepted inputs:
- ``.srt`` / ``.vtt`` / ``.txt`` / ``.md`` — parsed and cleaned via the product's
  own ``parse_transcript_file`` (same subtitle-to-text logic the product uses).
- ``.json`` — a FluentFlow ``transcript.json`` or result payload with ``text`` /
  ``transcript_text`` / ``segments``.

Notes are generated with the DEFAULT product prompt (no preset). Generation
calls a real model and can take minutes per mode on long transcripts.
Sample directories live under ``data/eval_samples/`` (gitignored); never commit
generated transcripts or notes.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.ai_summarizer import summarize_transcript_with_metadata  # noqa: E402
from backend.core.transcript_parser import parse_transcript_file  # noqa: E402

DEFAULT_MODES = ("high_fidelity", "chapter_coverage")
_TEXT_SUFFIXES = {".srt", ".vtt", ".txt", ".md", ".ass"}


def _load_transcript(path: Path) -> dict[str, Any]:
    """Return {text, segments, source, duration} from a transcript/subtitle file."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"unexpected transcript JSON shape: {path}")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
        text = str(result.get("text") or result.get("transcript_text") or "").strip()
        segments = result.get("segments") if isinstance(result.get("segments"), list) else []
        if not text and segments:
            text = "\n".join(str(seg.get("text") or "").strip() for seg in segments).strip()
        if not text:
            raise ValueError(f"no transcript text found in {path}")
        return {
            "text": text,
            "segments": segments,
            "duration": result.get("duration") or result.get("audio_duration_seconds"),
            "source": str(path),
        }
    if suffix in _TEXT_SUFFIXES:
        parsed = parse_transcript_file(path.read_bytes(), path.name)
        text = (parsed.text or "").strip()
        if not text:
            raise ValueError(f"parsed transcript is empty: {path}")
        return {
            "text": text,
            "segments": [dict(seg) for seg in parsed.segments],
            "duration": parsed.duration,
            "source": str(path),
        }
    raise ValueError(f"unsupported input type '{suffix}'. Use .srt/.vtt/.txt/.md/.json")


def _generate(text: str, mode: str, *, provider: str | None, model: str | None) -> dict[str, Any]:
    started = time.monotonic()
    result = summarize_transcript_with_metadata(text, note_mode=mode, provider=provider, model=model)
    elapsed = round(time.monotonic() - started, 2)
    meta = dataclasses.asdict(result)
    markdown = meta.pop("markdown", "") or ""
    meta["elapsed_seconds"] = elapsed
    meta["summary_chars"] = len(markdown)
    return {"markdown": markdown, "meta": meta}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, required=True, help="Transcript/subtitle file (.srt/.vtt/.txt/.md/.json)")
    parser.add_argument("--name", required=True, help="Sample name (becomes the sample directory name)")
    parser.add_argument("--modes", default=",".join(DEFAULT_MODES), help=f"Comma-separated note modes (default: {','.join(DEFAULT_MODES)})")
    parser.add_argument("--output-root", type=Path, default=Path("data/eval_samples"), help="Root dir for sample directories (default: data/eval_samples)")
    parser.add_argument("--provider", default=None, help="AI provider (default: env AI_PROVIDER or deepseek)")
    parser.add_argument("--model", default=None, help="Model id (default: provider default)")
    parser.add_argument("--overwrite", action="store_true", help="Regenerate even if a <mode>.md already exists")
    args = parser.parse_args()

    input_path = args.input.expanduser().resolve()
    if not input_path.exists():
        print(f"input not found: {input_path}", file=sys.stderr)
        return 2
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if not modes:
        print("no modes requested", file=sys.stderr)
        return 2

    transcript = _load_transcript(input_path)
    sample_dir = (args.output_root / args.name).expanduser().resolve()
    sample_dir.mkdir(parents=True, exist_ok=True)

    transcript_path = sample_dir / "transcript.json"
    transcript_path.write_text(json.dumps({
        "source": transcript["source"],
        "duration": transcript.get("duration"),
        "text_length": len(transcript["text"]),
        "text": transcript["text"],
        "segments": transcript["segments"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "sample": args.name,
        "sample_dir": str(sample_dir),
        "transcript_chars": len(transcript["text"]),
        "modes": modes,
    }, ensure_ascii=False, indent=2))

    generated: dict[str, Any] = {}
    for mode in modes:
        md_path = sample_dir / f"{mode}.md"
        if md_path.exists() and not args.overwrite:
            print(f"skip {mode}: {md_path.name} exists (use --overwrite to regenerate)")
            continue
        print(f"generating {mode} ...")
        try:
            out = _generate(transcript["text"], mode, provider=args.provider, model=args.model)
        except Exception as exc:  # noqa: BLE001 - offline tool, surface per-mode failure
            print(f"  FAILED {mode}: {exc}", file=sys.stderr)
            generated[mode] = {"status": "failed", "error": str(exc)}
            continue
        md_path.write_text(out["markdown"], encoding="utf-8")
        (sample_dir / f"{mode}.json").write_text(json.dumps(out["meta"], ensure_ascii=False, indent=2), encoding="utf-8")
        generated[mode] = {
            "status": "ok",
            "resolved_mode": out["meta"].get("resolved_mode"),
            "summary_chars": out["meta"].get("summary_chars"),
            "elapsed_seconds": out["meta"].get("elapsed_seconds"),
        }
        print(f"  wrote {md_path.name} ({out['meta'].get('summary_chars')} chars, {out['meta'].get('elapsed_seconds')}s)")

    print(json.dumps({"status": "ok", "generated": generated}, ensure_ascii=False, indent=2))
    print(
        "\nNext: score this sample with\n"
        f"  ./venv/bin/python scripts/evaluate_note_modes.py --sample-dir {sample_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
