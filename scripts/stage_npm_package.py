#!/usr/bin/env python3
"""Stage a release as the npm package `fluentflow-local`, for installs from China.

npm is not how FluentFlow runs; it is a second host for the release. npmjs
publishes it, npmmirror (Alibaba) copies it within minutes, and from there the
one-command install and its code download work where GitHub crawls:

    https://registry.npmmirror.com/fluentflow-local/latest/files/install.ps1
    https://registry.npmmirror.com/fluentflow-local/-/fluentflow-local-<version>.tgz

The package holds the two install scripts at its top and the release's source
tree at that tag, exactly as ``git archive`` gives it, under ``source/``. The repository's
own package.json stays inside ``source/`` (it builds the interface), so the
package's metadata is a separate package.json written here.

    python scripts/stage_npm_package.py v0.12.0 build/npm [built-frontend-dir]
    npm publish ./build/npm --access public

Run by `.github/workflows/release.yml` when a release is published.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "fluentflow-local"


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def version_of(tag: str) -> str:
    version = tag[1:] if tag.startswith("v") else tag
    try:
        declared = _git("show", f"{tag}:VERSION").decode("utf-8").strip()
    except subprocess.CalledProcessError:
        raise SystemExit(f"there is no tag {tag} here (fetch the tags first)") from None
    if version != declared:
        raise SystemExit(f"tag {tag} does not match VERSION {declared}")
    return version


def stage(tag: str, out: Path, frontend: Path | None = None) -> Path:
    version = version_of(tag)
    if out.exists():
        shutil.rmtree(out)
    source = out / "source"
    source.mkdir(parents=True)
    # The tag's tree, not the checkout's: this script may be newer than the
    # release it publishes (a release made before it existed, published by hand).
    archive = _git("archive", "--format=tar", tag)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(source, filter="data")
    # npm reads a .gitignore or .npmignore anywhere in the package as rules for
    # what to leave out; the installed code does not need them.
    for ignore in [*source.rglob(".gitignore"), *source.rglob(".npmignore")]:
        ignore.unlink()
    if frontend is not None:
        # The interface built for this release (release.yml), so an install
        # from npm needs no Node.js or build either.
        shutil.copytree(frontend, source / "frontend" / "dist-local")
    # The install commands at the package's top are this checkout's (the
    # default branch's), not the tag's: they install and update any release,
    # and an older tag's copy (v0.11.0) cannot download from npmmirror at all.
    for script in ("install.ps1", "install.sh"):
        shutil.copy2(ROOT / script, out / script)
    (out / "README.md").write_text(
        "# fluentflow-local\n\n"
        "The release of [FluentFlow Local](https://github.com/Userneima/fluentflow-local), "
        "kept on npm so it can be installed from npmmirror in China. "
        "It is not a Node.js library; install it with `install.ps1` / `install.sh`.\n",
        encoding="utf-8",
    )
    (out / "package.json").write_text(
        json.dumps(
            {
                "name": PACKAGE_NAME,
                "version": version,
                "description": "FluentFlow Local release (installer and source), mirrored for installs in China",
                "license": "AGPL-3.0-only",
                "homepage": "https://github.com/Userneima/fluentflow-local",
                "repository": {"type": "git", "url": "git+https://github.com/Userneima/fluentflow-local.git"},
                "files": ["install.ps1", "install.sh", "source/"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return out


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__)
        return 2
    frontend = Path(argv[2]).resolve() if len(argv) == 3 else None
    print(stage(argv[0], Path(argv[1]).resolve(), frontend))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
