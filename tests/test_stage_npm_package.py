"""Requirements for publishing the release to npm (the host installs in China use).

- The package holds both install scripts at its top and the release source
  under source/, with the repository's own package.json kept inside source/.
- No .gitignore or .npmignore inside: npm would take them as rules and leave
  files out.
- A tag that does not match VERSION is refused.
- The workflow skips the publish, green, without the NPM_TOKEN secret.
- Both install scripts know the npmmirror host and check the tarball's sha512.
- The China install command in the documents and in both scripts' headers takes
  the installer out of the package tarball, not from `/files/install.sh` (403).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def stage_module():
    if not shutil.which("git") or not (ROOT / ".git").exists():
        pytest.skip("needs a git checkout")
    import subprocess
    if subprocess.run(["git", "rev-parse", "-q", "--verify", "refs/tags/v0.11.0"], cwd=ROOT, capture_output=True).returncode:
        pytest.skip("needs the v0.11.0 tag (a shallow CI checkout has none)")
    spec = importlib.util.spec_from_file_location("stage_npm_package", ROOT / "scripts" / "stage_npm_package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_package_holds_the_installers_and_the_source(stage_module, tmp_path):
    version = "0.11.0"
    out = stage_module.stage(f"v{version}", tmp_path / "npm")

    meta = json.loads((out / "package.json").read_text(encoding="utf-8"))
    assert meta["name"] == "fluentflow-local"
    assert meta["version"] == version
    # The installers on top are the current ones, which know npmmirror.
    assert (out / "install.ps1").read_bytes() == (ROOT / "install.ps1").read_bytes()
    assert "registry.npmmirror.com" in (out / "install.sh").read_text(encoding="utf-8")
    assert (out / "source" / "VERSION").read_text(encoding="utf-8").strip() == version
    assert (out / "source" / "backend" / "local_main.py").is_file()
    assert "scripts" in json.loads((out / "source" / "package.json").read_text(encoding="utf-8"))
    assert not list(out.rglob(".gitignore")) and not list(out.rglob(".npmignore"))


def test_a_tag_that_is_not_the_version_is_refused(stage_module, tmp_path):
    with pytest.raises(SystemExit):
        stage_module.stage("v0.0.1", tmp_path / "npm")


def test_the_workflow_skips_without_a_token():
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "secrets.NPM_TOKEN != ''" in text
    assert text.count("if: env.HAS_TOKEN == 'true'") >= 3


def test_both_installers_can_fetch_from_npmmirror_and_check_it():
    for name in ("install.ps1", "install.sh"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "registry.npmmirror.com/fluentflow-local/" in text, name
        assert "sha512-" in text and "integrity" in text, name


def test_the_china_install_takes_the_installer_out_of_the_package():
    # npmmirror serves an application's files one at a time only for packages on
    # the unpkg-white-list; this one answers 403 there (2026-11-27). The documents
    # and the scripts' own headers must send people to the package instead: read
    # dist.tarball from the metadata, unpack install.sh / install.ps1, run it with
    # that package's source.
    for name in ("README.md", "AGENT_INSTALL.md", "install.sh", "install.ps1"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "npmmirror.com/fluentflow-local/latest/files/install" not in text, name
        assert "fluentflow-local/latest" in text, name
        assert "dist.tarball" in text, name
        assert "FLUENTFLOW_INSTALL_SOURCE" in text, name


def test_a_release_carries_the_built_interface_and_installs_skip_the_build():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "npm run build:frontend" in workflow
    assert "cp VERSION frontend/dist-local/.release-version" in workflow
    assert "gh release upload" in workflow and "frontend-dist-local.zip" in workflow
    assert 'stage_npm_package.py "$TAG" build/npm build/frontend' in workflow
    for name in ("install.ps1", "install.sh"):
        assert "releases/download/" in (ROOT / name).read_text(encoding="utf-8"), name
    for setup in ("launchers/windows/setup-local.ps1", "launchers/macos/setup-local.sh"):
        assert ".release-version" in (ROOT / setup).read_text(encoding="utf-8-sig"), setup


def test_the_built_interface_goes_into_the_npm_package(stage_module, tmp_path):
    built = tmp_path / "built"
    built.mkdir()
    (built / "local.html").write_text("<html></html>", encoding="utf-8")
    (built / ".release-version").write_text("0.11.0\n", encoding="utf-8")
    out = stage_module.stage("v0.11.0", tmp_path / "npm", built)
    assert (out / "source" / "frontend" / "dist-local" / "local.html").is_file()
    assert (out / "source" / "frontend" / "dist-local" / ".release-version").read_text(encoding="utf-8").strip() == "0.11.0"
