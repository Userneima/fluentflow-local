"""Edition-neutral FastAPI application assembly."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles


def create_app(
    *,
    title: str,
    api_routers: Iterable[APIRouter],
    spa_router: APIRouter,
    frontend_dist_dir: Path,
    lifespan: Any = None,
    http_middleware: Iterable[Any] = (),
) -> FastAPI:
    app = FastAPI(title=title, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for middleware in http_middleware:
        app.middleware("http")(middleware)
    for router in api_routers:
        app.include_router(router)
    assets_dir = frontend_dist_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    app.include_router(spa_router)
    return app
