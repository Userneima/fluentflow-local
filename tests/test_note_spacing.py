"""Blank lines in a review note.

Requirements (owner, 2026-11-16):
1. A heading has one blank line before it and none after it.
2. A line ending with a colon runs straight into what follows it; a table keeps
   its blank line above (Obsidian needs it to show the table).
3. A 核对 quote next to a list item has no blank line on that side.
4. The PDF and Feishu still see each list and quote as its own block.
"""

from __future__ import annotations

import markdown

from backend.core import note_pdf, note_spacing

WRITTEN = """# 第三课：复习笔记

## 四、提示缓存


先讲为什么。

### （一）第一指标

**结论**：命中率是第一指标。

**为什么**：

- 上下文越来越长。
- 让模型更快。

**怎么做的**（Jessie 查到的规则）：

- **打标规则**：最多四个标。

> **核对（2026-11-05）**：顺序写反了。

- **TTL**：5 分钟和 1 小时。

> **核对（2026-11-05）**：默认 5 分钟。

（转录稿补充）：

- 都是让 AI 对照源码讲的。

**例子和数字**：

| 轮次 | input |
| --- | --- |
| 第一轮 | 2431 |
### （二）下一节
正文。

```text
代码里的空行

保持原样
```
"""

WANTED = """# 第三课：复习笔记

## 四、提示缓存
先讲为什么。

### （一）第一指标
**结论**：命中率是第一指标。

**为什么**：
- 上下文越来越长。
- 让模型更快。

**怎么做的**（Jessie 查到的规则）：
- **打标规则**：最多四个标。
> **核对（2026-11-05）**：顺序写反了。
- **TTL**：5 分钟和 1 小时。
> **核对（2026-11-05）**：默认 5 分钟。

（转录稿补充）：
- 都是让 AI 对照源码讲的。

**例子和数字**：

| 轮次 | input |
| --- | --- |
| 第一轮 | 2431 |

### （二）下一节
正文。

```text
代码里的空行

保持原样
```
"""


def test_blank_lines_sit_where_the_owner_reads_them():
    assert note_spacing.tidy(WRITTEN) == WANTED


def test_tidying_twice_changes_nothing():
    assert note_spacing.tidy(WANTED) == WANTED


def test_a_check_between_list_items_still_prints_as_its_own_quote():
    html = markdown.markdown(note_pdf._markdown_for_print(WANTED), extensions=["fenced_code", "sane_lists"])
    assert html.count("<blockquote>") == 2
    assert "<li><strong>打标规则</strong>：最多四个标。</li>" in html.replace("\n", "")
    assert "顺序写反了" not in html.split("<blockquote>")[0]


def test_what_feishu_gets_has_each_quote_and_list_apart():
    spaced = note_spacing.for_renderers(WANTED)
    assert "- **打标规则**：最多四个标。\n\n> **核对" in spaced
    assert "顺序写反了。\n\n- **TTL**" in spaced
    assert "**为什么**：\n\n- 上下文越来越长。" in spaced
    assert "```text\n代码里的空行\n\n保持原样\n```" in spaced
