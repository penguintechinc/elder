"""Unit tests for the tier -> LimitSet resolver (apps/api/common/licensing/limits.py)."""

from dataclasses import replace
from types import SimpleNamespace

from apps.api.common.licensing.limits import TIER_DEFAULTS, LimitSet, resolve_limits


class FakeLicenseClient:
    """Minimal stand-in for penguin_licensing's LicenseClient in unit tests."""

    def __init__(self, tier: str, limits: dict | None = None):
        self._tier = tier
        self._limits = limits or {}

    def validate(self):
        """Return a lightweight validation object exposing .tier/.limits."""
        return SimpleNamespace(tier=self._tier, limits=self._limits)


class TestResolveLimits:
    """Tests for resolve_limits() tier resolution and license-server overrides."""

    def test_none_client_returns_community_defaults(self):
        """No license client configured -> community (Free) tier defaults."""
        tier, limits = resolve_limits(None)

        assert tier == "community"
        assert limits == LimitSet(
            max_global_admins=1,
            max_tenant_admins=0,
            max_teams=1,
            max_tenants=1,
            max_objects=1000,
            max_nodes_per_type=1,
        )

    def test_enterprise_tier_is_unlimited(self):
        """Enterprise tier -> every limit is None (unlimited)."""
        client = FakeLicenseClient(tier="enterprise")

        tier, limits = resolve_limits(client)

        assert tier == "enterprise"
        assert limits == LimitSet(
            max_global_admins=None,
            max_tenant_admins=None,
            max_teams=None,
            max_tenants=None,
            max_objects=None,
            max_nodes_per_type=None,
        )

    def test_professional_tier_defaults(self):
        """Professional tier matches the Global Constraints table."""
        client = FakeLicenseClient(tier="professional")

        tier, limits = resolve_limits(client)

        assert tier == "professional"
        assert limits == LimitSet(
            max_global_admins=1,
            max_tenant_admins=10,
            max_teams=None,
            max_tenants=1,
            max_objects=None,
            max_nodes_per_type=1,
        )

    def test_limits_override_applies_single_field(self):
        """A .limits override replaces only the matching field, not the whole set."""
        client = FakeLicenseClient(tier="community", limits={"max_objects": 5000})

        tier, limits = resolve_limits(client)

        assert tier == "community"
        assert limits.max_objects == 5000
        assert limits == replace(TIER_DEFAULTS["community"], max_objects=5000)

    def test_limits_override_ignores_unknown_keys(self):
        """Unknown keys in .limits are ignored rather than raising."""
        client = FakeLicenseClient(
            tier="community", limits={"max_objects": 2000, "not_a_real_field": 42}
        )

        _, limits = resolve_limits(client)

        assert limits.max_objects == 2000
        assert not hasattr(limits, "not_a_real_field")

    def test_unknown_tier_falls_back_to_community(self):
        """An unrecognized tier string falls back to community defaults."""
        client = FakeLicenseClient(tier="not-a-real-tier")

        tier, limits = resolve_limits(client)

        assert tier == "not-a-real-tier"
        assert limits == TIER_DEFAULTS["community"]

    def test_validate_raising_falls_back_to_community(self):
        """A client whose .validate() raises degrades to community, never crashes."""

        class BrokenClient:
            def validate(self):
                raise RuntimeError("license server unreachable")

        tier, limits = resolve_limits(BrokenClient())

        assert tier == "community"
        assert limits == TIER_DEFAULTS["community"]

    def test_tier_defaults_table_has_all_tiers(self):
        """TIER_DEFAULTS covers exactly the three supported tiers."""
        assert set(TIER_DEFAULTS) == {"community", "professional", "enterprise"}

    def test_distinct_clients_do_not_share_cached_result(self):
        """Per-process caching must not leak one client's resolved limits onto another."""
        community = FakeLicenseClient(tier="community")
        enterprise = FakeLicenseClient(tier="enterprise")

        tier_a, _ = resolve_limits(community)
        tier_b, _ = resolve_limits(enterprise)

        assert tier_a == "community"
        assert tier_b == "enterprise"
