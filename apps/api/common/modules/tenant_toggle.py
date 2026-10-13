"""Tenant module toggle service with Redis caching.

Provides runtime access to per-tenant module enablement state with
automatic Redis caching and fail-soft behavior.

Uses penguin-dal for all database queries (never SQLAlchemy at runtime).
"""

# flake8: noqa: E501

import logging
from typing import Any, Optional

import redis

logger = logging.getLogger(__name__)


def is_module_enabled(
    db: Any, redis_client: redis.Redis, tenant_id: int, module_name: str, default: bool
) -> bool:
    """
    Check if a module is enabled for a specific tenant.

    Checks Redis cache first; on miss, loads all tenant modules for the tenant
    from the database and caches them. If the module has no explicit row in
    the database, returns the module's default_enabled flag.

    Fails gracefully if Redis is unavailable — falls back to direct DB read.

    Args:
        db: penguin-dal DAL instance for database queries
        redis_client: Redis client for caching
        tenant_id: Tenant ID to check
        module_name: Module name (e.g., 'infrastructure', 'sbom')
        default: Default enabled state if module row doesn't exist

    Returns:
        bool: Whether the module is enabled for this tenant
    """
    cache_key = f"elder:modtoggle:{tenant_id}"

    # Try to get from cache
    try:
        cached = redis_client.get(cache_key)
        if cached:
            import json

            module_map = json.loads(cached)
            return module_map.get(module_name, default)
    except redis.RedisError as e:
        logger.warning(f"Redis unavailable, falling back to DB: {e}")

    # Cache miss or Redis unavailable — load from database
    try:
        rows = db(db.tenant_modules.tenant_id == tenant_id).select()
        module_map = {row.module_name: row.enabled for row in rows}

        # Try to cache for next request (fail-soft if Redis is still down)
        try:
            import json

            redis_client.setex(cache_key, 60, json.dumps(module_map))
        except redis.RedisError as e:
            logger.debug(f"Could not cache module toggles: {e}")

        return module_map.get(module_name, default)
    except Exception as e:
        logger.error(f"Failed to load tenant modules: {e}")
        return default


def set_module_enabled(
    db: Any,
    redis_client: redis.Redis,
    tenant_id: int,
    module_name: str,
    enabled: bool,
    settings: dict | None = None,
) -> None:
    """
    Set module enablement state for a tenant and bust Redis cache.

    Upserts a row in the tenant_modules table (or updates if exists) and
    invalidates the Redis cache for this tenant.

    Args:
        db: penguin-dal DAL instance for database queries
        redis_client: Redis client for caching
        tenant_id: Tenant ID
        module_name: Module name
        enabled: Whether the module should be enabled
        settings: Optional module-specific settings (JSON)

    Raises:
        Exception: If database upsert fails
    """
    try:
        # Try to update existing row
        existing = (
            db(
                (db.tenant_modules.tenant_id == tenant_id)
                & (db.tenant_modules.module_name == module_name)
            )
            .select()
            .first()
        )

        if existing:
            db(
                (db.tenant_modules.tenant_id == tenant_id)
                & (db.tenant_modules.module_name == module_name)
            ).update(enabled=enabled, settings=settings)
        else:
            # Insert new row
            db.tenant_modules.insert(
                tenant_id=tenant_id,
                module_name=module_name,
                enabled=enabled,
                settings=settings,
            )

        # Bust Redis cache for this tenant
        cache_key = f"elder:modtoggle:{tenant_id}"
        try:
            redis_client.delete(cache_key)
        except redis.RedisError as e:
            logger.warning(f"Could not bust Redis cache: {e}")

    except Exception as e:
        logger.error(f"Failed to set module enabled state: {e}")
        raise


def get_tenant_modules(
    db: Any,
    redis_client: redis.Redis,
    tenant_id: int,
    all_modules_with_defaults: dict[str, bool],
) -> dict[str, bool]:
    """
    Get the effective module enablement map for a tenant.

    For each module in all_modules_with_defaults, returns the tenant's
    override if it exists, otherwise the module's default_enabled value.

    Args:
        db: penguin-dal DAL instance for database queries
        redis_client: Redis client for caching
        tenant_id: Tenant ID
        all_modules_with_defaults: Dict of {module_name: default_enabled}

    Returns:
        dict[str, bool]: Effective module enablement for this tenant
    """
    cache_key = f"elder:modtoggle:{tenant_id}"

    # Try cache first
    try:
        cached = redis_client.get(cache_key)
        if cached:
            import json

            cached_map = json.loads(cached)
            # Fill in any missing modules from defaults
            result = dict(all_modules_with_defaults)
            result.update(cached_map)
            return result
    except redis.RedisError as e:
        logger.warning(f"Redis unavailable, falling back to DB: {e}")

    # Cache miss or Redis unavailable — load from database
    try:
        rows = db(db.tenant_modules.tenant_id == tenant_id).select()
        tenant_overrides = {row.module_name: row.enabled for row in rows}

        # Build effective map (defaults + tenant overrides)
        result = dict(all_modules_with_defaults)
        result.update(tenant_overrides)

        # Try to cache (fail-soft)
        try:
            import json

            redis_client.setex(cache_key, 60, json.dumps(tenant_overrides))
        except redis.RedisError as e:
            logger.debug(f"Could not cache module toggles: {e}")

        return result
    except Exception as e:
        logger.error(f"Failed to load tenant modules: {e}")
        return all_modules_with_defaults
