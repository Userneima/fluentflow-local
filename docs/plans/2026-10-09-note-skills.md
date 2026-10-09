# 写笔记改用 skill（2026-10-09 定）

## 要求

### 两份 skill

1. **默认笔记 skill**：从个人笔记规则里拆出来，作为产品默认风格。不署名，不含任何个人信息。
2. **元 skill**：帮每个用户做出他自己的笔记 skill。

### 默认 skill 的来源和内容

来源：`yuchao-agent-skills/skills/shared/feishu-doc-ops/references/` 下的 `personal-note-style.md` 和 `article-study-note.md`。只留写笔记的通用规则，去掉飞书操作和格式、名字和个人习惯、翻译、版权、文档命名。至少包含：

- 只写录像里能确认的内容，推测不写成讲者说过的话。
- 术语在正文第一次出现的地方用括号解释，不单独设词汇表一节。
- 保留讲者的限定词：「往往」不写成「只」，「至少 100 条」不写成「约 100 条」。
- 同一个意思只写一处，写完检查同一个结论有没有在几节里重复出现。
- 案例放在它支撑的观点旁边，不集中成一节。
- 内容多的节分层级标题，短的节保持一层。
- 结尾的回顾直接列条目，不加引导句。

`ai_prompts.py` 的 `FLUENTFLOW_SYSTEM_PROMPT` 和画面笔记提示词（现在在 `backend/core/note_writing_rules.md` 加 `claude_vision.NOTE_CONTRACT`）里讲画面怎么用、图注怎么写的部分，并进默认 skill 或留在原处，不能丢。

### 元 skill：接住用户的纠正

- 用户在编辑器里改过的笔记，和生成时的原稿对比，从差异里提出候选规则（例：每次都删掉词汇表，改成正文括号解释）。
- 每条候选规则经用户确认后才写进他自己的 skill。
- 写进去的规则用正面说法，写该怎么做。
- 起步时可以让用户给几份满意的笔记提炼初始风格，这只是起点。

### 保险

- 任何一份 skill 改动后（包括用户确认新规则），用新旧两版对同一段录像各生成一次，并排给用户看，确认后才生效。
- 默认 skill 的改动走 `scripts/evaluate_note_quality.py`，同一批录像新旧两版并排比较。

### 技术约束

- `claude_code_note.py` 调用 Claude Code 时的隔离保留：只放开加载产品自己目录里的笔记 skill，绝不读用户 `~/.claude` 下的个人 skill。
- 默认 skill 和每个用户的 skill 都放在产品自己管理的位置。

## 实测（2026-10-09，Claude Code 2.1.226）

- `--safe-mode` 会连 `--plugin-dir` 指定的产品插件一起关掉。
- `--setting-sources project --plugin-dir <产品插件>`，工作目录是空的临时目录：可用 skill 只有产品插件里的和 Claude Code 内置的；`~/.claude/skills` 下的个人 skill、`~/.claude/CLAUDE.md`、记忆都没有加载。账号邮箱仍会出现在上下文里（来自登录，不来自 `~/.claude`）。
- `--bare` 不读钥匙串，订阅登录用不了。
- 通过 Claude Code 的 skill 机制加载时，是否打开 skill 由模型自己决定；API Key 通道（Messages API）没有这个机制。
- `scripts/evaluate_note_quality.py` 现在只读已有任务结果出报告，不会用两版 skill 生成笔记，需要补上「同一批录像、两版 skill 各写一遍」这一步。

## 决定（2026-10-09）

- **加载方式**：skill 是产品目录里独立的 SKILL.md 文件夹，产品每次写笔记读出来交给 Claude，两条通道都一定用上。调用 Claude Code 的隔离保持现状（`--safe-mode` 不变），不走 Claude Code 自己的 skill 机制。
- **确认入口**：在产品界面里做。设置页「我的笔记风格」：列出候选规则逐条确认；确认前用新旧两版对同一段录像各写一份，并排显示。

## 做法

1. 两份 skill 和读取：默认 skill 在 `backend/note_skills/default/`，元 skill 在 `backend/note_skills/meta/`，用户自己的 skill 在数据目录 `note_skills/mine/`，每次生效都留一份旧版本可退回。画面笔记按「用户的 skill，没有就用默认」来写，固定的输入输出要求仍在代码里。
2. 纠正循环后台：找出用户改过的笔记，和原稿对比，交给元 skill 提候选规则；确认的规则由元 skill 并进用户的 skill，生成新旧两版对比，用户确认后生效。
3. 设置页界面。
4. `scripts/evaluate_note_quality.py` 补上「同一批录像、新旧两版 skill 各写一遍并排比较」。
