"""Where LLM token usage gets reported, without wiring billing into the model client.

`ai_client` is shared with the local edition, which has no billing and must not
import the quota store. So it reports usage through this registry instead: the
default recorder does nothing, and the hosted composition root registers one
that counts tokens toward the global daily budget.

Reporting is deliberately best-effort. A provider that omits `usage`, or a
recorder that raises, must never turn a successful generation into a failed
task — an uncounted call is a worse outcome than a lost note only in the sense
that it under-reports cost, and the budget check errs on the safe side by also
counting audio minutes at admission.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenUsage:
    """Split by price tier, because the tiers differ by up to 50x.

    On DeepSeek v4-flash a cache-miss input token costs $0.14/M, an output token
    $0.28/M, and a cache HIT $0.0028/M — fifty times less than a miss. Recording
    only the total made cost an estimate rather than a number, and made cache
    effectiveness invisible: a long video re-sends the same system prompt across
    many chapter calls, so hits should dominate, but nothing could confirm it.

    `input_tokens` is the whole prompt; `cached_input_tokens` is the part of it
    that was served from cache, so billable-at-full-price input is the difference.
    """

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def uncached_input_tokens(self) -> int:
        return max(self.input_tokens - self.cached_input_tokens, 0)

    @property
    def cache_hit_ratio(self) -> float | None:
        return self.cached_input_tokens / self.input_tokens if self.input_tokens > 0 else None


class TokenUsageRecorder(Protocol):
    def __call__(self, usage: TokenUsage) -> None: ...


def _noop_recorder(usage: TokenUsage) -> None:  # noqa: ARG001 - the local default
    return None


_recorder: Callable[[TokenUsage], None] = _noop_recorder


def register_token_usage_recorder(recorder: Callable[[TokenUsage], None] | None) -> None:
    """Hosted roots call this at boot; passing None restores the no-op default."""

    global _recorder
    _recorder = recorder or _noop_recorder


def record_token_usage(*, provider: str, model: str, response: Any) -> TokenUsage | None:
    """Extract `usage` from an OpenAI-compatible response and report it."""

    usage = extract_token_usage(provider=provider, model=model, response=response)
    if usage is None:
        return None
    try:
        _recorder(usage)
    except Exception:  # noqa: BLE001 - accounting must not break generation
        logger.warning("Recording LLM token usage failed", exc_info=True)
    return usage


def extract_token_usage(*, provider: str, model: str, response: Any) -> TokenUsage | None:
    raw = getattr(response, "usage", None)
    if raw is None and isinstance(response, dict):
        raw = response.get("usage")
    if raw is None:
        return None
    input_tokens = _first_int(raw, ("prompt_tokens", "input_tokens"))
    output_tokens = _first_int(raw, ("completion_tokens", "output_tokens"))
    if input_tokens is None and output_tokens is None:
        return None
    # DeepSeek reports `prompt_cache_hit_tokens`; the OpenAI-compatible shape puts
    # it under `prompt_tokens_details.cached_tokens`. Read whichever is present.
    cached = _first_int(raw, ("prompt_cache_hit_tokens", "cached_tokens"))
    if cached is None:
        cached = _nested_int(raw, "prompt_tokens_details", ("cached_tokens",))
    return TokenUsage(
        provider=(provider or "").strip() or "unknown",
        model=(model or "").strip() or "unknown",
        input_tokens=input_tokens or 0,
        output_tokens=output_tokens or 0,
        cached_input_tokens=min(cached or 0, input_tokens or 0),
    )


def _nested_int(raw: Any, container: str, names: tuple[str, ...]) -> int | None:
    inner = getattr(raw, container, None)
    if inner is None and isinstance(raw, dict):
        inner = raw.get(container)
    return _first_int(inner, names) if inner is not None else None


def _first_int(raw: Any, names: tuple[str, ...]) -> int | None:
    for name in names:
        value = getattr(raw, name, None)
        if value is None and isinstance(raw, dict):
            value = raw.get(name)
        if value is None:
            continue
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            continue
    return None
