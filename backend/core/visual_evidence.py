"""Build final visual evidence from Agent-selected note image references."""

from __future__ import annotations

import re
import math
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_HIGH_VALUE_TERMS = (
    "流程", "结构", "框架", "公式", "代码", "表格", "图表", "数据", "对比",
    "定义", "步骤", "方法", "模型", "架构", "结论", "板书", "演示", "界面",
)
_LOW_VALUE_TERMS = (
    "封面", "目录", "致谢", "结束页", "片头", "片尾", "过渡", "纯标题",
    "标题页", "纯人像", "人物讲话", "字幕条",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _plain_markdown(value: str) -> str:
    text = re.sub(r"[*_`>#]+", "", value or "")
    return re.sub(r"\s+", " ", text).strip()


def _heading_before(markdown: str, position: int) -> str:
    heading = ""
    for line in markdown[:position].splitlines():
        match = _HEADING_RE.match(line)
        if match:
            heading = _plain_markdown(match.group(1))
    return heading


def _plain_without_images(markdown: str) -> str:
    return _plain_markdown(_IMAGE_RE.sub("", markdown or ""))


def _slug_match(left: str, right: str) -> bool:
    lhs = _plain_markdown(left)
    rhs = _plain_markdown(right)
    return bool(lhs and rhs and (lhs in rhs or rhs in lhs))


def _artifact_by_filename(frame_artifacts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for artifact in frame_artifacts:
        if not isinstance(artifact, dict):
            continue
        filename = Path(_text(artifact.get("filename"))).name
        if filename:
            indexed[filename] = artifact
    return indexed


def _image_target_name(target: str) -> str:
    parsed = urlparse(_text(target))
    query_file = parse_qs(parsed.query).get("file", [""])[0]
    if query_file:
        return Path(unquote(query_file)).name
    return Path(unquote(parsed.path or target)).name


def _hex_hamming(left: str, right: str) -> int | None:
    try:
        return bin(int(left, 16) ^ int(right, 16)).count("1")
    except Exception:
        return None


def _looks_low_value(alt_text: str, section: str) -> bool:
    text = f"{alt_text} {section}"
    if not any(term in text for term in _LOW_VALUE_TERMS):
        return False
    return not any(term in text for term in _HIGH_VALUE_TERMS)


def _priority_score(alt_text: str, section: str, position: int) -> int:
    text = f"{alt_text} {section}"
    score = 0
    for term in _HIGH_VALUE_TERMS:
        if term in text:
            score += 6
    if section:
        score += 2
    if position < 1200:
        score += 1
    return score


def _visual_limit(markdown: str, candidates: list[dict[str, Any]]) -> int:
    text_chars = len(_plain_without_images(markdown))
    by_text = max(2, min(8, math.ceil(max(text_chars, 1) / 1000) * 2))
    timestamps = [
        float(item["artifact"].get("timestamp_seconds"))
        for item in candidates
        if isinstance(item.get("artifact"), dict) and item["artifact"].get("timestamp_seconds") is not None
    ]
    if not timestamps:
        return by_text
    by_duration = max(2, min(8, math.ceil(max(timestamps) / 180) * 3))
    return min(by_text, by_duration, 8)


def _candidate_image_refs(markdown: str, frame_artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    artifact_index = _artifact_by_filename(frame_artifacts)
    candidates: list[dict[str, Any]] = []
    for match in _IMAGE_RE.finditer(markdown or ""):
        alt_text = _plain_markdown(match.group(1))
        image_name = _image_target_name(match.group(2))
        artifact = artifact_index.get(image_name)
        if not alt_text or not artifact:
            continue
        section = _heading_before(markdown, match.start())
        if artifact.get("low_information") is True:
            continue
        if _looks_low_value(alt_text, section):
            continue
        candidates.append({
            "match": match,
            "alt_text": alt_text,
            "image_name": image_name,
            "artifact": artifact,
            "section": section,
            "score": _priority_score(alt_text, section, match.start()),
        })
    return candidates


def _select_visual_candidates(markdown: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    limit = _visual_limit(markdown, candidates)
    selected: list[dict[str, Any]] = []
    section_counts: dict[str, int] = {}
    used_names: set[str] = set()
    used_hashes: list[str] = []
    used_timestamps: list[float] = []
    for candidate in sorted(candidates, key=lambda item: (-int(item.get("score") or 0), item["match"].start())):
        if len(selected) >= limit:
            break
        artifact = candidate["artifact"]
        image_name = candidate["image_name"]
        if image_name in used_names:
            continue
        section = candidate.get("section") or ""
        if section_counts.get(section, 0) >= 2:
            continue
        timestamp = artifact.get("timestamp_seconds")
        ts = None
        if timestamp is not None:
            try:
                ts = float(timestamp)
            except (TypeError, ValueError):
                ts = None
            if ts is not None and any(abs(ts - old) < 3.0 for old in used_timestamps):
                continue
        visual_hash = _text(artifact.get("visual_hash") or artifact.get("perceptual_hash"))
        if visual_hash:
            duplicate = False
            for old_hash in used_hashes:
                distance = _hex_hamming(visual_hash, old_hash)
                if distance is not None and distance <= 4:
                    duplicate = True
                    break
            if duplicate:
                continue
        selected.append(candidate)
        used_names.add(image_name)
        if section:
            section_counts[section] = section_counts.get(section, 0) + 1
        if visual_hash:
            used_hashes.append(visual_hash)
        if ts is not None:
            used_timestamps.append(ts)
    return sorted(selected, key=lambda item: item["match"].start())


def _remove_unselected_images(markdown: str, selected: list[dict[str, Any]]) -> str:
    selected_spans = {(item["match"].start(), item["match"].end()) for item in selected}

    def replace(match: re.Match[str]) -> str:
        if (match.start(), match.end()) in selected_spans:
            return match.group(0)
        return ""

    return re.sub(r"\n{3,}", "\n\n", _IMAGE_RE.sub(replace, markdown or "")).strip()


def build_visual_evidence_from_note_images(
    markdown: str,
    frame_artifacts: list[dict[str, Any]],
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    """Promote note image references into final visual evidence.

    Raw candidate frames are not enough to become screenshots in notes. This
    function only promotes frames when the generated note explicitly references
    them with Markdown image syntax and provides a non-empty alt text.
    """
    candidates = _candidate_image_refs(markdown, frame_artifacts)
    selected = _select_visual_candidates(markdown, candidates)
    evidence: list[dict[str, Any]] = []
    visual_artifacts: dict[str, dict[str, Any]] = {}

    for candidate in selected:
        alt_text = candidate["alt_text"]
        artifact = candidate["artifact"]
        visual_id = f"visual_{len(evidence) + 1:03d}"
        artifact_url = _text(artifact.get("url"))
        timestamp = artifact.get("timestamp_seconds")
        artifact_provider = _text(provider or artifact.get("provider"))
        visual_artifacts[visual_id] = {
            **artifact,
            "kind": visual_id,
            "content_type": artifact.get("content_type") or "image/jpeg",
            "timestamp_seconds": timestamp,
            "provider": artifact_provider or None,
        }
        evidence.append({
            "id": visual_id,
            "timestamp_seconds": timestamp,
            "reason": alt_text,
            "note_section": candidate.get("section") or "",
            "source": "visual_review_grid",
            "confidence": "high",
            "provider": artifact_provider or None,
            "artifact_kind": visual_id,
            "artifact_url": artifact_url,
        })

    filtered_markdown = _remove_unselected_images(markdown, selected) if _IMAGE_RE.search(markdown or "") else markdown
    unavailable_reason = (
        "Agent 候选截图没有通过全局密度、去重或价值过滤；最终笔记不插入截图。"
        if candidates and not evidence
        else "没有可确认的截图证据；候选帧不会自动插入笔记。"
    )
    return {
        "summary_markdown": filtered_markdown,
        "visual_evidence": [
            {key: value for key, value in item.items() if value not in (None, "")}
            for item in evidence
        ],
        "visual_artifacts": {
            key: {field: value for field, value in artifact.items() if value not in (None, "")}
            for key, artifact in visual_artifacts.items()
        },
        "visual_evidence_status": "completed" if evidence else "unavailable",
        "visual_evidence_reason": (
            "多模态 Agent 已在笔记中选择截图，并通过全局密度、去重和价值过滤。"
            if evidence
            else unavailable_reason
        ),
    }


def _evidence_filenames(visual_evidence: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for item in visual_evidence:
        if not isinstance(item, dict):
            continue
        target = _text(item.get("artifact_url"))
        if target:
            names.add(_image_target_name(target))
    return {name for name in names if name}


def build_visual_key_moments(
    selections: list[dict[str, Any]],
    frame_artifacts: list[dict[str, Any]],
    *,
    visual_evidence: list[dict[str, Any]] | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    """Build user-visible key visual candidates from model-selected frames.

    Key moments are broader than inline visual evidence: they can help users
    revisit charts, code, formulas, UI states, or process diagrams without
    being inserted into the note body. Raw frame artifacts are still diagnostic
    and are not promoted unless the vision selector gave a useful reason.
    """
    artifact_index = _artifact_by_filename(frame_artifacts)
    inline_names = _evidence_filenames(visual_evidence or [])
    moments: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for selection in selections:
        if not isinstance(selection, dict):
            continue
        confidence = _text(selection.get("confidence")).lower()
        if confidence not in {"high", "medium"}:
            continue
        purpose = _text(selection.get("purpose")).lower()
        filename = Path(_text(selection.get("filename"))).name
        if not filename or filename in used_names or filename in inline_names:
            continue
        artifact = artifact_index.get(filename)
        if not artifact or artifact.get("low_information") is True:
            continue
        caption = _plain_markdown(_text(selection.get("caption")))
        reason = _plain_markdown(_text(selection.get("reason") or caption))
        if not caption and not reason:
            continue
        if purpose == "inline_evidence" and confidence == "high":
            # If a high-confidence inline frame did not survive evidence
            # density/deduping, keep it as a review candidate rather than
            # silently losing a potentially useful learning landmark.
            purpose = "key_moment"
        if purpose not in {"key_moment", "inline_evidence"}:
            purpose = "key_moment"
        moment_id = f"key_visual_{len(moments) + 1:03d}"
        artifact_url = _text(artifact.get("url"))
        moment = {
            "id": moment_id,
            "request_id": _text(selection.get("request_id")),
            "timestamp_seconds": selection.get("timestamp_seconds") if selection.get("timestamp_seconds") is not None else artifact.get("timestamp_seconds"),
            "caption": caption or reason,
            "reason": reason or caption,
            "note_section": _text(selection.get("note_section")),
            "confidence": confidence,
            "purpose": "key_moment",
            "source": "visual_frame_selection",
            "provider": _text(provider or artifact.get("provider")),
            "artifact_url": artifact_url,
            "filename": _text(artifact.get("filename")),
        }
        moments.append({key: value for key, value in moment.items() if value not in (None, "")})
        used_names.add(filename)
    return {
        "visual_key_moments": moments,
        "visual_key_moments_status": "completed" if moments else "unavailable",
        "visual_key_moments_reason": (
            "视觉模型选择了适合复查但未插入正文的关键画面。"
            if moments
            else "没有可用的关键画面候选；原始帧仅保留为诊断数据。"
        ),
    }


def rewrite_note_image_references(markdown: str, frame_artifacts: list[dict[str, Any]]) -> str:
    artifact_index = _artifact_by_filename(frame_artifacts)

    def replace(match: re.Match[str]) -> str:
        alt_text = match.group(1)
        raw_target = _text(match.group(2))
        artifact = artifact_index.get(Path(raw_target).name)
        artifact_url = _text((artifact or {}).get("url"))
        if not artifact_url:
            return match.group(0)
        return f"![{alt_text}]({artifact_url})"

    return _IMAGE_RE.sub(replace, markdown or "")


def inject_visual_evidence_references(markdown: str, selections: list[dict[str, Any]]) -> str:
    """Insert selected frame references near their planned note sections.

    The semantic decision comes from the visual planning/selection models. This
    helper only performs deterministic placement and leaves final density and
    low-value filtering to ``build_visual_evidence_from_note_images``.
    """
    text = markdown or ""
    if not text.strip() or not selections:
        return text

    existing_targets = {_image_target_name(match.group(2)) for match in _IMAGE_RE.finditer(text)}
    pending = [
        selection for selection in selections
        if isinstance(selection, dict)
        and _text(selection.get("filename"))
        and _text(selection.get("caption"))
        and _text(selection.get("confidence")).lower() in {"", "high"}
        and (_text(selection.get("purpose")).lower() in {"", "inline_evidence"})
        and Path(_text(selection.get("filename"))).name not in existing_targets
    ]
    if not pending:
        return text

    lines = text.splitlines()
    inserted_indices: set[int] = set()
    fallback_lines: list[str] = []
    for selection in pending:
        filename = Path(_text(selection.get("filename"))).name
        caption = _text(selection.get("caption"))
        note_section = _text(selection.get("note_section"))
        image_line = f"![{caption}]({filename})"
        insert_at: int | None = None
        if note_section:
            for index, line in enumerate(lines):
                match = _HEADING_RE.match(line)
                if match and _slug_match(match.group(1), note_section):
                    insert_at = index + 1
                    while insert_at < len(lines) and not lines[insert_at].strip():
                        insert_at += 1
                    break
        if insert_at is None:
            fallback_lines.append(image_line)
            continue
        while insert_at in inserted_indices:
            insert_at += 1
        lines.insert(insert_at, image_line)
        inserted_indices.add(insert_at)

    next_text = "\n".join(lines).strip()
    if fallback_lines:
        next_text = f"{next_text}\n\n## 视觉证据\n\n" + "\n\n".join(fallback_lines)
    return re.sub(r"\n{3,}", "\n\n", next_text).strip()


__all__ = [
    "build_visual_evidence_from_note_images",
    "build_visual_key_moments",
    "inject_visual_evidence_references",
    "rewrite_note_image_references",
]
