"""Unit tests for cross-reference registry.

Tests registry seeding, type lookup, parsing, and validation without database.
"""

# flake8: noqa: E501

import dataclasses

import pytest

from apps.api.common.refs.registry import (
    ResolvableType,
    get_registry,
    get_type,
    register,
)


class TestRegistryInitialization:
    """Test registry seeding at startup."""

    def test_registry_seeded_with_core_types(self):
        """Registry should contain all core resource types on import."""
        registry = get_registry()

        # Verify all core types are registered
        expected_types = {
            "tenant",
            "organization",
            "entity",
            "identity",
            "software",
            "service",
            "ipam_prefix",
            "ipam_address",
            "ipam_vlan",
            "issue",
            "project",
            "milestone",
        }

        registered_types = set(registry.keys())
        assert expected_types.issubset(
            registered_types
        ), f"Missing types: {expected_types - registered_types}"

    def test_registry_core_types_count(self):
        """Core registry should have at least 12 types."""
        registry = get_registry()
        assert len(registry) >= 12, f"Expected at least 12 types, got {len(registry)}"

    def test_type_has_required_fields(self):
        """Each registered type should have all required fields."""
        entity_type = get_type("entity")
        assert entity_type is not None
        assert entity_type.type == "entity"
        assert entity_type.module == "infrastructure"
        assert entity_type.table == "entities"
        assert entity_type.id_column == "id"
        assert entity_type.village_id_column == "village_id"
        assert entity_type.url_pattern == "/entities/{id}"
        assert entity_type.title_column == "name"


class TestTypeRegistration:
    """Test type registration and extension."""

    def test_register_new_type(self):
        """Should allow registering new types at runtime."""
        new_type = ResolvableType(
            type="test_resource",
            module="test_module",
            table="test_resources",
            url_pattern="/test-resources/{id}",
            title_column="title",
        )

        try:
            register(new_type)
            retrieved = get_type("test_resource")
            assert retrieved == new_type
        except ValueError:
            # Type may already be registered from prior tests
            pytest.skip("Type already registered")

    def test_register_duplicate_type_raises_error(self):
        """Registering a duplicate type should raise ValueError."""
        # Try to register an existing core type
        duplicate = ResolvableType(
            type="entity",
            module="different",
            table="different_table",
        )

        with pytest.raises(ValueError, match="already registered"):
            register(duplicate)


class TestTypeResolution:
    """Test type lookup by name."""

    def test_get_type_returns_resolvable_type(self):
        """get_type() should return a ResolvableType instance."""
        entity_type = get_type("entity")
        assert isinstance(entity_type, ResolvableType)

    def test_get_type_returns_none_for_unknown(self):
        """get_type() should return None for unknown types."""
        result = get_type("nonexistent_type")
        assert result is None

    def test_all_registered_types_retrievable(self):
        """Every registered type should be retrievable by get_type()."""
        registry = get_registry()
        for type_name in registry.keys():
            result = get_type(type_name)
            assert result is not None
            assert result.type == type_name


class TestRegistryFrozenFields:
    """Test that ResolvableType is immutable (frozen dataclass)."""

    def test_resolvable_type_is_frozen(self):
        """ResolvableType fields should not be modifiable."""
        entity_type = get_type("entity")

        with pytest.raises((AttributeError, dataclasses.FrozenInstanceError), match=""):
            entity_type.url_pattern = "/modified"


def test_registry_not_empty():
    """Registry should never be empty."""
    registry = get_registry()
    assert len(registry) > 0


def test_registry_module_mappings():
    """Verify module mappings for core types."""
    expected_mappings = {
        "tenant": "tenant",
        "organization": "infrastructure",
        "entity": "infrastructure",
        "identity": "identity",
        "software": "sbom",
        "service": "sbom",
        "issue": "issues",
        "project": "issues",
        "milestone": "issues",
        "ipam_prefix": "ipam",
        "ipam_address": "ipam",
        "ipam_vlan": "ipam",
    }

    for type_name, expected_module in expected_mappings.items():
        rt = get_type(type_name)
        assert rt is not None, f"Type {type_name} not found"
        assert (
            rt.module == expected_module
        ), f"Type {type_name} module mismatch: expected {expected_module}, got {rt.module}"
