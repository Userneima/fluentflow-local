"""The note skills FluentFlow writes with.

Requirements (2026-10-09, docs/plans/2026-10-09-note-skills.md):

1. With no skill of the user's own, Claude gets the product's default skill,
   followed by the fixed input/output contract.
2. The user's own skill, in a folder the product manages, replaces the default;
   the contract is still sent.
3. A change reaches the next note without a restart; an empty skill falls back
   to the default.
4. Every replacement of the user's skill keeps the version it replaced.
5. Both channels send the same text, and a preview can write with a given skill
   without making it the one in effect.
6. The default skill carries no personal information and contains the agreed
   writing rules; each skill's name matches its folder.
7. Calling Claude Code still starts it with every personal customization off.
"""

from __future__ import annotations

import re
import subprocess

from backend.core import claude_code_note, claude_vision, note_skills


def _own_skill(monkeypatch, tmp_path, text=None):
    folder = tmp_path / "mine"
    monkeypatch.setenv("FLUENTFLOW_NOTE_SKILL_DIR", str(folder))
    if text is not None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(text, encoding="utf-8")
    return folder


def test_default_skill_and_contract_without_an_own_skill(monkeypatch, tmp_path):
    _own_skill(monkeypatch, tmp_path)
    prompt = claude_vision.system_prompt()
    assert prompt.startswith(note_skills.default_skill().rules)
    assert not prompt.startswith("---"), "the frontmatter is for the file, not the model"
    assert prompt.endswith(claude_vision.NOTE_CONTRACT)


def test_own_skill_replaces_the_default_and_keeps_the_contract(monkeypatch, tmp_path):
    _own_skill(monkeypatch, tmp_path, "---\nname: mine\ndescription: x\n---\n\n只写会议决定。")
    prompt = claude_vision.system_prompt()
    assert prompt.startswith("只写会议决定。")
    assert claude_vision.NOTE_CONTRACT in prompt


def test_a_change_reaches_the_next_note_and_empty_falls_back(monkeypatch, tmp_path):
    folder = _own_skill(monkeypatch, tmp_path, "第一版")
    assert claude_vision.system_prompt().startswith("第一版")
    (folder / "SKILL.md").write_text("第二版", encoding="utf-8")
    assert claude_vision.system_prompt().startswith("第二版")
    (folder / "SKILL.md").write_text("  \n", encoding="utf-8")
    assert note_skills.load_note_skill().is_default


def test_saving_keeps_the_version_it_replaced(monkeypatch, tmp_path):
    _own_skill(monkeypatch, tmp_path, "第一版")
    note_skills.save_user_skill("第二版")
    note_skills.save_user_skill("第三版")
    assert note_skills.load_note_skill().text == "第三版"
    kept = [path.read_text(encoding="utf-8").strip() for path in note_skills.skill_history()]
    assert kept == ["第二版", "第一版"]


def test_both_channels_send_the_same_text_and_a_preview_uses_its_own(monkeypatch, tmp_path):
    _own_skill(monkeypatch, tmp_path, "生效中的版本")
    sent = {}

    class Stream:
        def __init__(self, request):
            sent.setdefault("api", []).append(request["system"])

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            class Block:
                type = "text"
                text = '{"note_markdown": "# 标题", "basis_note": ""}'

            class Message:
                content = [Block()]
                stop_reason = "end_turn"
                usage = None

            return Message()

    class Client:
        class messages:  # noqa: N801 - mirrors the SDK shape
            @staticmethod
            def stream(**request):
                return Stream(request)

    claude_vision.write_visual_note("[00:00] 你好", [], api_key="sk-ant-test", client=Client())
    claude_vision.write_visual_note("[00:00] 你好", [], api_key="sk-ant-test", client=Client(), rules="待确认的版本")

    monkeypatch.setattr(claude_code_note, "cli_path", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr(claude_code_note, "unavailable_reason", lambda: None)

    def runner(command, **kwargs):
        sent.setdefault("cli", []).append(command[command.index("--system-prompt") + 1])
        line = '{"type": "result", "is_error": false, "result": "{\\"note_markdown\\": \\"# 标题\\", \\"basis_note\\": \\"\\"}"}'
        return subprocess.CompletedProcess(command, 0, line, "")

    claude_code_note.write_visual_note("[00:00] 你好", [], runner=runner)
    claude_code_note.write_visual_note("[00:00] 你好", [], runner=runner, rules="待确认的版本")

    assert sent["api"] == sent["cli"]
    assert sent["api"][0].startswith("生效中的版本")
    assert sent["api"][1].startswith("待确认的版本")
    assert note_skills.load_note_skill().text == "生效中的版本"


def test_the_default_skill_is_anonymous_and_has_the_agreed_rules():
    text = note_skills.default_skill().text
    for personal in ("王愉超", "愉超", "Yuchao", "yuchao", "飞书", "Feishu"):
        assert personal not in text
    for agreed in (
        "只写能从转录文字或截图里确认的内容",
        "第一次出现的地方用括号解释",
        "不另设词汇表",
        "保留讲者的限定词",
        "同一个意思只写一处",
        "案例放在它支撑的观点旁边",
        "内容多的节分层级标题",
        "标题下面直接列条目",
        "图注一句话",
    ):
        assert agreed in text, agreed


def test_each_skill_is_named_after_its_folder():
    for path in (note_skills.DEFAULT_SKILL_PATH, note_skills.META_SKILL_PATH):
        name = re.search(r"^name: (.+)$", path.read_text(encoding="utf-8"), re.MULTILINE).group(1).strip()
        assert name == path.parent.name
        assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)


def test_claude_code_still_starts_with_personal_customizations_off(monkeypatch):
    monkeypatch.setattr(claude_code_note, "cli_path", lambda: "/usr/local/bin/claude")
    command = claude_code_note._command("claude-opus-5", [], inline=True, system_prompt="x")
    assert "--safe-mode" in command
    assert "--plugin-dir" not in command and "--setting-sources" not in command
