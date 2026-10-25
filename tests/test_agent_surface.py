"""What an agent can do and what it is told stay in step.

Requirements (owner, 2026-10-25, docs/方案/2026-10-25-让agent改得对的架构整理.md):
adding a capability for agents touches the backend route, the MCP tool and the
skill. A missed one fails silently: the route exists and no agent can reach it,
or the skill names a tool that was renamed.

- Every ``/agent/v1`` route is called by at least one MCP tool, unless it is
  listed below with the reason it has none.
- Every tool-shaped name the skill writes in backticks is a tool the MCP server
  lists. Tool-shaped means it starts the way the tools' names start (get_,
  save_, wait_ ...): field names such as ``agent_cut`` or ``to_judge`` are
  left alone. A heuristic, labelled as one: a tool whose first word no other
  tool uses is not checked.
- The agent routes import nothing from the page's routes
  (``backend/routers/``): an operation both can do lives in ``backend/core``
  (``task_actions``, ``task_queue`` and the rest), and an agent route borrowing
  from a page route was how operations the page had never lent stayed out of
  an agent's reach.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from fastapi.routing import APIRoute

from backend.routers.local_agent import router as agent_router
from scripts import fluentflow_mcp_server as mcp

ROOT = Path(__file__).resolve().parents[1]

# Routes no MCP tool needs, with the reason.
ROUTES_WITHOUT_A_TOOL: dict[str, str] = {}


def _shape(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path.split("?", 1)[0]).rstrip("/")


def _routes() -> set[tuple[str, str]]:
    found = set()
    for route in agent_router.routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                found.add((method, _shape(route.path)))
    return found


def _paths_the_tools_call() -> set[str]:
    source = (ROOT / "scripts" / "fluentflow_mcp_server.py").read_text(encoding="utf-8")
    return {_shape(path) for path in re.findall(r'f?"(/agent/v1/[^"]*|/agent/v1)"', source)}


def test_every_agent_route_is_reachable_by_a_tool():
    called = _paths_the_tools_call()
    unreached = sorted(
        f"{method} {path}" for method, path in _routes()
        if path not in called and path not in ROUTES_WITHOUT_A_TOOL
    )
    assert not unreached, (
        "These agent routes have no MCP tool; add one to scripts/fluentflow_mcp_server.py "
        f"or list the route in ROUTES_WITHOUT_A_TOOL with the reason: {unreached}"
    )


def test_the_skill_names_only_tools_that_exist():
    tools = {item["name"] for item in mcp.TOOL_DEFINITIONS}
    first_words = {name.split("_", 1)[0] + "_" for name in tools}
    skill = (ROOT / "agent" / "skills" / "fluentflow" / "SKILL.md").read_text(encoding="utf-8")
    named = set(re.findall(r"`([a-z][a-z0-9_]*)`", skill))
    unknown = sorted(name for name in named if name.startswith(tuple(first_words)) and name not in tools)
    assert not unknown, f"agent/skills/fluentflow/SKILL.md names tools the MCP server does not list: {unknown}"


def test_the_checks_see_what_they_check():
    assert len(_routes()) >= 30
    assert "/agent/v1/tasks/{}/wait" in _paths_the_tools_call()


def _router_imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        found += [name for name in names if name.startswith("backend.routers")]
    return found


def test_the_agent_routes_borrow_nothing_from_the_page_routes():
    borrowed = _router_imports(ROOT / "backend" / "routers" / "local_agent.py")
    assert not borrowed, (
        "backend/routers/local_agent.py imports from the page's routes; move what both need "
        f"into backend/core (task_actions, task_queue, task_submission ...): {borrowed}"
    )


def test_the_import_check_sees_an_import(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text("from backend.routers.local_processing import retry_task\n", encoding="utf-8")
    assert _router_imports(sample) == ["backend.routers.local_processing", "backend.routers.local_processing.retry_task"]
