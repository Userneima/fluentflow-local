"""Every call to a model, one row each (``model_calls``), and what a task's
calls add up to.

The row is written where the call leaves this machine, not where a step
starts: the OpenAI-compatible client (``ai_client``), the picture model the
cut judge calls directly (``cut_judge``), the Anthropic key (``claude_text``,
``claude_vision``, ``cut_judge``) and the subscription command line
(``claude_code_note``). Each of them wraps its request in ``call``. A new step
needs nothing here.

Which task and step a call worked for travels to those exits in a context
variable. ``task_steps.start`` sets it for the code that runs the step; the
functions that do a step's work on request (cut again, rewrite the note) set it
themselves with ``scope``; ``item`` names the part of the step (``判断结尾``,
``查漏``). Work handed to another thread takes the variable with it only through
``carry`` or ``CarryingExecutor``: a thread started any other way records its
calls as not belonging to any step, which the processing report shows rather
than hides.

Only tokens are stored. Money is worked out when it is shown, from the price
table in ``model_prices.json`` (decision 6.43), in the currency each
provider bills (DeepSeek's Chinese platform in yuan, Anthropic in dollars): a price that changes changes
the figure for every call made at that price, and a model without a price shows
its tokens and no figure. Calls through the Claude subscription cost nothing
per call; the dollar figure the command line reports is the API price of the
same tokens and is kept only to be shown as that.

Recording never costs the task: a row that cannot be written is logged and the
call's answer goes back as if nothing happened.
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import urlparse

from backend.core import job_store
from backend.core.ai_usage import extract_token_usage

logger = logging.getLogger(__name__)

KEY = "key"
SUBSCRIPTION = "subscription"
ANTHROPIC = "anthropic"


@dataclass(frozen=True)
class Scope:
    task_id: str | None = None
    step: str | None = None
    item: str | None = None


_SCOPE: contextvars.ContextVar[Scope | None] = contextvars.ContextVar("fluentflow_model_scope", default=None)


def current() -> Scope:
    return _SCOPE.get() or Scope()


def enter_step(task_id: str, step: str) -> None:
    """From here on in this context, calls work for ``step`` of ``task_id``
    (``task_steps.start``)."""
    _SCOPE.set(Scope(task_id, step))


def leave_step(task_id: str, step: str) -> None:
    """The step ended (``task_steps.finish``): later calls in this context
    belong to the task but to no step, unless another step starts."""
    now = current()
    if now.task_id == task_id and now.step == step:
        _SCOPE.set(Scope(task_id))


@contextmanager
def scope(task_id: str | None, step: str | None, item: str | None = None) -> Iterator[None]:
    """The block works for ``step`` of ``task_id``: the functions that do a
    step's work, whoever asked for it."""
    token = _SCOPE.set(Scope(task_id, step, item))
    try:
        yield
    finally:
        _SCOPE.reset(token)


@contextmanager
def item(label: str) -> Iterator[None]:
    """The block is this part of the current step (``判断结尾``)."""
    now = current()
    token = _SCOPE.set(Scope(now.task_id, now.step, label))
    try:
        yield
    finally:
        _SCOPE.reset(token)


@contextmanager
def part_of(step: str, label: str | None = None) -> Iterator[None]:
    """The block is always ``step``'s work (``label`` the part of it), for
    whichever task the context names: the transcript's corrections, the cut's
    judgement, the note and its check. Used on the functions that do that work,
    so a call is put in its step even when the code that called it is in
    another (the pipeline writes some notes while the correction step is open).
    """
    now = current()
    token = _SCOPE.set(Scope(now.task_id, step, label))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def works_for(step: str, item_label: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorates a function that does ``step``'s work for the task named by
    its ``task_id`` argument (first, or by keyword), whoever called it: the
    task's own run, or a run again on request (cut again, rewrite the note,
    an agent's operation)."""

    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        def task_of(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
            value = kwargs.get("task_id", args[0] if args else None)
            return str(value) if value else None

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def run_async(*args: Any, **kwargs: Any) -> Any:
                with scope(task_of(args, kwargs), step, item_label):
                    return await fn(*args, **kwargs)
            return run_async

        @functools.wraps(fn)
        def run(*args: Any, **kwargs: Any) -> Any:
            with scope(task_of(args, kwargs), step, item_label):
                return fn(*args, **kwargs)
        return run

    return wrap


def carry(fn: Callable[..., Any], *, task_id: str | None = None, step: str | None = None) -> Callable[..., Any]:
    """``fn``, run on whatever thread with the task and step it was handed
    over with, or with ``task_id`` and ``step`` when given (a route doing a
    step's work on request). Each run gets its own copy, so one function can
    run on several threads at once."""
    context = contextvars.copy_context()

    def inside(*args: Any, **kwargs: Any) -> Any:
        if task_id is None:
            return fn(*args, **kwargs)
        with scope(task_id, step):
            return fn(*args, **kwargs)

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> Any:
        return context.copy().run(inside, *args, **kwargs)

    return run


class CarryingExecutor(ThreadPoolExecutor):
    """A thread pool whose work keeps the task and step it was submitted with."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any):  # type: ignore[override]
        return super().submit(carry(fn), *args, **kwargs)


# ── recording ──────────────────────────────────────────────────────────────

@dataclass
class Call:
    """One request, filled in by the exit that makes it."""

    provider: str
    model: str
    billing: str
    input_tokens: int | None = None
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int | None = None
    reported_cost_usd: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def openai(self, response: Any) -> None:
        """An OpenAI-compatible response's ``usage``: the prompt counts the
        cached part, DeepSeek or OpenAI style."""
        usage = extract_token_usage(provider=self.provider, model=self.model, response=response)
        if usage is None:
            return
        self.input_tokens = usage.input_tokens
        self.cached_input_tokens = usage.cached_input_tokens
        self.output_tokens = usage.output_tokens

    def anthropic(self, message: Any) -> None:
        """Anthropic counts the cache apart from the input; stored here as one
        prompt with its cached and cache-written parts."""
        self.claude_usage(getattr(message, "usage", None))

    def claude_usage(self, usage: Any) -> None:
        def read(name: str) -> int:
            value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            return value if isinstance(value, int) and not isinstance(value, bool) else 0

        if usage is None:
            return
        fresh, hits, writes = read("input_tokens"), read("cache_read_input_tokens"), read("cache_creation_input_tokens")
        self.input_tokens = fresh + hits + writes
        self.cached_input_tokens = hits
        self.cache_write_tokens = writes
        self.output_tokens = read("output_tokens")

    def command_line(self, payload: dict[str, Any]) -> None:
        """The subscription command line's result event: its tokens, the model
        it resolved and the API-price figure it reports."""
        self.claude_usage(payload.get("usage") if isinstance(payload.get("usage"), dict) else None)
        models = payload.get("modelUsage")
        if isinstance(models, dict) and models:
            self.model = str(max(models, key=lambda name: (models[name] or {}).get("outputTokens", 0)
                                 if isinstance(models[name], dict) else 0))
        cost = payload.get("total_cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            self.reported_cost_usd = float(cost)


@contextmanager
def call(provider: str, model: str, billing: str = KEY) -> Iterator[Call]:
    """Around one request at an exit: records it, answered or not."""
    record_of = Call(provider=provider or "unknown", model=model or "unknown", billing=billing)
    started = time.perf_counter()
    try:
        yield record_of
    except BaseException as exc:
        _record(record_of, time.perf_counter() - started, ok=False, error=str(exc) or type(exc).__name__)
        raise
    _record(record_of, time.perf_counter() - started, ok=True)


def _record(made: Call, seconds: float, *, ok: bool, error: str | None = None) -> None:
    where = current()
    try:
        job_store.insert_model_call(
            task_id=where.task_id, step=where.step, item=where.item,
            provider=made.provider, model=made.model, billing=made.billing,
            input_tokens=made.input_tokens, cached_input_tokens=made.cached_input_tokens,
            cache_write_tokens=made.cache_write_tokens, output_tokens=made.output_tokens,
            duration_ms=int(seconds * 1000), ok=ok, error=(error or "")[:300] or None,
            reported_cost_usd=made.reported_cost_usd,
        )
    except Exception:  # noqa: BLE001 - the call's answer matters more than its record
        logger.warning("could not record a model call (%s %s)", made.provider, made.model, exc_info=True)


def provider_of_base_url(base_url: str) -> str:
    """The provider behind an OpenAI-compatible address: one FluentFlow knows
    by name, else the address's host."""
    from backend.core import text_providers

    base = str(base_url or "").rstrip("/")
    for entry in text_providers.PROVIDERS.values():
        if base in (text_providers.base_url(entry).rstrip("/"), entry.base_url.rstrip("/")):
            return entry.id
    return urlparse(base).hostname or "unknown"


# ── prices ─────────────────────────────────────────────────────────────────

PRICES_PATH = Path(__file__).with_name("model_prices.json")


@functools.lru_cache(maxsize=1)
def prices() -> dict[str, Any]:
    return json.loads(PRICES_PATH.read_text(encoding="utf-8"))


def _when(stamp: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _peak(provider: dict[str, Any], moment: datetime) -> bool:
    hours = provider.get("peak_hours_utc")
    if not hours:
        return False
    utc = moment.astimezone(timezone.utc)
    if provider.get("peak_weekdays_only") and utc.weekday() >= 5:
        return False
    minute = utc.hour * 60 + utc.minute
    for start, end in hours:
        low, high = (int(part[:2]) * 60 + int(part[3:]) for part in (start, end))
        if low <= minute < high:
            return True
    return False


def unit_prices(provider: str, model: str, stamp: Any) -> tuple[str, dict[str, float]] | None:
    """The currency and the price per million tokens of each kind, for this
    call; None when the table has no price for it."""
    moment = _when(stamp)
    found = (prices().get("providers") or {}).get(provider)
    if moment is None or not isinstance(found, dict):
        return None
    chosen = None
    for entry in found.get("prices") or []:
        if model not in entry.get("models", []):
            continue
        since = _when(entry.get("from"))
        if since is not None and since <= moment and (chosen is None or since >= _when(chosen.get("from"))):
            chosen = entry
    if chosen is None:
        return None
    tier = chosen.get("off_peak") if chosen.get("off_peak") and not _peak(found, moment) else chosen.get("per_million")
    return (str(found.get("currency") or "USD"), dict(tier)) if tier else None


def cost(row: dict[str, Any]) -> tuple[str, float] | None:
    """What a call made with a key cost, in the provider's currency, by the
    price table; None without a price (or without token counts). Calls
    through the subscription are not priced."""
    if row.get("billing") != KEY or row.get("input_tokens") is None:
        return None
    found = unit_prices(str(row.get("provider") or ""), str(row.get("model") or ""), row.get("created_at"))
    if found is None:
        return None
    currency, unit = found
    total = int(row.get("input_tokens") or 0)
    hits = int(row.get("cached_input_tokens") or 0)
    writes = int(row.get("cache_write_tokens") or 0)
    fresh = max(total - hits - writes, 0)
    amount = (
        fresh * unit.get("input", 0)
        + hits * unit.get("cached_input", unit.get("input", 0))
        + writes * unit.get("cache_write", unit.get("input", 0))
        + int(row.get("output_tokens") or 0) * unit.get("output", 0)
    )
    return currency, amount / 1_000_000


# ── what a task's calls add up to ──────────────────────────────────────────

def _empty() -> dict[str, Any]:
    return {
        "calls": 0, "failed_calls": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
        "cost": {}, "unpriced_calls": 0, "subscription_calls": 0, "reported_cost_usd": None,
        "models": [],
    }


def _add(total: dict[str, Any], row: dict[str, Any]) -> None:
    total["calls"] += 1
    total["failed_calls"] += 0 if row.get("ok") else 1
    for name in ("input_tokens", "cached_input_tokens", "output_tokens"):
        total[name] += int(row.get(name) or 0)
    model = str(row.get("model") or "")
    if model and model not in total["models"]:
        total["models"].append(model)
    if row.get("billing") == SUBSCRIPTION:
        total["subscription_calls"] += 1
        if row.get("reported_cost_usd") is not None:
            total["reported_cost_usd"] = (total["reported_cost_usd"] or 0.0) + float(row["reported_cost_usd"])
        return
    priced = cost(row)
    if priced is None:
        # A failed call that reported nothing cost nothing to price.
        if row.get("input_tokens") is not None or row.get("ok"):
            total["unpriced_calls"] += 1
        return
    currency, amount = priced
    total["cost"][currency] = total["cost"].get(currency, 0.0) + amount


UNATTRIBUTED = "unattributed"
UNATTRIBUTED_LABEL = "没归到步骤"


def summarize(task_id: str, rows: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A task's calls: the total, then each step in the task's order with its
    parts, then the calls that belong to no step."""
    from backend.core import task_steps

    found = list(rows) if rows is not None else job_store.list_model_calls(task_id)
    total = _empty()
    steps: dict[str, dict[str, Any]] = {}
    for row in found:
        _add(total, row)
        name = str(row.get("step") or "") or UNATTRIBUTED
        step = steps.setdefault(name, {**_empty(), "step": name, "items": {}})
        _add(step, row)
        if row.get("item"):
            part = step["items"].setdefault(str(row["item"]), {**_empty(), "item": str(row["item"])})
            _add(part, row)
    order = {name: index for index, name in enumerate(task_steps.ORDER)}
    listed = []
    for name in sorted(steps, key=lambda name: order.get(name, len(order))):
        step = steps[name]
        step["label"] = task_steps.LABELS.get(name, UNATTRIBUTED_LABEL)
        step["items"] = list(step["items"].values())
        listed.append(step)
    return {"total": total, "steps": listed, "prices_checked": prices().get("checked")}


def for_task(task_id: str) -> dict[str, Any] | None:
    """``summarize`` for the page and agents; None when the record cannot be
    read, which leaves the rest of the answer as it was."""
    try:
        return summarize(task_id)
    except Exception:  # noqa: BLE001 - the task's own data comes first
        logger.warning("could not read the model calls of %s", task_id, exc_info=True)
        return None


__all__ = [
    "ANTHROPIC", "CarryingExecutor", "KEY", "SUBSCRIPTION", "UNATTRIBUTED", "UNATTRIBUTED_LABEL",
    "call", "carry", "cost", "current", "enter_step", "for_task", "item", "leave_step", "part_of", "prices",
    "provider_of_base_url", "scope", "summarize", "unit_prices", "works_for",
]
