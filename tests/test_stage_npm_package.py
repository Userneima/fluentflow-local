"""Requirements for publishing the release to npm (the host installs in China use).

- The package holds both install scripts at its top and the release source
  under source/, with the repository's own package.json kept inside source/.
- No .gitignore or .npmignore inside: npm would take them as rules and leave
  files out.
- A tag that does not match VERSION is refused.
- The workflow skips the publish, green, without the NPM_TOKEN secret.
- Both install scripts know the npmmirror host and check the tarball's sha512.
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
    assert (out / "install.ps1").read_bytes() == (out / "source" / "install.ps1").read_bytes()
    assert (out / "source" / "VERSION").read_text(encoding="utf-8").strip() == version
    assert (out / "source" / "backend" / "local_main.py").is_file()
    assert "scripts" in json.loads((out / "source" / "package.json").read_text(encoding="utf-8"))
    assert not list(out.rglob(".gitignore")) and not list(out.rglob(".npmignore"))


def test_a_tag_that_is_not_the_version_is_refused(stage_module, tmp_path):
    with pytest.raises(SystemExit):
        stage_module.stage("v0.0.1", tmp_path / "npm")


def test_the_workflow_skips_without_a_token():
    text = (ROOT / ".github" / "workflows" / "publish-npm.yml").read_text(encoding="utf-8")
    assert "secrets.NPM_TOKEN != ''" in text
    assert text.count("if: env.HAS_TOKEN == 'true'") >= 3


def test_both_installers_can_fetch_from_npmmirror_and_check_it():
    for name in ("install.ps1", "install.sh"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "registry.npmmirror.com/fluentflow-local/" in text, name
        assert "sha512-" in text and "integrity" in text, name
