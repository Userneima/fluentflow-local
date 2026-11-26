"""On a test copy nothing reaches the owner's real files or his Feishu,
whatever the request looks like (independent reviews of the copy,
2026-11-26).

Requirements, in the user's terms:

- A copied task is read-only however the change is sent: in the address, in
  a JSON body, or in a form (the page's note rewrite sends a form). Every
  write route of the service is checked, not a list of known ones.
- A saved change to a copied task sets nothing off that writes beside its
  note (the change log, the written note, the folder, Feishu) or calls a
  model.
- Nothing is exported to Feishu on the copy, for a copied task or a task made
  in it, by the page, an agent or the pipeline; a PDF export writes only
  inside the copy.
- The started copy keeps every runtime path, and a link task's folder, inside
  the copy, whatever a ``.env`` beside the code says; a place to write that
  is a link out of the copy is refused when the copy is prepared, and no
  write request is let through while one resolves outside it.
- With the switch off, the check reads no request body.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi import File, UploadFile
from fastapi.dependencies.utils import get_flat_dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.core import job_store, test_copy
from backend.core.local_request_scope import LOCAL_OWNER_ID

ROOT = Path(__file__).resolve().parents[1]
OWNER = {"X-Client-Id": LOCAL_OWNER_ID}
SAFE = {"GET", "HEAD", "OPTIONS"}


@pytest.fixture
def copy_of(monkeypatch, tmp_path):
    def turn_on(*task_ids: str) -> None:
        monkeypatch.setenv(test_copy.ENV, "1")
        monkeypatch.setenv("FLUENTFLOW_DATA_DIR", str(tmp_path / "copy"))
        for name, relative in test_copy.PINNED_PATHS.items():  # as the start command sets them
            monkeypatch.setenv(name, str(tmp_path / "copy" / relative))
        (tmp_path / "copy").mkdir(exist_ok=True)
        (tmp_path / "copy" / test_copy.TASKS_FILE).write_text(json.dumps(list(task_ids)), encoding="utf-8")
        test_copy.reset()

    yield turn_on
    test_copy.reset()


@pytest.fixture
def no_network(monkeypatch):
    """Any request that slips out goes to a closed port, and the model is the
    fake one."""
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "testserver")
    monkeypatch.setenv("FLUENTFLOW_FAKE_MODEL", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake")


def _real_folder_task(tmp_path: Path) -> tuple[str, Path]:
    """A copied task whose note sits in the owner's real folder."""
    real = tmp_path / "owner_real_folder"
    real.mkdir()
    md = real / "lecture_笔记.md"
    md.write_text("# old note\n\nold body\n", encoding="utf-8")
    task_id = uuid.uuid4().hex
    job_store.upsert_job(task_id=task_id, status="completed", client_id=LOCAL_OWNER_ID, summary_status="completed",
                         result={"task_id": task_id, "transcript_text": "hello world transcript",
                                 "summary_markdown": "# old note\n\nold body\n",
                                 "output_folder": {"md_path": str(md), "folder": str(real)}})
    return task_id, real


def _files(folder: Path) -> list[str]:
    return sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*"))


def _app():
    from backend.local_main import create_local_app

    return create_local_app()


# ── a copied task is read-only however the change is sent ──────────────────

@pytest.mark.parametrize("encoding", ["multipart", "urlencoded"])
def test_the_pages_note_rewrite_of_a_copied_task_is_refused(copy_of, no_network, tmp_path, encoding):
    """The page's 用文本模型重写 sends a form; it went through and wrote
    过程文件/修改记录.md into the owner's real folder (review, 2026-11-26)."""
    old, real = _real_folder_task(tmp_path)
    copy_of(old)
    before = _files(real)
    fields = {"transcript": "hello world transcript", "task_id": old}
    client = TestClient(_app())
    if encoding == "multipart":
        response = client.post("/regenerate-summary", data=fields, files={"unused": ("x.txt", b"")}, headers=OWNER)
    else:
        response = client.post("/regenerate-summary", data=fields, headers=OWNER)

    assert (response.status_code, response.json().get("detail")) == (409, test_copy.READ_ONLY)
    assert _files(real) == before
    assert job_store.get_job(old)["result"]["summary_markdown"] == "# old note\n\nold body\n"


def test_a_note_rewrite_of_a_task_made_in_the_copy_still_works(copy_of, no_network):
    """The form read for the check above still reaches the route whole."""
    copy_of()
    new = uuid.uuid4().hex
    job_store.upsert_job(task_id=new, status="completed", client_id=LOCAL_OWNER_ID, summary_status="completed",
                         result={"task_id": new, "transcript_text": "hello world transcript",
                                 "summary_markdown": "# new\n\nbody\n"})
    response = TestClient(_app()).post("/regenerate-summary", headers=OWNER, data={
        "transcript": "大家好，今天讲测试副本。" * 20, "task_id": new})
    assert response.status_code == 200, response.text
    assert response.json()["task_id"] == new


def _write_routes() -> list[APIRoute]:
    return [route for route in _app().routes if isinstance(route, APIRoute) and route.methods - SAFE]


def _through_the_check(method: str, path: str, headers: dict[str, str], body: bytes,
                       read: list[bool] | None = None) -> tuple[int, bool]:
    """Send one request through the copy's check alone; whether the route
    behind it would have been reached. ``read`` records each read of the body."""
    reached: list[bool] = []
    sent = [body]

    async def receive():
        if read is not None:
            read.append(True)
        return {"type": "http.request", "body": sent.pop() if sent else b"", "more_body": False}

    async def call_next(_request):
        reached.append(True)
        from fastapi.responses import JSONResponse

        return JSONResponse({"reached": True})

    path, _, query = path.partition("?")
    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": query.encode(),
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
             "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8013), "scheme": "http", "root_path": ""}
    response = asyncio.run(test_copy.middleware(Request(scope, receive), call_next))
    return response.status_code, bool(reached)


def _bodies(task_id: str) -> dict[str, tuple[dict[str, str], bytes]]:
    multipart = (
        "--b\r\nContent-Disposition: form-data; name=\"task_id\"\r\n\r\n" + task_id + "\r\n"
        "--b\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.txt\"\r\n"
        "Content-Type: text/plain\r\n\r\nhello\r\n--b--\r\n"
    ).encode()
    return {
        "json task_id": ({"content-type": "application/json"}, json.dumps({"task_id": task_id}).encode()),
        "json task_ids": ({"content-type": "application/json"}, json.dumps({"task_ids": [task_id]}).encode()),
        "urlencoded": ({"content-type": "application/x-www-form-urlencoded"},
                       urlencode({"task_id": task_id}).encode()),
        "multipart": ({"content-type": "multipart/form-data; boundary=b"}, multipart),
    }


def test_no_write_route_reaches_a_copied_task_whatever_the_body(copy_of):
    old = uuid.uuid4().hex
    copy_of(old)
    let_through = []
    routes = _write_routes()
    assert len(routes) > 50  # every write route of the service, not a sample
    for route in routes:
        for method in route.methods - SAFE:
            named_in_path = [p.name for p in route.dependant.path_params if re.search("task|job", p.name)]
            path = route.path
            for param in route.dependant.path_params:
                path = path.replace("{" + param.name + "}", old if param.name in named_in_path else "x")
            for label, (headers, body) in _bodies(old).items():
                headers = {**headers, "content-length": str(len(body))}
                status, reached = _through_the_check(method, path, headers, body)
                if reached or status != 409:
                    let_through.append(f"{method} {route.path} ({label})")
            if named_in_path:
                status, reached = _through_the_check(method, path, {}, b"")
                if reached or status != 409:
                    let_through.append(f"{method} {route.path} (address)")
    assert let_through == []


def _task_fields(route: APIRoute) -> list[tuple[str, str]]:
    """(where, name) of every field naming a task or a job a write route
    reads: its path, query and body parameters (and the fields of a model
    body), and the keys its handler reads from a dict body
    (``payload.get("…")``, ``body.get("…")``)."""
    flat = get_flat_dependant(route.dependant)
    fields = [("path", param.name) for param in flat.path_params]
    fields += [("query", param.name) for param in flat.query_params]
    for param in flat.body_params:
        fields.append(("body", param.name))
        fields += [("body", name) for name in getattr(param.field_info.annotation, "model_fields", {})]
    try:
        source = inspect.getsource(route.endpoint)
    except (OSError, TypeError):
        source = ""
    fields += [("body", key) for key in re.findall(r"\b(?:payload|body)\.get\(\s*[\"']([^\"']+)[\"']", source)]
    return [(where, name) for where, name in fields if re.search("task|job", name, re.IGNORECASE)]


def test_every_task_field_a_write_route_reads_is_one_the_check_reads(copy_of):
    """A field naming a task where the check does not look (the query, a key
    of a dict body, a name like ``source_task_id``) would pass unseen: each
    one, sent naming a copied task, is refused."""
    old = uuid.uuid4().hex
    copy_of(old)
    unread = []
    for route in _write_routes():
        method = sorted(route.methods - SAFE)[0]
        for where, name in _task_fields(route):
            if where == "path":
                if not test_copy._TASK_IN_PATH.search(route.path.replace("{" + name + "}", old)):
                    unread.append(f"{route.path}: {name} (address)")
                continue
            path = re.sub(r"\{[^}]+\}", "x", route.path)
            if where == "query":
                status, reached = _through_the_check(method, f"{path}?{urlencode({name: old})}", {}, b"")
            else:
                body = json.dumps({name: old}).encode()
                status, reached = _through_the_check(method, path, {"content-type": "application/json",
                                                                    "content-length": str(len(body))}, body)
            if reached or status != 409:
                unread.append(f"{route.path}: {name} ({where})")
    assert unread == []


@pytest.mark.parametrize("sent", ["no content-type", "spaces", "query", "dict key", "list"])
def test_a_copied_task_is_refused_however_it_is_named(copy_of, sent):
    """Each of these reached /events with a copied task (review of 5dbdc21)."""
    old = uuid.uuid4().hex
    copy_of(old)
    path, headers, body = "/events", {}, b""
    if sent == "no content-type":
        body = json.dumps({"event_name": "task_cancelled", "task_id": old}).encode()
    elif sent == "spaces":
        body = json.dumps({"task_id": f" {old} "}).encode()
        headers = {"content-type": "application/json"}
    elif sent == "query":
        path = f"/notes/rewrite?task_id={old}"
    elif sent == "dict key":
        body = json.dumps({"source_task_id": old}).encode()
        headers = {"content-type": "application/json"}
    else:
        body = json.dumps({"job_ids": ["new", old]}).encode()
        headers = {"content-type": "application/json"}
    status, reached = _through_the_check("POST", path, {**headers, "content-length": str(len(body))}, body)
    assert (status, reached) == (409, False)


def test_a_json_body_without_content_type_naming_a_copied_task_is_refused_by_the_service(copy_of, tmp_path):
    old, _real = _real_folder_task(tmp_path)
    copy_of(old)
    body = json.dumps({"event_name": "task_cancelled", "task_id": old}).encode()
    response = TestClient(_app()).post("/events", content=body, headers=OWNER)
    assert (response.status_code, response.json()["detail"]) == (409, test_copy.READ_ONLY)


def test_a_form_without_a_length_naming_a_copied_task_is_refused(copy_of, no_network, tmp_path):
    old, real = _real_folder_task(tmp_path)
    copy_of(old)
    before = _files(real)
    body = urlencode({"transcript": "hello world transcript", "task_id": old}).encode()
    response = TestClient(_app()).post("/regenerate-summary", content=iter([body]), headers={
        **OWNER, "content-type": "application/x-www-form-urlencoded"})
    assert (response.status_code, response.json()["detail"]) == (409, test_copy.READ_ONLY)
    assert _files(real) == before


def _toy_upload_app():
    app = _app()

    @app.post("/toy-upload")
    async def toy(file: UploadFile = File(...)):
        return {"n": len(await file.read())}

    return app


def _multipart(payload: bytes) -> bytes:
    return (b"--b\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.bin\"\r\n"
            b"Content-Type: application/octet-stream\r\n\r\n" + payload + b"\r\n--b--\r\n")


@pytest.mark.parametrize("on", [False, True])
def test_an_upload_without_a_length_still_reaches_the_route_whole(copy_of, monkeypatch, on):
    if on:
        copy_of()
    else:
        monkeypatch.delenv(test_copy.ENV, raising=False)
    body = _multipart(os.urandom(3 * 1024 * 1024))
    chunks = [body[i:i + 65536] for i in range(0, len(body), 65536)]
    response = TestClient(_toy_upload_app()).post("/toy-upload", content=iter(chunks), headers={
        **OWNER, "content-type": "multipart/form-data; boundary=b"})
    assert response.status_code == 200, response.text
    assert response.json() == {"n": 3 * 1024 * 1024}


def test_a_body_without_a_length_too_large_to_read_is_refused_on_the_copy(copy_of):
    """It could name a copied task the check cannot see."""
    copy_of()
    body = _multipart(b"x" * (16 * 1024 * 1024 + 1))
    chunks = [body[i:i + 1024 * 1024] for i in range(0, len(body), 1024 * 1024)]
    response = TestClient(_toy_upload_app()).post("/toy-upload", content=iter(chunks), headers={
        **OWNER, "content-type": "multipart/form-data; boundary=b"})
    assert (response.status_code, response.json()["detail"]) == (409, test_copy.UNREAD_BODY)


def test_with_the_switch_off_the_check_reads_no_body(monkeypatch):
    monkeypatch.delenv(test_copy.ENV, raising=False)
    read: list[bool] = []
    body = json.dumps({"task_id": "anything"}).encode()
    for headers in ({"content-type": "application/json"}, {}, {"content-type": "application/x-www-form-urlencoded"}):
        assert _through_the_check("POST", "/events?task_id=x", headers, body, read) == (200, True)
    assert read == []


def test_a_large_form_naming_a_copied_task_is_refused_where_the_task_is_looked_up(copy_of):
    """A form too large for the check to read (an upload) still cannot reach a
    copied task: the one lookup of an existing task for writing refuses it."""
    from fastapi import HTTPException

    from backend.core.local_entry_guards import require_owned_task_id

    old = uuid.uuid4().hex
    job_store.upsert_job(task_id=old, status="completed", client_id=LOCAL_OWNER_ID)
    copy_of(old)
    with pytest.raises(HTTPException) as refused:
        require_owned_task_id(old, client_id=LOCAL_OWNER_ID)
    assert (refused.value.status_code, refused.value.detail) == (409, test_copy.READ_ONLY)


# ── a saved change to a copied task sets nothing off ───────────────────────

def test_no_result_listener_acts_on_a_copied_task(copy_of, monkeypatch, tmp_path):
    from backend.core import note_section_times, note_versions

    _app()  # every listener registered
    old, real = _real_folder_task(tmp_path)
    copy_of(old)
    before_files = _files(real)
    timers: list = []

    class NoTimer:
        def __init__(self, *args, **kwargs):
            timers.append(args)

        def start(self):
            pass

        def cancel(self):
            pass

    import threading

    monkeypatch.setattr(threading, "Timer", NoTimer)
    monkeypatch.setattr(note_section_times, "_enabled", True)
    before = job_store.get_job(old)["result"]
    after = note_versions.declare({**before, "summary_markdown": "# new note\n\n## 一\n\nnew body\n",
                                   "lark_sync": {"doc_id": "d"}}, note_versions.REWRITE)
    assert job_store._RESULT_LISTENERS
    for listener in job_store._RESULT_LISTENERS:
        listener(old, before, after)
    assert timers == []
    assert _files(real) == before_files


# ── nothing goes to Feishu, and a PDF stays in the copy ────────────────────

@pytest.mark.parametrize("body", [{"target": "lark"}, {"target": "feishu"}, {}, None])
def test_an_agent_export_to_feishu_is_refused_for_any_task(copy_of, body):
    copy_of()
    assert test_copy.refusal("POST", f"/agent/v1/tasks/{uuid.uuid4().hex}/exports", body) == test_copy.NO_FEISHU


def test_an_agent_pdf_export_passes_the_check(copy_of):
    copy_of()
    assert test_copy.refusal("POST", f"/agent/v1/tasks/{uuid.uuid4().hex}/exports", {"target": "pdf"}) is None


def test_the_export_route_sends_nothing_to_feishu_for_a_task_made_in_the_copy(copy_of, monkeypatch):
    from backend.routers.agent_v1 import exports

    copy_of()
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "tok")
    new = uuid.uuid4().hex
    job_store.upsert_job(task_id=new, status="completed", client_id=LOCAL_OWNER_ID,
                         result={"task_id": new, "summary_markdown": "# new\n\nbody\n"})
    called: list = []
    monkeypatch.setattr(exports, "export_markdown_via_lark_cli", lambda *a, **k: called.append(a) or {})
    monkeypatch.setattr(exports, "export_markdown_to_lark", lambda *a, **k: called.append(a) or {})
    monkeypatch.setattr(exports, "_local_lark_export_target", lambda *a, **k: "lark_cli")
    response = TestClient(_app()).post(f"/agent/v1/tasks/{new}/exports", json={"target": "lark"},
                                       headers={"Authorization": "Bearer tok", **OWNER})
    assert (response.status_code, response.json()["detail"]) == (409, test_copy.NO_FEISHU)
    assert called == []


def test_the_feishu_exporters_themselves_refuse_on_the_copy(copy_of, monkeypatch):
    """Every caller (the page, an agent, the pipeline, the review note) goes
    through these two."""
    from backend.core import lark_cli_exporter, lark_cli_run, lark_exporter

    copy_of()
    ran: list = []
    monkeypatch.setattr(lark_cli_run, "_resolve_lark_cli_bin", lambda *_a: "/usr/bin/true")
    with pytest.raises(RuntimeError, match=test_copy.NO_FEISHU):
        lark_cli_exporter.export_markdown_via_lark_cli("t", "# t", runner=lambda *a, **k: ran.append(a))
    monkeypatch.setattr(lark_exporter, "LarkExporter", lambda *a, **k: ran.append(a))
    with pytest.raises(RuntimeError, match=test_copy.NO_FEISHU):
        lark_exporter.export_markdown_to_lark("t", "# t", app_id="a", app_secret="s")
    assert ran == []


def test_a_pdf_export_on_the_copy_writes_only_inside_the_copy(copy_of, monkeypatch, tmp_path):
    from backend.core import note_pdf

    copy_of()
    monkeypatch.setenv("FLUENTFLOW_ACCESS_TOKEN", "tok")
    new = uuid.uuid4().hex
    job_store.upsert_job(task_id=new, status="completed", client_id=LOCAL_OWNER_ID,
                         result={"task_id": new, "summary_markdown": "# new\n\nbody\n"})
    written: list[Path] = []
    monkeypatch.setattr(note_pdf, "markdown_to_pdf",
                        lambda note, title, directory, stem, **k: written.append(Path(directory)) or {"pdf_path": ""})
    client = TestClient(_app())
    auth = {"Authorization": "Bearer tok", **OWNER}

    assert client.post(f"/agent/v1/tasks/{new}/exports", json={"target": "pdf"}, headers=auth).status_code == 200
    real = tmp_path / "Documents"
    refused = client.post(f"/agent/v1/tasks/{new}/exports", json={"target": "pdf", "output_dir": str(real)},
                          headers=auth)
    assert (refused.status_code, refused.json()["detail"]) == (409, test_copy.OUTSIDE_COPY)
    inside = tmp_path / "copy" / "mine"
    assert client.post(f"/agent/v1/tasks/{new}/exports", json={"target": "pdf", "output_dir": str(inside)},
                       headers=auth).status_code == 200
    copy = (tmp_path / "copy").resolve()
    assert len(written) == 2 and all(copy in path.resolve().parents for path in written)


# ── the started copy keeps every path inside it ────────────────────────────

def _location_variables() -> set[str]:
    """Every variable the code takes a place from: ``runtime_path("…")``
    calls, and any name shaped like one (``FLUENTFLOW_…_DIR``, ``_PATH``,
    ``_CACHE``) written in backend/ or scripts/."""
    found = set()
    for folder in ("backend", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            found |= set(re.findall(r"runtime_path\(\s*\"([A-Z_]+)\"", text))
            found |= set(re.findall(r"[\"'](FLUENTFLOW_[A-Z_]*(?:DIR|PATH|CACHE))[\"']", text))
    return found


def test_every_place_the_code_names_is_pinned_or_said_not_to_be_written():
    names = _location_variables()
    assert {"FLUENTFLOW_JOB_DB_PATH", "FLUENTFLOW_LINK_OUTPUT_DIR", "FLUENTFLOW_NOTE_SKILL_DIR"} <= names
    assert names - set(test_copy.NOT_WRITTEN) - set(test_copy.PINNED_PATHS) == set()
    assert set(test_copy.NOT_WRITTEN) <= names  # no reason kept for a variable gone from the code


@pytest.mark.parametrize("windows", [False, True])
def test_the_copys_start_command_pins_every_runtime_path_inside_the_copy(windows):
    from scripts import prepare_test_copy

    into = Path("C:/copy") if windows else Path("/tmp/the-copy")
    command = prepare_test_copy.start_command(into, port=8013, fake=True, max_calls=None, max_usd=None,
                                              max_cny=None, repo=Path("/repo"), token="t", windows=windows)
    for name in _location_variables() - set(test_copy.NOT_WRITTEN):
        set_to = re.search(rf"{name}='?([^';]+)'?", command.split(" try { ")[-1] if windows else command)
        assert set_to, f"{name} not set"
        assert set_to.group(1).replace("\\", "/").startswith(str(into).replace("\\", "/")), name


# ── a link cannot carry a write out of the copy ────────────────────────────

def _real_data(tmp_path: Path) -> Path:
    import sqlite3

    real = tmp_path / "real_data"
    real.mkdir()
    with sqlite3.connect(real / "fluentflow_jobs.sqlite") as db:
        db.execute("create table jobs (task_id text, status text, error_reason text, created_at text)")
    (real / "models").mkdir()
    return real


@pytest.mark.parametrize("linked", ["artifacts", "note_skills", "exports", "fluentflow_config.json",
                                    "fluentflow_jobs.sqlite-wal"])
def test_preparing_into_a_folder_whose_place_to_write_is_a_link_is_refused(tmp_path, linked):
    from scripts import prepare_test_copy

    real = _real_data(tmp_path)
    owners = tmp_path / "owners_artifacts"
    owners.mkdir()
    into = tmp_path / "copy"
    into.mkdir()
    (into / linked).symlink_to(owners)
    with pytest.raises(SystemExit, match="links a write would follow"):
        prepare_test_copy.prepare(real, into)
    assert list(owners.iterdir()) == [] and not (into / test_copy.TASKS_FILE).exists()


def test_preparing_the_same_copy_again_keeps_its_models_link(tmp_path):
    from scripts import prepare_test_copy

    real = _real_data(tmp_path)
    into = tmp_path / "copy"
    prepare_test_copy.prepare(real, into)
    assert (into / "models").is_symlink()
    prepare_test_copy.prepare(real, into)


def test_no_write_is_let_through_while_a_place_resolves_outside_the_copy(copy_of, tmp_path):
    copy_of()
    owners = tmp_path / "owners_artifacts"
    owners.mkdir()
    (tmp_path / "copy" / "artifacts").symlink_to(owners)
    assert test_copy.paths_outside() == [f"FLUENTFLOW_ARTIFACT_DIR → {owners.resolve()}"]
    status, reached = _through_the_check("POST", "/agent/v1/tasks", {}, b"")
    assert (status, reached) == (409, False)
    assert _through_the_check("GET", "/jobs", {}, b"") == (200, True)


@pytest.mark.parametrize("unpinned", ["FLUENTFLOW_LINK_OUTPUT_DIR", "FLUENTFLOW_CONFIG_PATH"])
def test_a_place_named_outside_the_copy_or_left_at_the_owners_default_is_found(copy_of, monkeypatch, tmp_path,
                                                                              unpinned):
    copy_of()
    if unpinned == "FLUENTFLOW_LINK_OUTPUT_DIR":
        monkeypatch.delenv(unpinned)  # the owner's video folder
    else:
        monkeypatch.setenv(unpinned, str(tmp_path / "real" / "fluentflow_config.json"))
    assert [place.split(" → ")[0] for place in test_copy.paths_outside()] == [unpinned]


def test_the_default_pdf_folder_linked_out_of_the_copy_is_refused(copy_of, tmp_path):
    copy_of()
    owners = tmp_path / "owners_documents"
    owners.mkdir()
    (tmp_path / "copy" / test_copy.PDF_DIRNAME).symlink_to(owners)
    with pytest.raises(ValueError, match=test_copy.OUTSIDE_COPY):
        test_copy.export_dir(None)
    with pytest.raises(ValueError, match=test_copy.OUTSIDE_COPY):
        test_copy.export_dir(str(tmp_path / "copy" / test_copy.PDF_DIRNAME / "sub"))
