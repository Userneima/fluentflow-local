"""Experiment: can a model match each note section to a span of the cut video?

Read-only. Takes a copy of a task's result_json (from fluentflow_jobs.sqlite,
opened read-only), sends the note (headings tagged with ids) and the cut-video
transcript (lines tagged with their start time) to the local Claude Code
subscription -- the same channel and model alias FluentFlow writes notes with --
and writes the returned section -> time span table as JSON beside the input.

Nothing here is product logic; it exists to decide whether the "note follows
playback" feature is worth building. See docs/方案/2026-10-23-笔记跟随播放对齐验证.md.

    python3 scripts/experiments/align_note_sections.py <result.json> <out.json>
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.core import claude_code_note  # noqa: E402

CHUNK_SECONDS = 15.0
PART_MARKER = re.compile(r"<!--\s*第\s*\d+/\d+\s*部分.*?-->")
HEADING = re.compile(r"^(#{2,3})\s+(.*\S)\s*$")

SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "primary": {
                        "type": "object",
                        "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
                        "required": ["start", "end"],
                    },
                    "others": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
                            "required": ["start", "end"],
                        },
                    },
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "note": {"type": "string"},
                },
                "required": ["id", "primary", "others", "confidence"],
            },
        }
    },
    "required": ["sections"],
}

SYSTEM = (
    "你在给一篇视频笔记的每个章节标出它对应录像里的哪一段。笔记是按主题重组、改写过的，"
    "字面和转录稿对不上，也不一定按录像顺序，要按意思判断。只输出要求的 JSON。"
)


def clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def parse_clock(text: str) -> float:
    parts = [float(p) for p in text.strip().split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def transcript_lines(segments: list[dict]) -> list[tuple[float, str]]:
    lines: list[tuple[float, str]] = []
    start, buf, last_speaker = None, [], None
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = seg.get("speaker") or ""
        if start is None:
            start = float(seg["start"])
        if speaker and speaker != last_speaker:
            buf.append(f"【{speaker.replace('SPEAKER_', 'S')}】")
            last_speaker = speaker
        buf.append(text)
        if float(seg["end"]) - start >= CHUNK_SECONDS:
            lines.append((start, " ".join(buf)))
            start, buf = None, []
    if buf and start is not None:
        lines.append((start, " ".join(buf)))
    return lines


def note_sections(markdown: str) -> tuple[str, list[dict]]:
    tagged, sections = [], []
    for line in PART_MARKER.sub("", markdown).splitlines():
        m = HEADING.match(line)
        if m:
            sid = f"S{len(sections) + 1}"
            sections.append({"id": sid, "level": len(m.group(1)), "title": m.group(2)})
            tagged.append(f"{line}  〔{sid}〕")
        else:
            tagged.append(line)
    return "\n".join(tagged), sections


def build_prompt(note: str, lines: list[tuple[float, str]], duration: float) -> str:
    transcript = "\n".join(f"[{clock(t)}] {text}" for t, text in lines)
    return (
        f"录像总长 {clock(duration)}。下面先是笔记，二级、三级标题后面用〔S数字〕标了编号；"
        "然后是带时间的转录稿，每行开头是这一行在录像里的开始时间。\n\n"
        "给每个编号的章节标出它主要对应的录像时间段（primary），start 和 end 用转录稿里出现过的行首时间，"
        "end 取这一段之后紧接的那一行的时间。一节的内容来自录像里不相邻的几处时，primary 放讲得最多的那一段，"
        "其他几处放 others；只来自一处时 others 为空。二级标题下面有三级标题时，二级标题的时间段覆盖它下面各小节的主要部分。"
        "总结性的章节（决定、待办、还没定的这类）按其中各条出处的主要那一段标，others 列其他出处。"
        "confidence：high 表示你在转录稿里找到了明确对应的话；medium 表示大致在这一带；low 表示找不到清楚的对应。"
        "low 或 medium 时在 note 里用一句话说为什么。每个编号都要出现一次。\n\n"
        f"=== 笔记 ===\n{note}\n\n=== 转录稿 ===\n{transcript}\n"
    )


def run(prompt: str) -> dict:
    binary = claude_code_note.cli_path()
    if not binary:
        raise SystemExit(claude_code_note.unavailable_reason())
    model = claude_code_note.subscription_model()
    command = [
        binary, "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(SCHEMA, ensure_ascii=False),
        "--system-prompt", SYSTEM,
        "--allowed-tools", "",
        "--disallowed-tools", "Bash,Edit,Write,Read,WebFetch,WebSearch,Task,Glob,Grep",
        "--safe-mode", "--strict-mcp-config",
        "--model", model,
    ]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="fluentflow-align-") as scratch:
        done = subprocess.run(
            command, input=prompt, capture_output=True, text=True, encoding="utf-8",
            cwd=scratch, env=claude_code_note._subprocess_env(), timeout=3600, check=False,
        )
    elapsed = time.monotonic() - started
    raw = (done.stdout or "").strip()
    try:
        payload = json.loads(raw[raw.index("{"):])
    except (ValueError, json.JSONDecodeError):
        print("RAW STDOUT:", raw[:2000], "\nSTDERR:", (done.stderr or "")[:2000], file=sys.stderr)
        raise SystemExit(f"could not parse CLI output (exit {done.returncode})")
    if payload.get("is_error"):
        print(json.dumps(payload, ensure_ascii=False)[:2000], file=sys.stderr)
        raise SystemExit("CLI reported an error")
    result = payload.get("structured_output") or payload.get("result")
    if isinstance(result, str):
        result = json.loads(result[result.index("{"):])
    return {
        "elapsed_seconds": round(elapsed, 1),
        "requested_model": model,
        "model_usage": payload.get("modelUsage"),
        "total_cost_usd": payload.get("total_cost_usd"),
        "usage": payload.get("usage"),
        "result": result,
    }


def main() -> None:
    source, out = Path(sys.argv[1]), Path(sys.argv[2])
    task = json.loads(source.read_text(encoding="utf-8"))
    segments = task["display_segments"]
    duration = float(segments[-1]["end"])
    note, sections = note_sections(task["summary_markdown"])
    lines = transcript_lines(segments)
    prompt = build_prompt(note, lines, duration)
    print(f"sections={len(sections)} lines={len(lines)} prompt_chars={len(prompt)}", flush=True)
    answer = run(prompt)
    by_id = {s["id"]: s for s in sections}
    for row in answer["result"]["sections"]:
        meta = by_id.get(row["id"], {})
        row["title"] = meta.get("title", "?")
        row["level"] = meta.get("level")
        row["primary_seconds"] = [parse_clock(row["primary"]["start"]), parse_clock(row["primary"]["end"])]
    answer["sections_in_note"] = sections
    answer["transcript_lines"] = lines
    answer["duration_seconds"] = duration
    out.write_text(json.dumps(answer, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"done in {answer['elapsed_seconds']}s, cost ${answer['total_cost_usd']}, model {answer['model_usage'] and list(answer['model_usage'])}")


if __name__ == "__main__":
    main()
