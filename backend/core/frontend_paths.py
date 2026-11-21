import os
from pathlib import Path


FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend"
FRONTEND_DIST_DIR = FRONTEND_ROOT / "dist"

# Local-edition bundle (npm run build:frontend). Its entry document is
# local.html — the build has no index.html on purpose, so the hosted and
# local outputs can never be confused for each other.
# FLUENTFLOW_FRONTEND_DIST_DIR points a test-copy service (FLUENTFLOW_TEST_COPY)
# at a bundle built elsewhere, so it can show unpushed page changes without
# rebuilding the dist-local the owner's service on 8000 serves
# (scripts/prepare_test_copy.py --frontend-dir). Any other service ignores it.
def _local_dist_dir() -> Path:
    from backend.core import test_copy

    chosen = os.environ.get("FLUENTFLOW_FRONTEND_DIST_DIR")
    if chosen and test_copy.enabled():
        return Path(chosen).expanduser().resolve()
    return FRONTEND_ROOT / "dist-local"


FRONTEND_LOCAL_DIST_DIR = _local_dist_dir()


def frontend_index_path() -> Path:
    dist_index = FRONTEND_DIST_DIR / "index.html"
    if dist_index.exists():
        return dist_index
    return FRONTEND_ROOT / "index.html"


def local_frontend_index_path() -> Path:
    # No source fallback: the unbuilt frontend/local.html references /src
    # modules that only the Vite dev server can serve. The local SPA router
    # turns a missing bundle into a readable build instruction instead.
    return FRONTEND_LOCAL_DIST_DIR / "local.html"
