"""The one string every reader of the local workspace has to agree on.

Jobs are scoped by client id. The browser app files them under
``local-single-user``; the MCP client shipped with its own default of
``local-client``, so `list_tasks` reported an empty workspace on a machine with a
full one, and anything it created was filed where the app could not show it.

There is no shared module across Python and JavaScript, so the copies are pinned
against each other here rather than trusted to stay in step.
"""

import re
from pathlib import Path
from unittest import TestCase

from backend.core.local_request_scope import LOCAL_SINGLE_USER_CLIENT_ID
from scripts.local_agent_client import DEFAULT_CLIENT_ID

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _js_constant(relative_path: str, name: str) -> str:
    source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
    match = re.search(rf"{name}\s*=\s*['\"]([^'\"]+)['\"]", source)
    assert match, f"{name} not found in {relative_path}"
    return match.group(1)


class LocalClientIdContractTests(TestCase):
    def test_the_mcp_client_defaults_to_the_workspace_the_app_writes_to(self):
        self.assertEqual(DEFAULT_CLIENT_ID, LOCAL_SINGLE_USER_CLIENT_ID)

    def test_the_browser_app_uses_the_same_identity(self):
        for path in ("frontend/src/app/apiConfig.js", "frontend/src/app/shared.jsx"):
            with self.subTest(path=path):
                self.assertEqual(
                    _js_constant(path, "LOCAL_SINGLE_USER_CLIENT_ID"),
                    LOCAL_SINGLE_USER_CLIENT_ID,
                )

    def test_the_ownership_merge_script_targets_the_same_identity(self):
        from scripts.merge_job_ownership_to_local import DEFAULT_TARGET

        self.assertEqual(DEFAULT_TARGET, LOCAL_SINGLE_USER_CLIENT_ID)
