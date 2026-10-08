"""What the user gets back from a Feishu export, on every route.

Requirements:

- App route (App ID / App Secret): a note with screenshots on this computer
  arrives with its pictures, and the response says how many made it.
- When writing stops half way, the error names the half-written document so
  the user can open or delete it.
- Each kind of failure reads differently and says what to do next, using the
  setting names the Settings page actually shows.
- A manual export is remembered on the task: reopening it shows the link, and
  a failure is kept too.
- The folder setting accepts the folder link as pasted from Feishu.

No network: Feishu calls are replaced by fakes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.core.lark_exporter as lx
import backend.routers.local_feishu_export as local_feishu_export
from backend.core.local_error_diagnostics import diagnose_error

TASK = "task-7"


def _frames(tmp_path: Path, *names: str) -> Path:
    frames = tmp_path / TASK / "frames"
    frames.mkdir(parents=True)
    for name in names:
        (frames / name).write_bytes(b"\xff\xd8 jpeg")
    return tmp_path


class FakeFeishu:
    """Stands in for the OpenAPI calls the app route makes."""

    def __init__(self, monkeypatch, *, fail_batch: int | None = None):
        self.converted = 0
        self.batches: list[list[dict]] = []
        self.uploaded: list[tuple[str, str]] = []
        self.fail_batch = fail_batch
        monkeypatch.setattr(lx.LarkExporter, "_create_empty_doc", lambda self_, title, folder: "doxNEW")
        monkeypatch.setattr(lx, "_get_tenant_token", lambda *a, **k: "t-token")
        monkeypatch.setattr(lx, "_convert_markdown_via_openapi", self._convert)
        monkeypatch.setattr(lx, "_post_block_descendants", lambda *a, **k: {"code": 0})
        monkeypatch.setattr(lx, "_post_block_children", self._children)
        monkeypatch.setattr(lx, "_upload_docx_image", self._upload)
        monkeypatch.setattr(lx, "_replace_docx_image", lambda *a, **k: None)
        monkeypatch.delenv("LARK_OPEN_BASE_URL", raising=False)

    def _convert(self, token, base_url, markdown, timeout):
        self.converted += 1
        return {"first_level_block_ids": ["b1"], "blocks": [{"block_id": "b1", "block_type": 2, "text": {}}]}

    def _children(self, token, base_url, doc_id, blocks, timeout, *, document_revision_id):
        index = len(self.batches)
        self.batches.append(blocks)
        if self.fail_batch is not None and index == self.fail_batch:
            return {"code": 99991400, "msg": "request trigger frequency limit"}
        return {"code": 0, "data": {"children": [{"block_id": f"blk{index}-{i}"} for i in range(len(blocks))]}}

    def _upload(self, token, base_url, path, block_id, timeout):
        self.uploaded.append((path.name, block_id))
        return f"file-{path.name}"


def _export(markdown: str, root: Path | None):
    return lx.export_markdown_to_lark(
        "笔记", markdown, app_id="cli_x", app_secret="s", task_id=TASK, artifact_root=root
    )


# ── app route keeps pictures ────────────────────────────────────────────────

def test_app_route_uploads_each_local_screenshot_into_its_image_block(monkeypatch, tmp_path):
    feishu = FakeFeishu(monkeypatch)
    root = _frames(tmp_path, "note_0090.jpg")
    note = f"# 标题\n\n正文\n\n![架构图](/jobs/{TASK}/artifacts/frame?file=note_0090.jpg)\n"

    result = _export(note, root)

    assert feishu.converted == 0, "the convert path uploads no pictures, so it is skipped"
    image_blocks = [b for b in feishu.batches[0] if b["block_type"] == lx._BT_IMAGE]
    assert len(image_blocks) == 1
    assert feishu.uploaded == [("note_0090.jpg", "blk0-2")]
    assert (result["image_count"], result["image_upload_count"], result["image_upload_errors"]) == (1, 1, [])


def test_a_screenshot_as_the_first_block_still_gets_its_picture(monkeypatch, tmp_path):
    feishu = FakeFeishu(monkeypatch)
    root = _frames(tmp_path, "a.jpg")

    result = _export(f"![甲](/jobs/{TASK}/artifacts/frame?file=a.jpg)", root)

    assert feishu.uploaded == [("a.jpg", "blk0-0")]
    assert result["image_upload_count"] == 1


def test_a_note_without_local_screenshots_keeps_the_official_convert_path(monkeypatch):
    feishu = FakeFeishu(monkeypatch)

    result = _export("# 标题\n\n正文", None)

    assert feishu.converted == 1
    assert result["via"] == "openapi_convert"
    assert result["image_count"] == 0


def test_writing_that_stops_half_way_names_the_half_written_document(monkeypatch):
    FakeFeishu(monkeypatch, fail_batch=1)
    monkeypatch.setenv("FLUENTFLOW_LARK_DISABLE_OPENAPI_CONVERT", "1")
    note = "\n\n".join(f"第 {i} 段" for i in range(120))

    with pytest.raises(RuntimeError) as caught:
        _export(note, None)

    assert "https://larksuite.com/docx/doxNEW" in str(caught.value)
    diagnosis = diagnose_error(caught.value)
    assert diagnosis["code"] == "feishu_rate_limited"
    assert "https://larksuite.com/docx/doxNEW" in diagnosis["detail"]
    assert "删掉" in diagnosis["detail"]


# ── each failure says what it is and what to do ─────────────────────────────

@pytest.mark.parametrize(
    ("raw", "code", "must_say"),
    [
        ("lark-cli not found. Install @larksuite/cli globally", "lark_cli_not_installed", "npm install -g @larksuite/cli"),
        ("lark-cli 失败 [type=authentication subtype=token_expired]：token expired", "lark_cli_login_required", "lark-cli auth login"),
        (
            "lark-cli 失败 [type=authorization subtype=missing_scope missing_scopes=docx:document:create]：missing scope",
            "feishu_missing_scope",
            'lark-cli auth login --scope "docx:document:create"',
        ),
        (
            "Lark create-doc error: code=99991672, msg=Access denied. One of the following scopes is required: [docx:document, docx:document:create]",
            "feishu_missing_scope",
            "docx:document:create",
        ),
        ("Lark credentials not set: provide app_id/app_secret", "feishu_app_credentials_missing", "高级 · 其他凭证"),
        ("Feishu tenant token error: {'code': 10014, 'msg': 'app secret invalid'}", "feishu_app_credentials_invalid", "App Secret"),
        ("Feishu create-doc HTTP 404: folder not found", "feishu_folder_not_found", "飞书文件夹链接"),
        ("Feishu 写入块失败: code=99991400 msg=request trigger frequency limit", "feishu_rate_limited", "过一两分钟"),
    ],
)
def test_each_failure_has_its_own_diagnosis_and_next_step(raw, code, must_say):
    diagnosis = diagnose_error(RuntimeError(raw))

    assert diagnosis["code"] == code
    shown = f"{diagnosis['detail']} {diagnosis['next_action']}"
    assert must_say in shown
    assert diagnosis["detail"] != "飞书导出失败。"


def test_missing_scope_names_the_scope_in_what_the_user_reads():
    diagnosis = diagnose_error(
        "lark-cli 失败 [type=authorization subtype=missing_scope missing_scopes=wiki:node:create]：forbidden"
    )
    assert "wiki:node:create" in diagnosis["detail"]


def test_no_message_points_at_settings_that_do_not_exist():
    raws = [
        "lark-cli not found",
        "lark-cli 失败 [type=authentication]：not logged in",
        "lark-cli 失败 [subtype=missing_scope missing_scopes=wiki:wiki]：x",
        "Lark credentials not set",
        "Feishu tenant token error: {}",
        "Feishu create-doc HTTP 404: folder not found",
        "Feishu 写入块失败: frequency limit",
        "Feishu something odd",
    ]
    for raw in raws:
        diagnosis = diagnose_error(raw)
        shown = diagnosis["detail"] + diagnosis["next_action"]
        assert "用本机 lark-cli 导出到「我的文档库」" not in shown
        assert "目标文件夹后重试" not in shown


def test_an_unknown_feishu_failure_keeps_its_own_words():
    diagnosis = diagnose_error("Feishu create-doc error: code=1234 msg=document locked by admin")
    assert diagnosis["code"] == "feishu_export_failed"
    assert "document locked by admin" in diagnosis["detail"]


def test_a_skylark_model_error_is_not_a_feishu_export_failure():
    assert not diagnose_error("Skylark model failed: unauthorized")["code"].startswith("feishu")


# ── folder link ─────────────────────────────────────────────────────────────

def test_the_folder_setting_accepts_the_link_or_the_token():
    assert lx.parse_folder_token("https://abc.feishu.cn/drive/folder/fldcnXYZ?from=space") == "fldcnXYZ"
    assert lx.parse_folder_token("  fldcnXYZ ") == "fldcnXYZ"
    assert lx.parse_folder_token("") is None


# ── the export is remembered on the task ────────────────────────────────────

def _client() -> TestClient:
    app = FastAPI()
    app.include_router(local_feishu_export.router)
    return TestClient(app)


@pytest.fixture
def owned_task(monkeypatch, tmp_path):
    store: dict = {TASK: {"task_id": TASK, "result": {"summary_markdown": "# 笔记", "note": "kept"}}}
    monkeypatch.setattr(local_feishu_export, "log_event", lambda **values: None)
    monkeypatch.setattr(local_feishu_export, "_artifact_storage_dir", lambda: tmp_path)
    monkeypatch.setattr(local_feishu_export, "resolve_secret", lambda form_value, name: "x")
    monkeypatch.setattr(local_feishu_export, "get_job", lambda task_id, client_id=None: store.get(task_id))

    def update(task_id, result, client_id=None):
        store[task_id] = {**store[task_id], "result": result}

    monkeypatch.setattr(local_feishu_export, "update_job_result", update)
    return store


def test_a_manual_export_keeps_its_link_on_the_task(monkeypatch, owned_task):
    seen: dict = {}

    def fake_cli(title, markdown, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "url": "https://x.feishu.cn/wiki/w", "via": "lark_cli",
                "image_count": 2, "image_upload_count": 1, "image_upload_errors": ["a"]}

    monkeypatch.setattr(local_feishu_export, "export_markdown_via_lark_cli", fake_cli)
    r = _client().post("/export-lark", data={"markdown": "# 笔记", "task_id": TASK, "lark_export_route": "local_cli"})

    assert r.status_code == 200
    saved = owned_task[TASK]["result"]
    assert saved["lark_response"]["url"] == "https://x.feishu.cn/wiki/w"
    assert saved["lark_response"]["image_upload_count"] == 1
    assert saved["lark_doc_title"] == "笔记"
    assert saved["lark_error"] is None
    assert saved["note"] == "kept", "the rest of the task result is untouched"
    assert seen["task_id"] == TASK, "the screenshots of this task can be found"


def test_a_failed_manual_export_keeps_the_previous_link_and_records_the_reason(monkeypatch, owned_task):
    owned_task[TASK]["result"]["lark_response"] = {"url": "https://x.feishu.cn/wiki/old"}

    def boom(title, markdown, **kwargs):
        raise RuntimeError("lark-cli not found.")

    monkeypatch.setattr(local_feishu_export, "export_markdown_via_lark_cli", boom)
    r = _client().post("/export-lark", data={"markdown": "x", "task_id": TASK, "lark_export_route": "local_cli"})

    assert r.status_code == 500
    assert "npm install -g @larksuite/cli" in r.json()["detail"], "the toast carries the next step"
    saved = owned_task[TASK]["result"]
    assert saved["lark_response"]["url"] == "https://x.feishu.cn/wiki/old"
    assert "lark-cli" in saved["lark_error"]


def test_auto_route_uses_the_users_own_identity_when_lark_cli_is_signed_in(monkeypatch):
    monkeypatch.setattr(local_feishu_export, "lark_cli_ready", lambda: True)
    assert local_feishu_export._local_lark_export_target("auto", None) == "lark_cli"
    monkeypatch.setattr(local_feishu_export, "lark_cli_ready", lambda: False)
    assert local_feishu_export._local_lark_export_target("auto", None) == "lark_openapi"


def test_a_task_record_that_cannot_be_saved_does_not_turn_a_made_document_into_a_failure(monkeypatch, owned_task):
    def broken_update(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(local_feishu_export, "update_job_result", broken_update)
    monkeypatch.setattr(
        local_feishu_export, "export_markdown_via_lark_cli",
        lambda title, markdown, **kwargs: {"ok": True, "url": "https://x.feishu.cn/wiki/w"},
    )

    r = _client().post("/export-lark", data={"markdown": "x", "task_id": TASK, "lark_export_route": "local_cli"})

    assert r.status_code == 200
    assert r.json()["url"] == "https://x.feishu.cn/wiki/w"
