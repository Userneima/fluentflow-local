"""What this backend is, said out loud, so a client never has to guess.

Two editions of FluentFlow serve the same ``/agent/v1`` routes with different
intake capabilities, and for a while both answered on 127.0.0.1:8000. A client
that submitted a local file path to the hosted edition got back a rejection
listing the inputs that one accepts, which says nothing about *which* backend
answered. On 2026-09-15 that rejection was read as "the MCP tool is newer than
this backend", and a recording job this edition could have done end to end was
handed to standalone scripts instead.

So identity and intake are declared, not inferred: ``/health`` carries them, and
an intake rejection names this edition rather than only its input list.

Ported deliberately, not shared: the hosted repository keeps its own copy with
its own values (see ``docs/edition_boundaries.md``).
"""

from __future__ import annotations

EDITION = "local"
EDITION_LABEL = "FluentFlow Local"

# Agent API intake types this edition accepts. Kept beside the rejection message
# so the declaration and the refusal cannot drift apart.
ACCEPTED_AGENT_INPUTS = ("local_path", "video_link", "transcript_text")

INTAKE_REJECTION = (
    f"{EDITION_LABEL} 接受本机文件路径（local_path）、视频链接或转录稿文本，这次一个都没拿到。"
    "按路径提交时，请给出这台机器上的完整路径。"
)


def identity_payload() -> dict[str, object]:
    """The edition block carried by ``/health``."""
    return {
        "edition": EDITION,
        "edition_label": EDITION_LABEL,
        "accepted_agent_inputs": list(ACCEPTED_AGENT_INPUTS),
    }
