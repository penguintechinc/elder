"""Redis Streams job-bus core library for Elder.

Async-first job queue using Redis Streams with support for:
- Idempotent job enqueuing (UUID deduplication)
- Consumer group management
- Automatic dead-letter routing after max delivery attempts
- Stale consumer reclamation (XAUTOCLAIM)
- Result publication to separate result stream

Design: enqueue/consume via injected async Redis client. No app context coupling.
"""

from .core import JobBus, JobEnvelope, ReclamedMessage

__all__ = ["JobBus", "JobEnvelope", "ReclamedMessage"]
