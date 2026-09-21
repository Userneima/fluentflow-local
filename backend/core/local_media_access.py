"""Short-lived, loopback-only grants for browser media elements.

HTML media elements cannot attach FluentFlow's client-id request header.  A
grant lets a verified API call hand the browser a narrowly scoped URL without
turning source files into unauthenticated local endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
import secrets
import time


@dataclass(frozen=True)
class LocalMediaGrant:
    """A single task's temporary media read capability."""

    token: str
    task_id: str
    expires_at: float


class LocalMediaAccess:
    """In-memory media grants; restarting the local service revokes all grants."""

    def __init__(self, *, ttl_seconds: int = 4 * 60 * 60, now=time.monotonic):
        self._ttl_seconds = max(int(ttl_seconds), 60)
        self._now = now
        self._grants: dict[str, LocalMediaGrant] = {}

    def issue(self, task_id: str, client_id: str) -> LocalMediaGrant:
        del client_id  # The request ownership check happens before a grant is issued.
        current = self._now()
        self._purge(current)
        grant = LocalMediaGrant(
            token=secrets.token_urlsafe(32),
            task_id=str(task_id),
            expires_at=current + self._ttl_seconds,
        )
        self._grants[grant.token] = grant
        return grant

    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def allows(self, token: str, task_id: str, *, now: float | None = None) -> bool:
        current = self._now() if now is None else now
        self._purge(current)
        grant = self._grants.get(str(token))
        return bool(grant and grant.task_id == str(task_id) and grant.expires_at > current)

    def _purge(self, current: float) -> None:
        expired = [token for token, grant in self._grants.items() if grant.expires_at <= current]
        for token in expired:
            self._grants.pop(token, None)
