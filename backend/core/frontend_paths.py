from pathlib import Path


FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend"
FRONTEND_DIST_DIR = FRONTEND_ROOT / "dist"

# Local-edition bundle (npm run build:frontend). Its entry document is
# local.html — the build has no index.html on purpose, so the hosted and
# local outputs can never be confused for each other.
FRONTEND_LOCAL_DIST_DIR = FRONTEND_ROOT / "dist-local"


def frontend_index_path() -> Path:
    dist_index = FRONTEND_DIST_DIR / "index.html"
    if dist_index.exists():
        return dist_index
    return FRONTEND_ROOT / "index.html"


def local_frontend_dist_dir() -> Path:
    """The local bundle this service serves: ``dist-local``, or on a test copy
    the build it was started with (``backend/core/test_copy.py``)."""
    from backend.core import test_copy

    return test_copy.frontend_dir() or FRONTEND_LOCAL_DIST_DIR


def local_frontend_index_path() -> Path:
    # No source fallback: the unbuilt frontend/local.html references /src
    # modules that only the Vite dev server can serve. The local SPA router
    # turns a missing bundle into a readable build instruction instead.
    return local_frontend_dist_dir() / "local.html"
