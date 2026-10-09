"""The note-writing rules come from a file the owner can edit.

Requirements (2026-10-09):

1. With no rules file of this machine's own, Claude gets the rules shipped with
   FluentFlow, followed by the fixed input/output contract.
2. A rules file in the data directory replaces the shipped rules; the contract
   is still sent, because the parser depends on it.
3. An edit to that file reaches the next note, without a restart.
4. An empty file falls back to the shipped rules.
5. Both ways of reaching Claude send the same text.
"""

from __future__ import annotations

import subprocess

from backend.core import claude_code_note, claude_vision, note_rules


def _use_rules_file(monkeypatch, tmp_path, text=None):
    path = tmp_path / "note_writing_rules.md"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("FLUENTFLOW_NOTE_RULES_PATH", str(path))
    return path


def test_shipped_rules_and_contract_when_there_is_no_own_file(monkeypatch, tmp_path):
    _use_rules_file(monkeypatch, tmp_path)
    prompt = claude_vision.system_prompt()
    assert prompt.startswith(note_rules.DEFAULT_RULES_PATH.read_text(encoding="utf-8").strip())
    assert claude_vision.NOTE_CONTRACT in prompt


def test_own_file_replaces_the_rules_and_keeps_the_contract(monkeypatch, tmp_path):
    _use_rules_file(monkeypatch, tmp_path, "只写会议决定，不写过程。")
    prompt = claude_vision.system_prompt()
    assert prompt.startswith("只写会议决定，不写过程。")
    assert "学习笔记" not in prompt.split(claude_vision.NOTE_CONTRACT)[0]
    assert claude_vision.NOTE_CONTRACT in prompt


def test_an_edit_reaches_the_next_note(monkeypatch, tmp_path):
    path = _use_rules_file(monkeypatch, tmp_path, "第一版要求")
    assert claude_vision.system_prompt().startswith("第一版要求")
    path.write_text("第二版要求", encoding="utf-8")
    assert claude_vision.system_prompt().startswith("第二版要求")


def test_an_empty_file_falls_back_to_the_shipped_rules(monkeypatch, tmp_path):
    _use_rules_file(monkeypatch, tmp_path, "  \n")
    rules = note_rules.load_rules()
    assert rules.is_default
    assert claude_vision.system_prompt().startswith(rules.text)


class _Stream:
    def __init__(self, sent):
        self.sent = sent

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        class Block:
            type = "text"
            text = '{"note_markdown": "# 标题\\n\\n正文", "basis_note": ""}'

        class Message:
            content = [Block()]
            stop_reason = "end_turn"
            usage = None

        return Message()


def test_both_channels_send_the_same_rules(monkeypatch, tmp_path):
    _use_rules_file(monkeypatch, tmp_path, "统一的要求")
    sent = {}

    class Client:
        class messages:  # noqa: N801 - mirrors the SDK shape
            @staticmethod
            def stream(**request):
                sent["api"] = request["system"]
                return _Stream(sent)

    claude_vision.write_visual_note("[00:00] 你好", [], api_key="sk-ant-test", client=Client())

    monkeypatch.setattr(claude_code_note, "cli_path", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr(claude_code_note, "unavailable_reason", lambda: None)

    def runner(command, **kwargs):
        sent["cli"] = command[command.index("--system-prompt") + 1]
        line = '{"type": "result", "is_error": false, "result": "{\\"note_markdown\\": \\"# 标题\\", \\"basis_note\\": \\"\\"}"}'
        return subprocess.CompletedProcess(command, 0, line, "")

    claude_code_note.write_visual_note("[00:00] 你好", [], runner=runner)

    assert sent["api"] == sent["cli"]
    assert sent["api"].startswith("统一的要求")
