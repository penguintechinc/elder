"""In-process per-subject concurrency cap (max in-flight requests per user)."""

from __future__ import annotations


class InflightLimiter:
    """Track in-flight requests per subject key and refuse past a cap.

    Single-threaded asyncio use only (no awaits between check and increment, so no
    lock is needed). Counts are per hub-api replica; keys with zero in-flight are
    dropped so the table stays bounded by concurrently active subjects.
    """

    def __init__(self) -> None:
        """Create an empty limiter."""
        self._counts: dict[str, int] = {}

    def try_acquire(self, key: str, max_in_flight: int) -> bool:
        """Take a slot for *key*; return False (taking nothing) when at the cap."""
        current = self._counts.get(key, 0)
        if current >= max_in_flight:
            return False
        self._counts[key] = current + 1
        return True

    def release(self, key: str) -> None:
        """Return a slot taken by ``try_acquire`` (safe to call once per acquire)."""
        remaining = self._counts.get(key, 1) - 1
        if remaining <= 0:
            self._counts.pop(key, None)
        else:
            self._counts[key] = remaining

    def in_flight(self, key: str) -> int:
        """Return the current in-flight count for *key*."""
        return self._counts.get(key, 0)
