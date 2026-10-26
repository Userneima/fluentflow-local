"""Who handles understanding on an agent submission, separate from page defaults.

Pure option normalization, shared by the MCP client and the Agent API. Merely
having a configured key must not take work away from the calling agent.
"""

from __future__ import annotations

from typing import Any


def agent_options(options: dict[str, Any] | None = None) -> dict[str, Any]:
    values = dict(options or {})
    executor = values.get("intelligence", "agent")
    if not isinstance(executor, str) or executor not in {"agent", "fluentflow"}:
        raise ValueError("intelligence 只能是 agent / fluentflow")
    values["intelligence"] = executor
    agent = executor == "agent"
    if agent:
        # Older MCP clients always sent their old defaults (false/auto).
        # Those defaults are not an explicit delegation to background models.
        values.update(skip_summary="true", cut_judge="agent")
        return values
    if values.get("skip_summary") is None:
        values["skip_summary"] = "false"
    if values.get("cut_judge") is None:
        values["cut_judge"] = "fluentflow"
    return values


def uses_agent(options: dict[str, Any] | None) -> bool:
    # Historical tasks and page submissions keep their recorded behaviour.
    return (options or {}).get("intelligence") == "agent"


def deferred_step() -> dict[str, Any]:
    return {
        "status": "skipped", "reason": "agent_required", "channel": "agent",
        "detail": "由当前 agent 负责理解和判断；此步骤暂缺 agent 写回入口，未自动调用模型。",
    }
