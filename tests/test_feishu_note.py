"""The note as written to Feishu, so the Feishu copy reads like the md and can
come back to it.

Requirements (owner, 2026-10-21, after a round trip of the Luo Fuli interview
note through Feishu):
1. A question or tip box from Obsidian is a Feishu callout (高亮块), and which
   kind it was can be told from it; its words are all there.
2. A table or code block inside a box is written right after the box, not lost
   (a Feishu callout cannot hold one).
3. A link to a chapter of the note leads to that chapter in the Feishu
   document. One whose chapter cannot be found keeps its words.
4. Each screenshot uploaded is recorded with the Feishu file it became, and the
   document with its version number after the export.
5. Tables stay tables; headings, lists and bold are left as they are; the
   part markers of a long note are dropped.
"""

from __future__ import annotations

from pathlib import Path

import backend.core.lark_cli_exporter as cli
from backend.core import feishu_note
from tests.test_lark_cli_export import DOC_URL, TASK, FakeCli, _flag, _frame_src, _frames, _ok

NOTE = """# 访谈

<!-- 第 1/3 部分（00:00–73:27） -->

## 一、开场

- **算力配比**：3:1:1。
- 有子项：
  > [!question] 现在的 memory 如何？
  > 有记忆但是比较薄。
- 下一项

> [!question] 究竟是哪些细节？
> soul.md 存性格；
> user.md 存你是谁；

> [!tip] 静态信息在[[#五、Agent 怎么"自学习"：模型和框架同步往前走|第五章]]。

> [!question] 我想到了 DSH
>
> | 改的是哪一环 | 例子 |
> |---|---|
> | 模型本身 | 切线路 |

| | Qwen | DeepSeek |
|---|---|---|
| 路线 | 开放 | 专注 |

普通引用：

> 原话。

## 五、Agent 怎么"自学习"：模型和框架同步往前走

见[[#一、开场]]和[[#没有这一章|别处]]。
"""


def _converted() -> feishu_note.FeishuMarkdown:
    return feishu_note.to_feishu(NOTE)


def test_boxes_become_callouts_that_say_their_kind_with_all_their_words():
    md = _converted().markdown
    question, _, _ = feishu_note.CALLOUT_STYLES["question"]
    tip, _, _ = feishu_note.CALLOUT_STYLES["tip"]
    assert question != tip
    assert f'<callout emoji="{question}"' in md and f'<callout emoji="{tip}"' in md
    assert "[!question]" not in md and "[!tip]" not in md
    block = md[md.index("究竟是哪些细节"):md.index("</callout>", md.index("究竟是哪些细节"))]
    assert "soul.md 存性格；" in block and "user.md 存你是谁；" in block
    assert "> soul.md" not in md


def test_a_box_inside_a_list_item_stays_in_the_item():
    md = _converted().markdown
    assert '  <callout emoji="❓"' in md
    assert "  有记忆但是比较薄。" in md
    assert md.index("- 有子项：") < md.index("现在的 memory") < md.index("- 下一项")


def test_a_table_in_a_box_follows_the_box_instead_of_being_lost():
    md = _converted().markdown
    box_end = md.index("</callout>", md.index("我想到了 DSH"))
    assert md.index("| 改的是哪一环 | 例子 |") > box_end
    assert "| 模型本身 | 切线路 |" in md
    assert "> | 模型本身" not in md


def test_tables_headings_lists_and_quotes_are_left_as_they_are():
    md = _converted().markdown
    for kept in ("| | Qwen | DeepSeek |", "| 路线 | 开放 | 专注 |", "## 一、开场",
                 "- **算力配比**：3:1:1。", "> 原话。"):
        assert kept in md


def test_part_markers_are_dropped():
    assert "<!--" not in _converted().markdown


def test_chapter_links_become_unique_placeholders_with_their_words():
    converted = _converted()
    assert [link.label for link in converted.links] == ["第五章", "一、开场", "别处"]
    assert len({link.placeholder for link in converted.links}) == 3
    for link in converted.links:
        assert f"[{link.label}]({link.placeholder})" in converted.markdown
    assert "[[#" not in converted.markdown


def test_headings_are_matched_by_their_words():
    xml = ('<h2 id="h1">一、开场</h2><p id="p">x</p>'
           '<h2 id="h5">五、Agent 怎么&quot;自学习&quot;：<b>模型</b>和框架同步往前走</h2>')
    found = feishu_note.headings(xml)
    assert found[feishu_note.heading_key('五、Agent 怎么"自学习"：模型和框架同步往前走')] == "h5"
    assert found[feishu_note.heading_key("一、开场")] == "h1"


# ── the export ──────────────────────────────────────────────────────────────

OUTLINE = ('<title id="d">访谈</title><h1 id="hT">访谈</h1><h2 id="h1">一、开场</h2>'
           '<h2 id="h5">五、Agent 怎么"自学习"：模型和框架同步往前走</h2>')


def _export(tmp_path: Path, runner: FakeCli) -> dict:
    root = _frames(tmp_path, "note_0001.jpg")
    note = NOTE + f"\n![开场画面]({_frame_src('note_0001.jpg')})\n"
    return cli.export_markdown_via_lark_cli("访谈", note, task_id=TASK, artifact_root=root, runner=runner)


def test_each_chapter_link_is_pointed_at_its_heading_in_the_document(tmp_path):
    runner = FakeCli(outline=OUTLINE)
    result = _export(tmp_path, runner)

    replaced = {_flag(c["cmd"], "--pattern"): _flag(c["cmd"], "--content") for c in runner.of("+update")}
    links = {link.label: link.placeholder for link in _converted().links}
    assert replaced[f"[第五章]({links['第五章']})"] == f"[第五章]({DOC_URL}#h5)"
    assert replaced[f"[一、开场]({links['一、开场']})"] == f"[一、开场]({DOC_URL}#h1)"
    assert replaced[f"[别处]({links['别处']})"] == "别处", "an unknown chapter keeps its words"
    assert result["sync"]["link_errors"] == ["别处"]


def test_the_document_its_version_and_each_screenshots_file_are_recorded(tmp_path):
    result = _export(tmp_path, FakeCli(outline=OUTLINE))

    sync = result["sync"]
    assert sync["url"] == DOC_URL and sync["doc_id"]
    assert sync["revision_id"] == 5, "the version the document reports when read after the last write"
    assert sync["feishu_modified_at"] == 1700000000
    assert sync["images"] == [{"src": _frame_src("note_0001.jpg"), "name": "note_0001.jpg",
                               "line": f"![开场画面]({_frame_src('note_0001.jpg')})", "caption": "图 1：开场画面",
                               "file_token": "file1", "block_id": "blk1"}]
    assert sync["note_digest"]


def test_the_document_stands_when_the_links_cannot_be_fixed(tmp_path):
    def broken(call):
        from tests.test_lark_cli_export import _fail
        return _fail({"type": "api_error", "message": "boom"})

    result = _export(tmp_path, FakeCli(outline=OUTLINE, replace=broken))
    assert result["ok"] and result["url"] == DOC_URL
    assert set(result["sync"]["link_errors"]) == {"第五章", "一、开场", "别处"}


def test_what_feishu_would_eat_is_escaped_outside_code():
    md = feishu_note.to_feishu("a ~约~ 数，~~删掉~~，<tag> 留着，<b>粗</b>，`<code>~` 原样。").markdown
    assert "\\~约\\~" in md and "~~删掉~~" in md
    assert "\\<tag>" in md and "<b>粗</b>" in md
    assert "`<code>~`" in md


def test_a_box_title_is_bold_in_feishu_and_plain_again_when_it_comes_back():
    md = feishu_note.to_feishu("> [!question] 究竟是哪些细节？\n> 回答一行\n").markdown
    assert "**究竟是哪些细节？**" in md
    back = feishu_note.from_feishu('<callout emoji="❓">\n**究竟是哪些细节？**  \n回答一行\n</callout>\n',
                                   feishu_note.Way(doc_id="", headings_by_id={}, images=[]))
    assert back == "> [!question] 究竟是哪些细节？\n> 回答一行"


def test_a_chapter_link_in_a_box_title_is_fixed_too(tmp_path):
    """Feishu shows a link in a bold title as [**label**](...)."""
    def replace(call):
        pattern = _flag(call["cmd"], "--pattern")
        ok = pattern.startswith("[**") or "第五章" not in pattern
        return _ok({"document": {"revision_id": 9}, "result": "success" if ok else "failed"})

    runner = FakeCli(outline=OUTLINE, replace=replace)
    result = cli.export_markdown_via_lark_cli("访谈", NOTE, task_id=TASK, artifact_root=tmp_path, runner=runner)

    fixed = [_flag(c["cmd"], "--content") for c in runner.of("+update") if "第五章" in _flag(c["cmd"], "--pattern")]
    assert fixed[-1] == f"[**第五章**]({DOC_URL}#h5)"
    assert "第五章" not in result["sync"]["link_errors"]


def test_a_box_title_with_a_link_comes_back_plain_with_its_link():
    back = feishu_note.from_feishu(
        '<callout emoji="💡">\n**这里讲的在**[**第五章**](https://x.feishu.cn/docx/D#h5)**。**\n</callout>\n',
        feishu_note.Way(doc_id="D", headings_by_id={"h5": "五、Agent 怎么自学习"}, images=[]),
    )
    assert back == "> [!tip] 这里讲的在[[#五、Agent 怎么自学习|第五章]]。"
