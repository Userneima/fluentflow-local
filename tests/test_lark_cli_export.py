"""Exporting with the user's own identity (lark-cli) keeps the note whole.

Requirements, from the user's side:

- The note's screenshots show up in the Feishu document, each where the note
  put it, not as broken images pointing at this computer.
- A screenshot that fails to upload does not lose the document; the user is
  told how many did not make it.
- A command that succeeded is never reported as failed because lark-cli
  printed progress text around its JSON; a command that failed says why, in
  lark-cli's own words.

No real lark-cli runs here: a fake runner stands in for ``subprocess.run``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import backend.core.lark_cli_exporter as cli
from backend.core.local_error_diagnostics import diagnose_error

TASK = "task-1"
DOC_ID = "doxcnNEW123"
DOC_URL = "https://example.feishu.cn/wiki/wikNODE"


def _frames(tmp_path: Path, *names: str) -> Path:
    root = tmp_path / "artifacts"
    frames = root / TASK / "frames"
    frames.mkdir(parents=True)
    for name in names:
        (frames / name).write_bytes(b"\xff\xd8 jpeg")
    return root


def _frame_src(name: str) -> str:
    return f"/jobs/{TASK}/artifacts/frame?file={name}"


def _ok(data: dict, *, prefix: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, prefix + json.dumps({"ok": True, "identity": "user", "data": data}), "")


def _fail(error: dict, code: int = 1) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, "", json.dumps({"ok": False, "identity": "user", "error": error}))


class FakeCli:
    def __init__(self, *, create=None, insert=None):
        self.calls: list[dict] = []
        self._create = create or (lambda: _ok({"document": {"document_id": DOC_ID, "url": DOC_URL}}))
        self._insert = insert or (lambda call: _ok({"document_id": DOC_ID, "block_id": "blk1"}))

    def __call__(self, cmd, **kwargs):
        call = {"cmd": list(cmd), "input": kwargs.get("input"), "cwd": kwargs.get("cwd")}
        self.calls.append(call)
        if cmd[1:3] == ["docs", "+create"]:
            return self._create()
        if cmd[1:3] == ["docs", "+media-insert"]:
            return self._insert(call)
        raise AssertionError(f"unexpected command {cmd}")

    def of(self, verb: str) -> list[dict]:
        return [c for c in self.calls if c["cmd"][2] == verb]


def _flag(cmd: list[str], name: str) -> str:
    return cmd[cmd.index(name) + 1]


@pytest.fixture(autouse=True)
def _cli_on_path(monkeypatch):
    monkeypatch.setattr(cli, "_resolve_lark_cli_bin", lambda explicit=None: "/usr/local/bin/lark-cli")


def _export(markdown: str, runner: FakeCli, artifact_root: Path | None) -> dict:
    return cli.export_markdown_via_lark_cli(
        "笔记", markdown, task_id=TASK, artifact_root=artifact_root, runner=runner
    )


# ── pictures ────────────────────────────────────────────────────────────────

def test_each_local_screenshot_is_uploaded_right_above_its_own_caption(tmp_path):
    root = _frames(tmp_path, "note_0090.jpg", "note_0120.jpg")
    note = (
        "# 标题\n\n第一段。\n"
        f"![架构图]({_frame_src('note_0090.jpg')})\n"
        "第二段。\n\n"
        f"![]({_frame_src('note_0120.jpg')})\n"
    )
    runner = FakeCli()

    result = _export(note, runner, root)

    created = runner.of("+create")[0]
    sent = created["input"]
    assert "/jobs/" not in sent, "no image may point at this computer"
    assert "\n\n图 1：架构图\n\n" in sent, "the caption is its own paragraph, where the picture was"
    assert "\n\n图 2：截图\n\n" in sent

    inserts = runner.of("+media-insert")
    assert [_flag(c["cmd"], "--selection-with-ellipsis") for c in inserts] == ["图 1：架构图", "图 2：截图"]
    for call, name in zip(inserts, ["note_0090.jpg", "note_0120.jpg"]):
        assert _flag(call["cmd"], "--doc") == DOC_ID, "media-insert needs the document id, not the wiki link"
        assert _flag(call["cmd"], "--file") == name, "lark-cli only accepts a relative file path"
        assert Path(call["cwd"]) == root / TASK / "frames"
        assert "--before" in call["cmd"] and _flag(call["cmd"], "--as") == "user"

    assert result["url"] == DOC_URL
    assert result["image_count"] == 2
    assert result["image_upload_count"] == 2
    assert result["image_upload_errors"] == []


def test_captions_stay_unique_so_each_picture_finds_its_own_place(tmp_path):
    names = [f"note_{i:04d}.jpg" for i in range(1, 12)]
    root = _frames(tmp_path, *names)
    note = "\n\n".join(f"![同一个说明]({_frame_src(n)})" for n in names)
    runner = FakeCli()

    _export(note, runner, root)

    captions = [_flag(c["cmd"], "--selection-with-ellipsis") for c in runner.of("+media-insert")]
    assert len(captions) == len(set(captions)) == 11
    for caption in captions:
        others = [c for c in captions if c != caption]
        assert not any(caption in other for other in others), "one caption must not match inside another"


def test_a_failed_picture_does_not_fail_the_export_and_is_counted(tmp_path):
    root = _frames(tmp_path, "a.jpg", "b.jpg")
    note = f"![甲]({_frame_src('a.jpg')})\n\n![乙]({_frame_src('b.jpg')})"

    def insert(call):
        if _flag(call["cmd"], "--file") == "b.jpg":
            return _fail({"type": "api_error", "message": "upload failed"})
        return _ok({"block_id": "blk"})

    result = _export(note, FakeCli(insert=insert), root)

    assert result["ok"] is True and result["url"] == DOC_URL
    assert result["image_count"] == 2
    assert result["image_upload_count"] == 1
    assert result["image_upload_errors"] == [_frame_src("b.jpg")]


def test_a_screenshot_whose_file_is_gone_is_named_in_text_and_counted_as_not_uploaded(tmp_path):
    root = _frames(tmp_path)
    runner = FakeCli()

    result = _export(f"![流程图]({_frame_src('missing.jpg')})", runner, root)

    assert "图 1：流程图（截图没有传上去）" in runner.of("+create")[0]["input"]
    assert runner.of("+media-insert") == []
    assert (result["image_count"], result["image_upload_count"]) == (1, 0)


def test_web_images_are_left_for_feishu_to_fetch(tmp_path):
    runner = FakeCli()

    result = _export("![logo](https://example.com/logo.png)", runner, None)

    assert "![logo](https://example.com/logo.png)" in runner.of("+create")[0]["input"]
    assert runner.of("+media-insert") == []
    assert result["image_count"] == 0


def test_an_image_inside_a_code_block_is_code_not_a_picture(tmp_path):
    root = _frames(tmp_path, "a.jpg")
    note = f"```\n![甲]({_frame_src('a.jpg')})\n```"
    runner = FakeCli()

    _export(note, runner, root)

    assert runner.of("+media-insert") == []


# ── the create command ──────────────────────────────────────────────────────

def test_the_note_goes_to_my_library_as_markdown_on_stdin(tmp_path):
    runner = FakeCli()

    _export("# 标题\n\n内容", runner, None)

    cmd = runner.of("+create")[0]["cmd"]
    assert _flag(cmd, "--doc-format") == "markdown"
    assert _flag(cmd, "--content") == "-", "the note goes on stdin, not as one huge argument"
    assert _flag(cmd, "--parent-position") == "my_library"
    assert _flag(cmd, "--title") == "笔记"
    assert _flag(cmd, "--as") == "user"
    assert "--markdown" not in cmd and "--wiki-space" not in cmd, "lark-cli removed these v1 flags"
    assert "内容" in runner.of("+create")[0]["input"]


# ── reading what lark-cli printed ───────────────────────────────────────────

def test_progress_lines_before_the_json_do_not_turn_success_into_failure():
    runner = FakeCli(create=lambda: _ok(
        {"document": {"document_id": DOC_ID, "url": DOC_URL}},
        prefix="[1/2] uploading...\n[2/2] creating {doc}\n",
    ))

    result = _export("内容", runner, None)

    assert result["url"] == DOC_URL


def test_a_failure_carries_lark_clis_own_words():
    runner = FakeCli(create=lambda: _fail({
        "type": "authorization",
        "subtype": "missing_scope",
        "code": 99991679,
        "message": "用户未授权：缺少 wiki:node:create 权限",
        "hint": "lark-cli auth login --scope wiki:node:create",
        "missing_scopes": ["wiki:node:create"],
    }))

    with pytest.raises(cli.LarkCliError) as caught:
        _export("内容", runner, None)

    diagnosis = diagnose_error(caught.value)
    assert diagnosis["code"] == "feishu_missing_scope"
    assert "用户未授权" in diagnosis["detail"], "lark-cli's Chinese message is passed through"
    assert 'lark-cli auth login --scope "wiki:node:create"' in diagnosis["next_action"]


def test_unreadable_output_on_failure_keeps_the_raw_tail():
    runner = FakeCli(create=lambda: subprocess.CompletedProcess([], 2, "", "panic: something broke at step 3"))

    with pytest.raises(cli.LarkCliError) as caught:
        _export("内容", runner, None)

    assert "panic: something broke at step 3" in str(caught.value)
    assert "退出码 2" in str(caught.value)


def test_success_without_a_link_says_the_document_may_exist():
    runner = FakeCli(create=lambda: _ok({"document": {"document_id": DOC_ID}}))

    with pytest.raises(cli.LarkCliError) as caught:
        _export("内容", runner, None)

    assert "我的文档库" in str(caught.value)


def test_missing_lark_cli_is_reported_as_not_installed(monkeypatch):
    monkeypatch.setattr(cli, "_resolve_lark_cli_bin", lambda explicit=None: None)

    with pytest.raises(cli.LarkCliError) as caught:
        cli.export_markdown_via_lark_cli("笔记", "内容", runner=FakeCli())

    assert diagnose_error(caught.value)["code"] == "lark_cli_not_installed"


# ── is lark-cli ready to be the default ─────────────────────────────────────

def _status(user: dict, prefix: str = "") -> object:
    body = prefix + json.dumps({"identities": {"user": user}})
    return lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, body, "")


def test_lark_cli_counts_as_ready_only_with_a_signed_in_user():
    assert cli.lark_cli_ready(runner=_status({"available": True, "tokenStatus": "valid"})) is True
    assert cli.lark_cli_ready(runner=_status({"available": False}, prefix="checking...\n")) is False


def test_lark_cli_is_not_ready_when_it_is_not_installed(monkeypatch):
    monkeypatch.setattr(cli, "_resolve_lark_cli_bin", lambda explicit=None: None)
    assert cli.lark_cli_ready(runner=_status({"available": True})) is False
