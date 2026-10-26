"""Reading the token counts out of an OpenAI-compatible response.

``model_usage`` records them, one row per call; this only knows the shapes the
providers answer in. A provider that omits ``usage`` gives ``None``, and the
call is recorded without counts rather than as free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TokenUsage:
    """Split by price tier: a prompt token served from the cache costs a
    small fraction of one that is not (``model_prices.json``), so the total
    alone would make cost a guess and hide whether the cache works.

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
