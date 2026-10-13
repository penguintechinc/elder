"""Worker job queue infrastructure.

Provides job-bus consumer components:
- Group resolver: maps enabled modules to job-bus consumer groups
- Handler registry: maps groups to async task handlers
- Consumer loop: reads, processes, and acknowledges jobs
- XAUTOCLAIM sweeper: reclaims stale messages via XAUTOCLAIM
"""

from __future__ import annotations

__all__ = ["resolve_worker_groups", "get_handler", "HANDLER_REGISTRY"]
