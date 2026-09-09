from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.local_spa import create_local_spa_router


def test_local_spa_serves_product_routes_but_not_api_or_reserved_paths(tmp_path):
    index = tmp_path / "index.html"
    index.write_text("<html>local app</html>", encoding="utf-8")
    app = FastAPI()
    app.include_router(create_local_spa_router(index))
    client = TestClient(app)

    assert client.get("/editor").text == "<html>local app</html>"
    # /agent is the processing-records page (regression: it used to be
    # blanket-blocked as an API prefix, so refreshing the page 404'd);
    # only the /agent/v1 API namespace stays out of the SPA fallback.
    assert client.get("/agent").text == "<html>local app</html>"
    assert client.get("/agent/v1").status_code == 404
    assert client.get("/agent/v1/tasks/x").status_code == 404
    assert client.get("/jobs/missing").status_code == 404
    assert client.get("/admin").status_code == 404
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/favicon.ico").status_code == 204


def test_local_spa_reports_a_missing_frontend_index(tmp_path):
    app = FastAPI()
    app.include_router(create_local_spa_router(tmp_path / "missing.html"))

    response = TestClient(app).get("/editor")

    # A missing bundle is a setup problem, not a routing 404: tell the user
    # exactly how to build the local frontend.
    assert response.status_code == 503
    assert "build:frontend:local" in response.json()["detail"]


def test_local_spa_defaults_to_the_local_bundle_entry():
    import inspect

    from backend import local_main
    from backend.core.frontend_paths import (
        FRONTEND_LOCAL_DIST_DIR,
        local_frontend_index_path,
    )

    # The local edition must never serve the hosted bundle: its default index
    # is dist-local/local.html (no source or index.html fallback), and the
    # composition root mounts assets from dist-local.
    assert local_frontend_index_path() == FRONTEND_LOCAL_DIST_DIR / "local.html"
    assert FRONTEND_LOCAL_DIST_DIR.name == "dist-local"
    source = inspect.getsource(local_main)
    assert "frontend_dist_dir=FRONTEND_LOCAL_DIST_DIR" in source
