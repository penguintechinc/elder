"""Event emission utilities for Elder — publishes events to Redis Streams.

Events flow through Redis Streams (elder:events:{domain}) for downstream
processing by workers and event handlers. Designed to be fail-soft:
if Redis is unavailable, events are logged as warnings but do not break
the request path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timezone
from typing import Any
from uuid import uuid4

import structlog

logger = structlog.get_logger(__name__)


async def emit_event(
    domain: str,
    action: str,
    resource_id: int | str,
    tenant_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Emit event to Redis Stream for downstream processing.

    Args:
        domain: Event domain (e.g., 'infrastructure', 'issues', 'discovery')
        action: Action name (e.g., 'created', 'updated', 'deleted')
        resource_id: ID of affected resource (entity ID, issue ID, etc.)
        tenant_id: Tenant ID (optional; omitted for system events)
        payload: Additional event metadata (optional)

    Fails soft: logs warning if Redis unavailable, does not raise.
    """
    try:
        import redis
        from quart import current_app

        redis_url = current_app.config.get("REDIS_URL", "redis://localhost:6379/0")

        # Build event envelope
        event_id = str(uuid4())
        envelope = {
            "event_id": event_id,
            "domain": domain,
            "action": action,
            "resource_id": str(resource_id),
            "tenant_id": tenant_id,
            "payload": payload or {},
            "emitted_at": datetime.now(UTC).isoformat(),
        }

        # Connect and emit to stream
        r = redis.from_url(redis_url)
        stream_key = f"elder:events:{domain}"
        r.xadd(stream_key, {"event": json.dumps(envelope)})

        logger.debug(
            "event_emitted",
            event_id=event_id,
            domain=domain,
            action=action,
            resource_id=resource_id,
        )

    except ImportError:
        logger.warning("redis not installed; event emission unavailable")
    except redis.ConnectionError as e:
        logger.warning(
            "event_emission_failed_redis_unavailable",
            domain=domain,
            action=action,
            error=str(e),
        )
    except Exception as e:
        logger.warning(
            "event_emission_failed",
            domain=domain,
            action=action,
            error=str(e),
        )
