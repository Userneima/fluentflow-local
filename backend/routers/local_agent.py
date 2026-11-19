"""Local-edition Agent API (``/agent/v1``).

Same task surface as the hosted Agent API — submit, inspect, wait, diagnose,
retry, regenerate, export — with local semantics: the single local event hub
and job store, the local entry guards (atomic task ids, strict AI keys),
local-owner Feishu export routes only, and no accounts, quota, or
desktop-sync. Per the design contract the whole surface is DISABLED until the
user configures a local access token (``FLUENTFLOW_ACCESS_TOKEN``); the
loopback/session boundary itself belongs to the composition-root unit.

Classification note: ``local_ready``. Video-link submission delegates to the
local video-source router and therefore remains inside the local/shared graph.

The routes live in ``backend/routers/agent_v1/``, one module per group, each
with the ``/agent/v1`` prefix and the access-token dependency; this file is the
router the app mounts and includes them in order. That order is the order
FastAPI matches paths in, so a new module goes where its routes belong.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.routers.agent_v1 import (
    agent_material,
    exports,
    note_edits,
    note_skill,
    regenerate,
    review_notes,
    task_control,
    tasks,
)

router = APIRouter()

for _module in (
    tasks,
    task_control,
    agent_material,
    note_edits,
    note_skill,
    regenerate,
    exports,
    review_notes,
):
    router.include_router(_module.router)
