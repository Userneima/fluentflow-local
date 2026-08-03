"""The SPA entry document must never be reused across a frontend rebuild.

`vite.local.config.mjs` builds content-hashed chunks with ``emptyOutDir: True``,
so every rebuild deletes the filenames the previous ``local.html`` points at. A
browser that keeps serving a stored copy of that document — session restore,
history navigation — then requests chunks the server no longer has. Marking the
document ``no-store`` is what keeps the entry point and its chunks from drifting
apart.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.local_spa import create_local_spa_router


class _Frontend:
    """A built local frontend with one hashed chunk name."""

    def __init__(self, tmp: str):
        self.index = Path(tmp) / "local.html"
        self.index.write_text(
            '<!DOCTYPE html><html><body><div id="root"></div>'
            '<script type="module" src="/assets/local-Dm5QjXLO.js"></script>'
            "</body></html>",
            encoding="utf-8",
        )

    def client(self) -> TestClient:
        app = FastAPI()
        app.include_router(create_local_spa_router(index_path=self.index))
        return TestClient(app)


class LocalSpaEntryCacheTests(TestCase):
    def test_entry_document_is_never_stored(self):
        with TemporaryDirectory() as tmp:
            client = _Frontend(tmp).client()

            for path in ("/", "/settings", "/tasks/abc/agent"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, path)
                cache_control = response.headers.get("cache-control", "")
                # "no-cache" alone still lets a browser restore the document
                # from its own store without asking; "no-store" does not.
                self.assertIn("no-store", cache_control, path)

    def test_hashed_assets_are_not_served_as_the_entry_document(self):
        # A request for a chunk that a stale page still wants must 404 rather
        # than fall through to index.html, so the failure is a chunk-load error
        # the frontend can recognize instead of HTML parsed as JavaScript.
        with TemporaryDirectory() as tmp:
            client = _Frontend(tmp).client()

            response = client.get("/assets/settings-Cc3dBdjv.js")
            self.assertEqual(response.status_code, 404)

    def test_missing_build_explains_how_to_build_it(self):
        with TemporaryDirectory() as tmp:
            app = FastAPI()
            app.include_router(create_local_spa_router(index_path=Path(tmp) / "local.html"))

            response = TestClient(app).get("/")
            self.assertEqual(response.status_code, 503)
            self.assertIn("npm run build:frontend", response.json()["detail"])
