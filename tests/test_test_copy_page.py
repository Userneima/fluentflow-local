"""The page a test copy serves sends its requests back to the copy, never to
the owner's service on 8000 (decision 8.29; the risk is decision 8.25).

What should happen:
- on a test copy, the page's config script names the copy's own address,
  whatever the build wrote there and whatever port the copy runs on, so the
  page asks the copy for everything, deletes included;
- a test copy started with a build of its own serves that build's page;
- a broken config in the build does not stop that, and the copy's pages
  may connect only to the copy, whatever their script says;
- the service's own checks (host, origin) still come first;
- the owner's service (no test copy) ignores that setting and its config
  script is the build's, untouched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core import test_copy

ROOT = Path(__file__).resolve().parents[1]
BUILT_FOR_8000 = 'window.FLUENTFLOW_CONFIG = {"apiBase": "http://127.0.0.1:8000", "version": {"version": "9.9.9"}};\n'


@pytest.fixture
def own_build(tmp_path):
    """A page build whose config sends the page to 8000 (built with
    FLUENTFLOW_API_BASE set)."""
    build = tmp_path / "build"
    (build / "assets").mkdir(parents=True)
    (build / "local.html").write_text("<html>the build under test</html>", encoding="utf-8")
    (build / "assets" / "config.js").write_text(BUILT_FOR_8000, encoding="utf-8")
    (build / "assets" / "page.js").write_text("// page code", encoding="utf-8")
    return build


def _app():
    from backend.local_main import create_local_app

    return create_local_app()


def _page_api_base(config_js: str, page_url: str) -> str:
    """Where frontend/src/lib/apiBase.js sends the requests of a page at
    ``page_url`` that ran ``config_js``; the browser's storage also names 8000."""
    script = """
import { pathToFileURL } from 'node:url';
const location = new URL(process.env.PAGE_URL);
const stored = { fluentflow_api_base: 'http://127.0.0.1:8000' };
globalThis.window = { location };
globalThis.localStorage = { getItem: (key) => stored[key] ?? null };
new Function('window', process.env.CONFIG_JS)(globalThis.window);
const { currentApiBase } = await import(pathToFileURL(process.env.API_BASE_JS).href);
console.log(JSON.stringify(currentApiBase()));
"""
    env = {**os.environ, "PAGE_URL": page_url, "CONFIG_JS": config_js,
           "API_BASE_JS": str(ROOT / "frontend" / "src" / "lib" / "apiBase.js")}
    done = subprocess.run(["node", "--input-type=module", "-e", script], env=env, capture_output=True,
                          text=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_page_entry_reads_its_config_before_its_code():
    """The override works only while the page takes its address from the
    config script, loaded before the page's own code."""
    page = (ROOT / "frontend" / "local.html").read_text(encoding="utf-8")
    assert '<script src="/assets/config.js"></script>' in page
    assert page.index("/assets/config.js") < page.index('type="module"')
    assert "window.FLUENTFLOW_CONFIG?.apiBase" in (ROOT / "frontend/src/lib/apiBase.js").read_text(encoding="utf-8")


def test_a_test_copy_serves_its_own_build_and_sends_the_page_back_to_itself(monkeypatch, own_build):
    monkeypatch.setenv(test_copy.ENV, "1")
    monkeypatch.setenv(test_copy.FRONTEND_ENV, str(own_build))
    client = TestClient(_app())

    assert client.get("/").text == "<html>the build under test</html>"
    assert client.get("/agent").text == "<html>the build under test</html>"
    assert client.get("/assets/page.js").text == "// page code"
    config = client.get("/assets/config.js")
    assert config.status_code == 200 and config.headers["cache-control"] == "no-store"
    assert json.dumps(BUILT_FOR_8000) in config.text


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the page's address code")
@pytest.mark.parametrize("port", ["4173", "5173", "5186", "8013"])
def test_the_copy_page_sends_no_request_to_8000_on_any_port(monkeypatch, own_build, port):
    monkeypatch.setenv(test_copy.ENV, "1")
    monkeypatch.setenv(test_copy.FRONTEND_ENV, str(own_build))
    config = TestClient(_app()).get("/assets/config.js").text
    page = f"http://127.0.0.1:{port}"

    assert _page_api_base(config, f"{page}/agent") == page
    # What the build alone would have done: the reason for the override.
    assert _page_api_base(BUILT_FOR_8000, f"{page}/agent") == "http://127.0.0.1:8000"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the page's address code")
def test_a_broken_build_config_still_leaves_the_page_on_the_copy(monkeypatch, own_build):
    (own_build / "assets" / "config.js").write_text(BUILT_FOR_8000[:40], encoding="utf-8")
    monkeypatch.setenv(test_copy.ENV, "1")
    monkeypatch.setenv(test_copy.FRONTEND_ENV, str(own_build))
    config = TestClient(_app()).get("/assets/config.js").text
    assert _page_api_base(config, "http://127.0.0.1:5173/agent") == "http://127.0.0.1:5173"


def test_the_copy_pages_may_connect_only_to_the_copy(monkeypatch, own_build):
    monkeypatch.setenv(test_copy.ENV, "1")
    monkeypatch.setenv(test_copy.FRONTEND_ENV, str(own_build))
    client = TestClient(_app())
    for page in ("/", "/agent", "/editor"):
        assert client.get(page).headers["content-security-policy"] == "connect-src 'self'"


def test_the_copy_config_still_passes_the_service_checks(monkeypatch, own_build):
    monkeypatch.setenv(test_copy.ENV, "1")
    monkeypatch.setenv(test_copy.FRONTEND_ENV, str(own_build))
    client = TestClient(_app())
    assert client.get("/assets/config.js", headers={"host": "evil.example"}).status_code == 403
    assert client.get("/assets/config.js", headers={"origin": "https://evil.example"}).status_code == 403


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the page's address code")
def test_the_copy_page_stays_on_the_copy_with_the_default_build(monkeypatch):
    """No --frontend-dir: dist-local's config (or none, in a fresh checkout)."""
    monkeypatch.setenv(test_copy.ENV, "1")
    monkeypatch.delenv(test_copy.FRONTEND_ENV, raising=False)
    config = TestClient(_app()).get("/assets/config.js").text
    assert _page_api_base(config, "http://127.0.0.1:5186/") == "http://127.0.0.1:5186"


def test_the_owners_service_ignores_another_build(monkeypatch, own_build):
    monkeypatch.delenv(test_copy.ENV, raising=False)
    monkeypatch.setenv(test_copy.FRONTEND_ENV, str(own_build))
    client = TestClient(_app())

    page = client.get("/")
    assert "the build under test" not in page.text and "content-security-policy" not in page.headers
    assert "FluentFlow test copy" not in client.get("/assets/config.js").text
