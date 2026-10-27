"""Short-lived, in-memory tickets for the placeholder mapping.

``POST /redact`` returns a redacted text and a ticket. The ticket stands in for
the mapping that puts the original values back, so a caller never has to hold
personal data itself. The store keeps that mapping in this process only: nothing
is written to disk, logged or sent anywhere, and a restart drops every ticket.

Tickets expire after :data:`DEFAULT_TTL_S` (15 minutes) and the store is bounded,
so a long-running service cannot accumulate mappings without limit. Expired and
unknown tickets are indistinguishable to the caller: both report "gone".
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

DEFAULT_TTL_S: Final = 15 * 60.0
DEFAULT_MAX_ENTRIES: Final = 512
_TOKEN_BYTES: Final = 24


@dataclass(frozen=True, slots=True)
class Ticket:
    """A freshly issued ticket: the opaque id and the lifetime it was given."""

    id: str
    expires_in_s: int


@dataclass(frozen=True, slots=True)
class _Entry:
    mapping: Mapping[str, str]
    expires_at: float


class TicketStore:
    """Bounded, thread-safe store of ticket id to placeholder mapping.

    ``clock`` is injectable so tests can move time instead of sleeping.
    """

    def __init__(
        self,
        *,
        ttl_s: float = DEFAULT_TTL_S,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    @property
    def ttl_s(self) -> float:
        """Ticket lifetime in seconds."""
        return self._ttl_s

    @property
    def max_entries(self) -> int:
        """Maximum number of live tickets held at once."""
        return self._max_entries

    def issue(self, mapping: Mapping[str, str]) -> Ticket:
        """Store ``mapping`` under a fresh ticket id and return the ticket."""
        now = self._clock()
        with self._lock:
            self._purge(now)
            while len(self._entries) >= self._max_entries:
                self._entries.pop(next(iter(self._entries)))
            ticket_id = secrets.token_urlsafe(_TOKEN_BYTES)
            self._entries[ticket_id] = _Entry(
                mapping=dict(mapping),
                expires_at=now + self._ttl_s,
            )
        return Ticket(id=ticket_id, expires_in_s=max(1, round(self._ttl_s)))

    def mapping(self, ticket_id: str) -> Mapping[str, str] | None:
        """Return the mapping for ``ticket_id``, or ``None`` when unknown or expired."""
        now = self._clock()
        with self._lock:
            entry = self._entries.get(ticket_id)
            if entry is None:
                return None
            if entry.expires_at <= now:
                del self._entries[ticket_id]
                return None
            return dict(entry.mapping)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def _purge(self, now: float) -> None:
        """Drop every expired entry. Caller holds the lock."""
        expired = [
            ticket_id for ticket_id, entry in self._entries.items() if entry.expires_at <= now
        ]
        for ticket_id in expired:
            del self._entries[ticket_id]
