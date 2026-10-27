#!/usr/bin/env python3
"""Build note quality evaluation reports from FluentFlow result JSON files.

With ``--compare-skills OLD NEW --task ID``, write each task's note with two
versions of a note skill and put them side by side; every change to the
default note skill goes through this before it ships.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
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


def _read_skill(spec: str) -> str:
    """A skill's rules from a file path, or from git as ``git:REV:PATH``."""
    from backend.core.note_skills import NoteSkill

    if spec.startswith("git:"):
        _, rev, path = spec.split(":", 2)
        text = subprocess.run(
            ["git", "show", f"{rev}:{path}"], cwd=PROJECT_ROOT, check=True, capture_output=True, text=True,
        ).stdout
    else:
        text = Path(spec).expanduser().read_text(encoding="utf-8")
    return NoteSkill(text.strip(), Path(spec), False).rules


_COMPARE_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>笔记 skill 对比</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
<style>
body{{font:15px/1.7 -apple-system,"PingFang SC",sans-serif;margin:0;background:#f6f6f4;color:#222}}
header{{padding:16px 24px;background:#fff;border-bottom:1px solid #ddd}}
section{{padding:16px 24px}} h2.task{{font-size:16px;margin:24px 0 8px}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.col{{background:#fff;border:1px solid #ddd;border-radius:6px;padding:12px 20px;overflow:auto;max-height:80vh}}
.col h1{{font-size:20px}} .col h2{{font-size:17px}} .col h3{{font-size:15px}}
.meta{{font-size:12px;color:#666;border-bottom:1px solid #eee;padding-bottom:6px;margin-bottom:8px}}
img{{max-width:100%}}
</style></head><body>
<header><b>笔记 skill 对比</b>　左：{old_label}　右：{new_label}</header>
<section>{body}</section>
<script>document.querySelectorAll('.md').forEach(e=>{{e.innerHTML=marked.parse(e.textContent)}})</script>
</body></html>"""


def _compare_skills(args) -> int:
    import html

    from backend.core import note_preview
    from backend.core.visual_note_job import _frame_artifact_path

    old_rules, new_rules = _read_skill(args.compare_skills[0]), _read_skill(args.compare_skills[1])
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    blocks, runs = [], []
    for task_id in args.task:
        try:
            pair = note_preview.compare(task_id, old_rules, new_rules)
        except Exception as exc:  # noqa: BLE001 - one task's failure skips that task only
            print(f"{task_id}: {exc}", file=sys.stderr)
            continue
        columns = []
        for draft in pair:
            name = "old" if draft is pair[0] else "new"
            (output_dir / f"{task_id}_{name}.md").write_text(draft.markdown + "\n", encoding="utf-8")
            shown = re.sub(
                r"\]\((note_\d+\.jpg)\)",
                lambda m: "](" + Path(str(_frame_artifact_path(task_id, m.group(1)))).as_uri() + ")",
                draft.markdown,
            )
            columns.append(
                f'<div class="col"><div class="meta">{html.escape(draft.label)} · {len(draft.markdown)} 字'
                f' · 引用 {len(draft.cited)} 张截图</div><div class="md">{html.escape(shown)}</div></div>'
            )
            runs.append({"task_id": task_id, "version": name, "chars": len(draft.markdown),
                         "cited": draft.cited, "usage": draft.usage})
        blocks.append(f'<h2 class="task">{html.escape(task_id)}</h2><div class="pair">{"".join(columns)}</div>')
    page = output_dir / "compare.html"
    page.write_text(_COMPARE_PAGE.format(
        old_label=html.escape(args.compare_skills[0]), new_label=html.escape(args.compare_skills[1]),
        body="".join(blocks),
    ), encoding="utf-8")
    (output_dir / "compare.json").write_text(json.dumps(runs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "tasks": len(blocks), "page": str(page)}, ensure_ascii=False, indent=2))
    return 0 if blocks else 1


def _score(args) -> int:
    """Score each recording's note under the test folder against its sheet;
    the report goes into that recording's 评分 folder."""
    from datetime import date

    from backend.core import job_store, note_scoring
    from backend.core.claude_text import claude_chat
    from backend.core.local_config import resolve_secret

    found = claude_chat(resolve_secret(None, "anthropic_api_key"))
    if found is None:
        print("no model to judge with (Claude not reachable)", file=sys.stderr)
        return 2
    chat, checker = found
    root = args.score.expanduser().resolve()
    rows = []
    for folder in sorted(path for path in root.iterdir() if (path / "考题.md").is_file()):
        if args.only and not any(word in folder.name for word in args.only):
            continue
        note_file = folder / args.note_file if args.note_file else None
        if note_file is not None and note_file.is_file():
            note = note_file.read_text(encoding="utf-8")
        else:
            task_id = re.search(r"任务\s+(\S+)", (folder / "来源.txt").read_text(encoding="utf-8")).group(1)
            note = str(((job_store.get_job(task_id) or {}).get("result") or {}).get("summary_markdown") or "")
        if not note.strip():
            print(f"{folder.name}: no note", file=sys.stderr)
            continue
        sheet = note_scoring.parse_sheet((folder / "考题.md").read_text(encoding="utf-8"))
        # The series' correction document, if the recording's folder has one
        # (a copy named 校正文档.md in the test folder), so names it fixes are
        # not counted as made up.
        corrections_file = folder / "校正文档.md"
        corrections = corrections_file.read_text(encoding="utf-8") if corrections_file.is_file() else ""
        result = note_scoring.score(chat, sheet, note, (folder / "转录稿.txt").read_text(encoding="utf-8"),
                                    checker=checker, corrections=corrections)
        out = folder / "评分"
        out.mkdir(exist_ok=True)
        stem = f"{date.today().isoformat()}-{args.label}"
        (out / f"{stem}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        (out / f"{stem}.md").write_text(note_scoring.report_markdown(folder.name, args.label, result), encoding="utf-8")
        rows.append({"recording": folder.name, "summary": note_scoring.summary_line(result), "report": str(out / f"{stem}.md")})
        print(f"{folder.name}: {note_scoring.summary_line(result)}", flush=True)
    print(json.dumps({"status": "ok", "scored": rows}, ensure_ascii=False, indent=2))
    return 0 if rows else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--compare-skills", nargs=2, metavar=("OLD", "NEW"), default=None,
        help="Write each --task's note with two note skills (file path or git:REV:PATH) and put them side by side",
    )
    parser.add_argument("--task", action="append", default=[], help="Task id for --compare-skills (repeatable)")
    parser.add_argument("--score", type=Path, default=None,
                        help="Test folder (e.g. private/笔记考题): score each recording's note against its 考题.md")
    parser.add_argument("--label", default="现有笔记", help="Name of the version being scored, used in the report file name")
    parser.add_argument("--note-file", default=None, help="Score this file inside each recording folder instead of the task's note")
    parser.add_argument("--only", action="append", default=[], help="Only recordings whose folder name contains this (repeatable)")
    parser.add_argument("--input", action="append", type=Path, default=[], help="Result/job JSON file or a directory of JSON files")
    parser.add_argument("--input-dir", type=Path, default=None, help="Directory of result/job JSON files")
    parser.add_argument("--review", type=Path, default=None, help="Optional JSON review payload")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/note_quality_eval"), help="Report output directory")
    parser.add_argument("--stdout", action="store_true", help="Print the Markdown report instead of only writing files")
    args = parser.parse_args()

    if args.score:
        return _score(args)

    if args.compare_skills:
        if not args.task:
            print("--compare-skills needs at least one --task", file=sys.stderr)
            return 2
        return _compare_skills(args)

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
