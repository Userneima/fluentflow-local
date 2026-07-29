#!/usr/bin/env python3
"""Build note quality evaluation reports from FluentFlow result JSON files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.note_quality import (  # noqa: E402
    build_note_quality_collection,
    load_note_quality_input,
    load_review_file,
    render_note_quality_markdown,
)


def _input_paths(paths: list[Path], input_dir: Path | None) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        expanded = path.expanduser().resolve()
        if expanded.is_dir():
            result.extend(sorted(expanded.glob("*.json")))
        else:
            result.append(expanded)
    if input_dir:
        result.extend(sorted(input_dir.expanduser().resolve().glob("*.json")))
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in result:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, default=[], help="Result/job JSON file or a directory of JSON files")
    parser.add_argument("--input-dir", type=Path, default=None, help="Directory of result/job JSON files")
    parser.add_argument("--review", type=Path, default=None, help="Optional JSON review payload")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/note_quality_eval"), help="Report output directory")
    parser.add_argument("--stdout", action="store_true", help="Print the Markdown report instead of only writing files")
    args = parser.parse_args()

    paths = _input_paths(args.input, args.input_dir)
    if not paths:
        print("No input JSON files provided", file=sys.stderr)
        return 2

    review = load_review_file(args.review.expanduser().resolve()) if args.review else None
    items = [load_note_quality_input(path, review=review) for path in paths]
    collection = build_note_quality_collection(items)
    markdown = render_note_quality_markdown(collection)

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    runs_path = output_dir / "runs.json"
    report_path = output_dir / "report.md"
    runs_path.write_text(json.dumps(collection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(markdown, encoding="utf-8")

    if args.stdout:
        print(markdown)
    else:
        print(json.dumps({
            "status": "ok",
            "run_count": collection["run_count"],
            "runs": str(runs_path),
            "report": str(report_path),
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
