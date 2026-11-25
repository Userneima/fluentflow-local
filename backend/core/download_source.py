"""Which of several hosts of the same file to download it from.

Every download the installers make has an original host and a mirror in China
that serves byte-identical files (each is checked against a pinned SHA-256 by
its caller). The rule, the same in the PowerShell and shell installers
(``launchers/windows/fluentflow-env.ps1``, ``launchers/macos/fluentflow-env.sh``):
probe the original for a few seconds; if it fails or is plainly slow, probe the
mirror and put the faster one first. The caller still falls back to the other
on failure, so a wrong guess costs a retry, never the install.

Waiting for the original to fail is what this replaces. A host that is slow but
not broken never fails: a user's friend in China watched a GitHub download
crawl for many minutes with nothing on screen (2026-11-21).

Standard library only.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

# A host is fast enough when it hands over this much within the time below:
# 1 MB in 5 seconds, 200 KB/s. Below that, a 40 MB model takes minutes and the
# installer's two gigabytes take hours.
PROBE_BYTES = 1 << 20
PROBE_SECONDS = 5.0


def measure(url: str, budget: int = PROBE_BYTES, seconds: float = PROBE_SECONDS) -> int:
    """How many bytes ``url`` hands over within ``seconds`` (at most ``budget``).

    A file shorter than ``budget`` that arrives whole in time counts as ``budget``.
    """
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "FluentFlow-Local", "Range": f"bytes=0-{budget - 1}"},
    )
    started = time.monotonic()
    received = 0
    try:
        with urllib.request.urlopen(request, timeout=seconds) as response:  # noqa: S310 - fixed hosts
            if response.status >= 400:
                return 0
            while received < budget and time.monotonic() - started < seconds:
                chunk = response.read(min(65536, budget - received))
                if not chunk:
                    # A file smaller than the budget, read to its end in time.
                    return budget if received else 0
                received += len(chunk)
    except (urllib.error.URLError, OSError, ValueError):
        pass
    return received


def _fast(received: int, budget: int) -> bool:
    return received >= budget


def order(candidates: list[str], budget: int = PROBE_BYTES, seconds: float = PROBE_SECONDS) -> list[str]:
    """``candidates`` (original first) reordered so the one to try first leads.

    The original keeps its place when it is fast; otherwise every candidate is
    probed and they are tried by how much each delivered.
    """
    if len(candidates) < 2:
        return list(candidates)
    first = measure(candidates[0], budget, seconds)
    if _fast(first, budget):
        return list(candidates)
    delivered = {candidates[0]: first}
    for url in candidates[1:]:
        delivered[url] = measure(url, budget, seconds)
    return sorted(candidates, key=lambda url: -delivered[url])


__all__ = ["PROBE_BYTES", "PROBE_SECONDS", "measure", "order"]
