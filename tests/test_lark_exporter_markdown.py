"""The note that lands in Feishu is the note that was written.

No network here. ``markdown_to_feishu_blocks`` is the flat block writer the
exporter falls back to when the OpenAPI convert route is off or fails; what it
produces is what the user opens in Feishu, so every structure in a note —
headings, lists, code, tables, emphasis, screenshots — has to arrive as that
structure and with its text intact. And when the export fails, the reason the
user reads must say which thing went wrong: no permission on the folder is a
different problem from a folder that does not exist.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

import backend.core.lark_exporter as lx
from backend.core.local_entry_guards import friendly_error
from backend.core.local_error_diagnostics import diagnose_error

NOTE = """# 投资人视角下的 AI 浪潮

## 一、开场

### 1.1 背景

这一讲先讲**为什么现在**，再讲方法。

- 第一点：市场
- 第二点：技术

1. 先看数据
2. 再看案例

```python
print("hello")
x = 1
```

| 阶段 | 时间 | 结论 |
| --- | --- | --- |
| 早期 | 2015 | 观望 |
| 现在 | 2024 | 入场 |

命令用 `git status` 查看。

![第 12 分钟的幻灯片](frames/slide-12.jpg)
"""


def _text(block: dict) -> str:
    body = next(value for key, value in block.items() if key != "block_type" and isinstance(value, dict))
    return "".join(element["text_run"]["content"] for element in body.get("elements", []))


def _blocks_of_type(blocks: list[dict], block_type: int) -> list[dict]:
    return [block for block in blocks if block["block_type"] == block_type]


@pytest.fixture()
def blocks():
    return lx.markdown_to_feishu_blocks(NOTE)


# ── structure arrives as structure ──────────────────────────────────────────

def test_headings_keep_their_level_and_text(blocks):
    h1 = _blocks_of_type(blocks, lx._BT_H1)
    h2 = _blocks_of_type(blocks, lx._BT_H2)
    h3 = _blocks_of_type(blocks, lx._BT_H3)
    assert [_text(b) for b in h1] == ["投资人视角下的 AI 浪潮"]
    assert [_text(b) for b in h2] == ["一、开场"]
    assert [_text(b) for b in h3] == ["1.1 背景"]
    assert "heading1" in h1[0] and "heading2" in h2[0] and "heading3" in h3[0]


def test_bullets_and_numbered_items_are_list_items_without_their_markers(blocks):
    bullets = [_text(b) for b in _blocks_of_type(blocks, lx._BT_BULLET)]
    ordered = [_text(b) for b in _blocks_of_type(blocks, lx._BT_ORDERED)]
    assert bullets[:2] == ["第一点：市场", "第二点：技术"]
    assert ordered == ["先看数据", "再看案例"]


def test_a_code_block_is_one_code_block_with_every_line(blocks):
    code = _blocks_of_type(blocks, lx._BT_CODE)
    assert len(code) == 1
    assert _text(code[0]) == 'print("hello")\nx = 1'
    assert not any("```" in _text(b) for b in blocks), "fence markers never reach the document"


def test_a_table_arrives_as_a_readable_labelled_list_not_pipe_text(blocks):
    """Feishu's flat writer has no table block; the rows become one labelled
    item per row so the reader still sees every cell with its column name."""
    bullets = _blocks_of_type(blocks, lx._BT_BULLET)
    rows = [b for b in bullets if _text(b).startswith("阶段")]
    assert [_text(b) for b in rows] == ["阶段：早期", "阶段：现在"]
    cells = [_text(b) for b in bullets if _text(b).startswith(("时间", "结论"))]
    assert cells == ["时间：2015", "结论：观望", "时间：2024", "结论：入场"]
    label_runs = [
        run["text_run"] for run in rows[0]["bullet"]["elements"] if run["text_run"]["content"] == "阶段"
    ]
    assert label_runs and label_runs[0]["text_element_style"] == {"bold": True}, "the column name is the bold label"
    assert not any("|" in _text(b) for b in blocks), "no raw pipe syntax reaches the document"


def test_bold_is_a_bold_run_and_the_asterisks_are_gone(blocks):
    paragraph = next(b for b in _blocks_of_type(blocks, lx._BT_TEXT) if "为什么现在" in _text(b))
    runs = paragraph["text"]["elements"]
    assert [r["text_run"]["content"] for r in runs] == ["这一讲先讲", "为什么现在", "，再讲方法。"]
    assert runs[1]["text_run"]["text_element_style"] == {"bold": True}
    assert "text_element_style" not in runs[0]["text_run"]
    assert "**" not in _text(paragraph)


def test_inline_code_keeps_its_text(blocks):
    paragraph = next(b for b in _blocks_of_type(blocks, lx._BT_TEXT) if "git status" in _text(b))
    assert "git status" in _text(paragraph)


def test_inline_code_is_code_not_literal_backticks(blocks):
    paragraph = next(b for b in _blocks_of_type(blocks, lx._BT_TEXT) if "git status" in _text(b))
    assert "`" not in _text(paragraph)
    assert any(
        r["text_run"].get("text_element_style", {}).get("inline_code") for r in paragraph["text"]["elements"]
    )


# ── screenshots ─────────────────────────────────────────────────────────────

def test_a_screenshot_that_exists_becomes_an_image_block_at_its_place(tmp_path):
    frame = tmp_path / "slide-12.jpg"
    frame.write_bytes(b"jpeg bytes")
    blocks, refs = lx._markdown_to_feishu_blocks_with_image_refs(
        NOTE, image_resolver=lambda src: frame if src.endswith("slide-12.jpg") else None
    )

    images = _blocks_of_type(blocks, lx._BT_IMAGE)
    assert len(images) == 1 and images[0] == {"block_type": lx._BT_IMAGE, "image": {}}
    assert refs == [{
        "block_index": blocks.index(images[0]),
        "alt": "第 12 分钟的幻灯片",
        "src": "frames/slide-12.jpg",
        "path": frame,
    }]
    assert blocks.index(images[0]) == len(blocks) - 1, "the image sits where the note put it"


def test_a_screenshot_that_cannot_be_found_is_named_in_text_rather_than_dropped(blocks):
    assert not _blocks_of_type(blocks, lx._BT_IMAGE)
    fallback = [b for b in _blocks_of_type(blocks, lx._BT_TEXT) if _text(b).startswith("图片：")]
    assert [_text(b) for b in fallback] == ["图片：第 12 分钟的幻灯片"]


def test_a_screenshot_with_no_caption_is_named_by_its_file():
    blocks = lx.markdown_to_feishu_blocks("![](frames/slide-3.jpg)")
    assert _text(blocks[0]) == "图片：slide-3.jpg"


# ── the reason an export failed says which thing went wrong ─────────────────

def _feishu_refusal(monkeypatch, status: int, body: dict) -> None:
    def refuse(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, status, "refused", hdrs=None, fp=io.BytesIO(json.dumps(body).encode("utf-8"))
        )

    monkeypatch.setattr(lx.urllib.request, "urlopen", refuse)


def _user_reads(error: Exception) -> str:
    diagnosis = diagnose_error(error)
    return f"{friendly_error(error)} {diagnosis.get('next_action') or ''}"


def test_a_folder_the_app_may_not_write_to_is_reported_as_a_permission_problem(monkeypatch):
    _feishu_refusal(monkeypatch, 403, {"code": 99991672, "msg": "permission denied: folder_token"})

    with pytest.raises(RuntimeError) as caught:
        lx._create_empty_doc_with_token("tok", lx.DEFAULT_BASE_URL, "笔记", "fldcn123", 5)

    shown = _user_reads(caught.value)
    assert "飞书" in shown and "权限" in shown
    assert "视频" not in shown, "a refused document write is not a refused video download"


def test_a_folder_that_does_not_exist_is_reported_as_a_missing_folder_not_a_missing_task(monkeypatch):
    _feishu_refusal(monkeypatch, 404, {"code": 1770001, "msg": "folder not found"})

    with pytest.raises(RuntimeError) as caught:
        lx._create_empty_doc_with_token("tok", lx.DEFAULT_BASE_URL, "笔记", "fldcn-gone", 5)

    shown = _user_reads(caught.value)
    assert "飞书" in shown
    assert "文件夹" in shown or "目标" in shown
    assert "任务" not in shown


def test_the_two_failures_read_differently(monkeypatch):
    _feishu_refusal(monkeypatch, 403, {"code": 99991672, "msg": "permission denied"})
    with pytest.raises(RuntimeError) as forbidden:
        lx._create_empty_doc_with_token("tok", lx.DEFAULT_BASE_URL, "笔记", "f", 5)
    _feishu_refusal(monkeypatch, 404, {"code": 1770001, "msg": "folder not found"})
    with pytest.raises(RuntimeError) as missing:
        lx._create_empty_doc_with_token("tok", lx.DEFAULT_BASE_URL, "笔记", "f", 5)

    assert diagnose_error(forbidden.value)["code"] != diagnose_error(missing.value)["code"]
    assert "403" in str(forbidden.value) and "404" in str(missing.value), "the raw text keeps the status for the log"


def test_a_folder_path_is_only_sent_when_one_was_chosen(monkeypatch):
    sent: list[dict] = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def accept(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return _Resp(json.dumps({"code": 0, "data": {"document": {"document_id": "doc1"}}}).encode("utf-8"))

    monkeypatch.setattr(lx.urllib.request, "urlopen", accept)

    assert lx._create_empty_doc_with_token("tok", lx.DEFAULT_BASE_URL, "笔记", None, 5) == "doc1"
    assert lx._create_empty_doc_with_token("tok", lx.DEFAULT_BASE_URL, "笔记", "fldcn123", 5) == "doc1"
    assert sent == [{"title": "笔记"}, {"title": "笔记", "folder_token": "fldcn123"}]


def test_the_exporter_knows_which_notes_carry_a_table():
    assert lx.markdown_contains_table(NOTE) is True
    assert lx.markdown_contains_table("# 没有表格\n\n只有正文。") is False
