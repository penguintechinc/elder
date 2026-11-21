"""Village ID generator for Elder application.

Generates unique hierarchical identifiers for all trackable resources.
Format: TTTTTTTT-OOOOOOOOOOOOOOOO (tenant-object)
- Tenant: 32-bit (8 hex chars) = hex(tenant_id)
- Object: 64-bit (16 hex chars) = sequential per tenant via Redis INCR
- Total: 25 chars, fits String(32)
"""

# flake8: noqa: E501

import asyncio
import re
from dataclasses import dataclass
from typing import Optional

import redis.asyncio as aioredis
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

VILLAGE_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{16}$")


@dataclass(slots=True)
class VillageId:
    """Parsed village ID components."""

    tenant_id: int
    object_seq: int


def generate_village_id(tenant_id: int, redis_client: Redis | None = None) -> str:
    """Generate a village ID for the given tenant.

    Uses the AD-3 format: TTTTTTTT-OOOOOOOOOOOOOOOO
    - tenant: 8 hex chars = hex(tenant_id)
    - object: 16 hex chars = sequential per tenant via Redis INCR

    Args:
        tenant_id: The integer tenant ID (32-bit)
        redis_client: Sync redis.StrictRedis client for allocating sequence

    Returns:
        str: 25-character village ID (TTTTTTTT-OOOOOOOOOOOOOOOO)

    Raises:
        ValueError: If tenant_id is invalid or redis_client is None/unavailable
    """
    if not isinstance(tenant_id, int) or tenant_id < 0:
        raise ValueError(f"tenant_id must be non-negative int, got {tenant_id}")

    if redis_client is None:
        raise ValueError("redis_client required for village_id generation")

    # Allocate next sequence for this tenant
    counter_key = f"elder:vid:{tenant_id:08x}"
    seq = redis_client.incr(counter_key)

    # Format: tenant (8 hex) + object (16 hex)
    return f"{tenant_id:08x}-{seq:016x}"


async def agenerate_village_id(
    tenant_id: int, redis_client: AsyncRedis | None = None
) -> str:
    """Generate a village ID asynchronously.

    Async variant for use in Quart handlers with an async redis client.

    Args:
        tenant_id: The integer tenant ID (32-bit)
        redis_client: Async redis.asyncio.Redis client for allocating sequence

    Returns:
        str: 25-character village ID (TTTTTTTT-OOOOOOOOOOOOOOOO)

    Raises:
        ValueError: If tenant_id is invalid or redis_client is None/unavailable
    """
    if not isinstance(tenant_id, int) or tenant_id < 0:
        raise ValueError(f"tenant_id must be non-negative int, got {tenant_id}")

    if redis_client is None:
        raise ValueError("redis_client required for village_id generation")

    # Allocate next sequence for this tenant
    counter_key = f"elder:vid:{tenant_id:08x}"
    seq = await redis_client.incr(counter_key)

    # Format: tenant (8 hex) + object (16 hex)
    return f"{tenant_id:08x}-{seq:016x}"


def parse_village_id(village_id: str) -> VillageId:
    """Parse a village ID into its components.

    Args:
        village_id: The full village ID (e.g., "0000002a-000000000000f3c1")

    Returns:
        VillageId: Dataclass with tenant_id and object_seq

    Raises:
        ValueError: If format is invalid
    """
    if not is_valid_village_id(village_id):
        raise ValueError(f"Invalid village ID format: {village_id}")

    tenant_hex, object_hex = village_id.split("-")
    tenant_id = int(tenant_hex, 16)
    object_seq = int(object_hex, 16)

    return VillageId(tenant_id=tenant_id, object_seq=object_seq)


def is_valid_village_id(village_id: str) -> bool:
    """Check if a village ID is valid.

    Valid format: TTTTTTTT-OOOOOOOOOOOOOOOO (25 chars, lowercase hex)

    Args:
        village_id: The village ID to validate

    Returns:
        bool: True if valid, False otherwise
    """
    if not isinstance(village_id, str):
        return False
    return bool(VILLAGE_ID_RE.match(village_id))
