"""Unit tests for tenant module toggle service.

Tests Redis cache hit/miss/bust, fail-soft behavior when Redis is unavailable,
and unknown module handling with defaults.
"""

# flake8: noqa: E501


import json
from unittest.mock import MagicMock, patch

import pytest
import redis

from apps.api.common.modules.tenant_toggle import (
    get_tenant_modules,
    is_module_enabled,
    set_module_enabled,
)


class TestIsModuleEnabled:
    """Tests for is_module_enabled() function."""

    def test_cache_hit_returns_enabled_state(self):
        """Cache hit: should return cached enabled state without DB query."""
        mock_db = MagicMock()
        mock_redis = MagicMock(spec=redis.Redis)

        # Mock Redis cache hit
        cached_data = json.dumps({"infrastructure": True, "sbom": False})
        mock_redis.get.return_value = cached_data

        result = is_module_enabled(
            mock_db, mock_redis, tenant_id=1, module_name="infrastructure", default=True
        )

        assert result is True
        mock_redis.get.assert_called_once_with("elder:modtoggle:1")
        mock_db.assert_not_called()

    def test_cache_hit_returns_unknown_module_default(self):
        """Cache hit: unknown module not in cache should return default."""
        mock_db = MagicMock()
        mock_redis = MagicMock(spec=redis.Redis)

        # Mock Redis cache hit with some modules
        cached_data = json.dumps({"infrastructure": True})
        mock_redis.get.return_value = cached_data

        result = is_module_enabled(
            mock_db, mock_redis, tenant_id=1, module_name="unknown", default=False
        )

        assert result is False

    def test_cache_miss_loads_from_db_and_caches(self):
        """Cache miss: should load from DB, cache result, and return value."""
        mock_db = MagicMock()
        mock_redis = MagicMock(spec=redis.Redis)

        # Mock Redis cache miss
        mock_redis.get.return_value = None

        # Mock DB rows
        mock_row_1 = MagicMock()
        mock_row_1.module_name = "infrastructure"
        mock_row_1.enabled = True

        mock_row_2 = MagicMock()
        mock_row_2.module_name = "sbom"
        mock_row_2.enabled = False

        # Mock PyDAL query chain: db(condition).select()
        query_obj = MagicMock()
        query_obj.select.return_value = [mock_row_1, mock_row_2]
        mock_db.return_value = query_obj

        result = is_module_enabled(
            mock_db, mock_redis, tenant_id=1, module_name="sbom", default=True
        )

        assert result is False
        # Verify Redis cache was set
        assert mock_redis.setex.called

    def test_cache_miss_unknown_module_returns_default(self):
        """Cache miss: unknown module not in DB should return default."""
        mock_db = MagicMock()
        mock_redis = MagicMock(spec=redis.Redis)

        # Mock Redis cache miss
        mock_redis.get.return_value = None

        # Mock DB rows (no matching module)
        query_obj = MagicMock()
        query_obj.select.return_value = []
        mock_db.return_value = query_obj

        result = is_module_enabled(
            mock_db, mock_redis, tenant_id=1, module_name="unknown", default=False
        )

        assert result is False

    def test_redis_unavailable_falls_back_to_db(self):
        """Redis unavailable: should fall back to direct DB read."""
        mock_db = MagicMock()
        mock_redis = MagicMock(spec=redis.Redis)

        # Mock Redis error on get
        mock_redis.get.side_effect = redis.RedisError("Connection failed")

        # Mock DB rows
        mock_row = MagicMock()
        mock_row.module_name = "infrastructure"
        mock_row.enabled = True

        query_obj = MagicMock()
        query_obj.select.return_value = [mock_row]
        mock_db.return_value = query_obj

        result = is_module_enabled(
            mock_db, mock_redis, tenant_id=1, module_name="infrastructure", default=False
        )

        assert result is True
        # DB should have been queried
        mock_db.assert_called()


class TestSetModuleEnabled:
    """Tests for set_module_enabled() function."""

    def test_insert_new_module_row_and_bust_cache(self):
        """Insert: should create new row and bust Redis cache."""
        mock_db = MagicMock()
        mock_redis = MagicMock(spec=redis.Redis)

        # Mock: no existing row
        query_obj = MagicMock()
        query_obj.select.return_value.first.return_value = None
        mock_db.return_value = query_obj

        set_module_enabled(
            mock_db,
            mock_redis,
            tenant_id=1,
            module_name="infrastructure",
            enabled=True,
            settings={"key": "value"},
        )

        # Verify insert was called
        mock_db.tenant_modules.insert.assert_called_once()
        # Verify cache was busted
        mock_redis.delete.assert_called_once_with("elder:modtoggle:1")

    def test_update_existing_module_row_and_bust_cache(self):
        """Update: should update existing row and bust Redis cache."""
        mock_db = MagicMock()
        mock_redis = MagicMock(spec=redis.Redis)

        # Mock: existing row found
        existing_row = MagicMock()
        query_obj = MagicMock()
        query_obj.select.return_value.first.return_value = existing_row
        update_obj = MagicMock()
        query_obj.update.return_value = update_obj
        mock_db.return_value = query_obj

        set_module_enabled(
            mock_db,
            mock_redis,
            tenant_id=1,
            module_name="infrastructure",
            enabled=False,
            settings={"key": "value2"},
        )

        # Verify update was called
        query_obj.update.assert_called_once()
        # Verify cache was busted
        mock_redis.delete.assert_called_once_with("elder:modtoggle:1")

    def test_redis_cache_bust_fails_gracefully(self):
        """Cache bust fail: should succeed even if Redis delete fails."""
        mock_db = MagicMock()
        mock_redis = MagicMock(spec=redis.Redis)

        # Mock: no existing row
        query_obj = MagicMock()
        query_obj.select.return_value.first.return_value = None
        mock_db.return_value = query_obj

        # Mock Redis delete failure
        mock_redis.delete.side_effect = redis.RedisError("Cache bust failed")

        # Should not raise exception
        set_module_enabled(
            mock_db, mock_redis, tenant_id=1, module_name="infrastructure", enabled=True
        )

        # DB insert should still succeed
        mock_db.tenant_modules.insert.assert_called_once()


class TestGetTenantModules:
    """Tests for get_tenant_modules() function."""

    def test_cache_hit_fills_defaults(self):
        """Cache hit: should merge cached overrides with defaults."""
        mock_db = MagicMock()
        mock_redis = MagicMock(spec=redis.Redis)

        all_modules = {
            "infrastructure": True,
            "sbom": True,
            "issues": False,
        }

        # Mock Redis cache hit (overrides)
        cached_data = json.dumps({"infrastructure": False, "sbom": False})
        mock_redis.get.return_value = cached_data

        result = get_tenant_modules(
            mock_db, mock_redis, tenant_id=1, all_modules_with_defaults=all_modules
        )

        # Should return merged: infrastructure and sbom overridden, issues uses default
        assert result["infrastructure"] is False
        assert result["sbom"] is False
        assert result["issues"] is False

    def test_cache_miss_loads_from_db_and_fills_defaults(self):
        """Cache miss: should load overrides from DB and merge with defaults."""
        mock_db = MagicMock()
        mock_redis = MagicMock(spec=redis.Redis)

        all_modules = {
            "infrastructure": True,
            "sbom": True,
            "issues": False,
        }

        # Mock Redis cache miss
        mock_redis.get.return_value = None

        # Mock DB rows (only some modules have overrides)
        mock_row = MagicMock()
        mock_row.module_name = "infrastructure"
        mock_row.enabled = False

        query_obj = MagicMock()
        query_obj.select.return_value = [mock_row]
        mock_db.return_value = query_obj

        result = get_tenant_modules(
            mock_db, mock_redis, tenant_id=1, all_modules_with_defaults=all_modules
        )

        # Should return merged: infrastructure overridden, sbom/issues use defaults
        assert result["infrastructure"] is False
        assert result["sbom"] is True
        assert result["issues"] is False

    def test_redis_unavailable_falls_back_to_db(self):
        """Redis unavailable: should fall back to DB and return merged map."""
        mock_db = MagicMock()
        mock_redis = MagicMock(spec=redis.Redis)

        all_modules = {
            "infrastructure": True,
            "sbom": True,
        }

        # Mock Redis error
        mock_redis.get.side_effect = redis.RedisError("Connection failed")

        # Mock DB rows
        mock_row = MagicMock()
        mock_row.module_name = "infrastructure"
        mock_row.enabled = False

        query_obj = MagicMock()
        query_obj.select.return_value = [mock_row]
        mock_db.return_value = query_obj

        result = get_tenant_modules(
            mock_db, mock_redis, tenant_id=1, all_modules_with_defaults=all_modules
        )

        assert result["infrastructure"] is False
        assert result["sbom"] is True
