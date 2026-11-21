"""Service node heartbeat model for node-count license enforcement.

Every running pod/process of a given Elder service type (API, worker,
scanner, etc.) registers a row here on startup and refreshes it on a
periodic cadence. Licensing's node counter (Task 3,
apps/api/common/licensing/counters.py::count_nodes) treats any row with a
recent heartbeat as "active" -- there is no explicit deregistration on pod
shutdown, so a crashed/evicted pod's row simply goes stale and ages out of
the count on its own.
"""

# flake8: noqa: E501

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Column, DateTime, String

from apps.api.models.base import Base, IDMixin, TimestampMixin


class ServiceNode(Base, IDMixin, TimestampMixin):
    """One row per running pod/process of a given Elder service type."""

    __tablename__ = "service_nodes"

    service_type = Column(
        String(64),
        nullable=False,
        index=True,
        comment="Elder service type, e.g. 'main' (API), 'worker', 'scanner'",
    )
    pod_id = Column(
        String(128),
        nullable=False,
        unique=True,
        comment="Stable identifier for this pod/process, typically $HOSTNAME",
    )
    heartbeat_ts = Column(
        DateTime(timezone=True),
        nullable=False,
        comment="Last time this pod confirmed it is still running",
    )


def register_node(db: Any, service_type: str, pod_id: str) -> int:
    """Register (or refresh) this pod's service_nodes row.

    Upsert semantics: if a row for `pod_id` already exists (e.g. a pod
    restart reusing the same name), refresh its service_type and heartbeat
    instead of raising on the unique `pod_id` constraint.

    Args:
        db: penguin-dal DAL instance.
        service_type: Elder service type this pod belongs to.
        pod_id: Stable identifier for this pod/process.

    Returns:
        int: The `service_nodes.id` of the (possibly pre-existing) row.
    """
    now = datetime.now(UTC)
    existing = db(db.service_nodes.pod_id == pod_id).select().first()
    if existing:
        db(db.service_nodes.pod_id == pod_id).update(
            service_type=service_type, heartbeat_ts=now
        )
        return existing.id
    return db.service_nodes.insert(
        service_type=service_type, pod_id=pod_id, heartbeat_ts=now
    )


def heartbeat_node(db: Any, pod_id: str) -> bool:
    """Refresh heartbeat_ts for an already-registered pod.

    Args:
        db: penguin-dal DAL instance.
        pod_id: Stable identifier for this pod/process.

    Returns:
        bool: True if a row was updated, False if `pod_id` has no row yet
        (callers should fall back to register_node in that case).
    """
    updated = db(db.service_nodes.pod_id == pod_id).update(
        heartbeat_ts=datetime.now(UTC)
    )
    return bool(updated)


def count_active_nodes(db: Any, service_type: str, stale_after_s: int = 90) -> int:
    """Count service_nodes rows of `service_type` with a recent heartbeat.

    Args:
        db: penguin-dal DAL instance.
        service_type: Elder service type to count.
        stale_after_s: A row is "active" if its heartbeat is within this many
            seconds of now (default 90s -- 1.5x the 60s heartbeat cadence).

    Returns:
        int: Count of active nodes for this service_type.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_s)
    return db(
        (db.service_nodes.service_type == service_type)
        & (db.service_nodes.heartbeat_ts > cutoff)
    ).count()
