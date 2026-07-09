"""Unit tests for module registry and resolution logic."""

import pytest
from quart import Quart

from apps.api.modules import MODULES
from apps.api.modules.registry import ModuleManifest, mount, resolve_enabled


class TestModuleManifest:
    """Test ModuleManifest dataclass."""

    def test_manifest_creation(self):
        """Test creating a module manifest."""

        def dummy_blueprints():
            return []

        manifest = ModuleManifest(
            name="test_module",
            title="Test Module",
            license_feature=None,
            depends_on=(),
            blueprints=dummy_blueprints,
            models_import=(),
            table_prefix=None,
            nav_id="nav_test",
            scopes=("test:read", "test:write"),
            worker_task_groups=(),
            optional_services=(),
            default_enabled=True,
        )

        assert manifest.name == "test_module"
        assert manifest.title == "Test Module"
        assert manifest.scopes == ("test:read", "test:write")
        assert manifest.default_enabled is True

    def test_manifest_frozen(self):
        """Test that manifests are frozen (immutable)."""

        def dummy_blueprints():
            return []

        manifest = ModuleManifest(
            name="test_module",
            title="Test Module",
            license_feature=None,
            depends_on=(),
            blueprints=dummy_blueprints,
            models_import=(),
            table_prefix=None,
            nav_id="nav_test",
            scopes=("test:read",),
            worker_task_groups=(),
            optional_services=(),
            default_enabled=True,
        )

        with pytest.raises(AttributeError):
            manifest.name = "changed"


class TestResolveEnabled:
    """Test resolve_enabled() module resolution logic."""

    def test_default_all_enabled(self):
        """Test that 'all' resolves to all default_enabled modules."""
        env = {"ELDER_MODULES_ENABLED": "all"}
        enabled = resolve_enabled(env)

        # All Phase 0 modules should be enabled by default
        assert len(enabled) > 0
        names = {m.name for m in enabled}
        assert "infrastructure" in names
        assert "ipam" in names
        assert "sbom" in names

    def test_empty_default_all(self):
        """Test that empty env defaults to 'all'."""
        env = {}
        enabled = resolve_enabled(env)

        # Should default to "all"
        assert len(enabled) > 0
        names = {m.name for m in enabled}
        assert "infrastructure" in names

    def test_csv_subset(self):
        """Test CSV-based module selection."""
        env = {"ELDER_MODULES_ENABLED": "infrastructure,ipam"}
        enabled = resolve_enabled(env)

        names = {m.name for m in enabled}
        assert names == {"infrastructure", "ipam"}

    def test_per_module_override_enable(self):
        """Test per-module override to enable a module."""
        env = {
            "ELDER_MODULES_ENABLED": "infrastructure",
            "ELDER_MODULE_IPAM": "true",
        }
        enabled = resolve_enabled(env)

        names = {m.name for m in enabled}
        assert "infrastructure" in names
        assert "ipam" in names

    def test_per_module_override_disable(self):
        """Test per-module override to disable a module."""
        env = {
            "ELDER_MODULES_ENABLED": "all",
            "ELDER_MODULE_IPAM": "false",
        }
        enabled = resolve_enabled(env)

        names = {m.name for m in enabled}
        assert "infrastructure" in names
        assert "ipam" not in names

    def test_unknown_module_error(self):
        """Test error on unknown module name."""
        env = {"ELDER_MODULES_ENABLED": "nonexistent"}

        with pytest.raises(ValueError, match="Unknown modules"):
            resolve_enabled(env)

    def test_dependency_validation_satisfied(self):
        """Test that satisfied dependencies are allowed."""
        # discovery depends on infrastructure
        env = {"ELDER_MODULES_ENABLED": "infrastructure,discovery"}
        enabled = resolve_enabled(env)

        names = {m.name for m in enabled}
        assert "infrastructure" in names
        assert "discovery" in names

    def test_dependency_validation_missing(self):
        """Test error when dependency is not enabled."""
        # discovery depends on infrastructure
        env = {"ELDER_MODULES_ENABLED": "discovery"}

        with pytest.raises(ValueError, match="depends on disabled modules"):
            resolve_enabled(env)

    def test_topological_sort(self):
        """Test that modules are returned in dependency order."""
        env = {"ELDER_MODULES_ENABLED": "discovery,infrastructure"}
        enabled = resolve_enabled(env)

        names = [m.name for m in enabled]
        # infrastructure should come before discovery (dependency order)
        assert names.index("infrastructure") < names.index("discovery")

    def test_scopes_present(self):
        """Test that all modules have scopes defined."""
        env = {"ELDER_MODULES_ENABLED": "all"}
        enabled = resolve_enabled(env)

        for manifest in enabled:
            assert manifest.scopes is not None
            assert len(manifest.scopes) > 0
            # Each scope should be string
            for scope in manifest.scopes:
                assert isinstance(scope, str)


class TestMount:
    """Test mount() blueprint registration."""

    def test_mount_empty(self):
        """Test mounting with no modules."""
        app = Quart(__name__)
        mount(app, [])

        assert app.extensions["elder_modules"] == {}
        assert app.extensions["elder_module_by_blueprint"] == {}

    def test_mount_stores_metadata(self):
        """Test that mount stores module metadata on app."""
        env = {"ELDER_MODULES_ENABLED": "infrastructure"}
        enabled = resolve_enabled(env)

        app = Quart(__name__)
        mount(app, enabled)

        modules = app.extensions["elder_modules"]
        assert "infrastructure" in modules
        assert modules["infrastructure"].name == "infrastructure"

    def test_mount_registers_blueprints(self):
        """Test that mount registers blueprints with correct prefixes."""
        env = {"ELDER_MODULES_ENABLED": "infrastructure"}
        enabled = resolve_enabled(env)

        app = Quart(__name__)
        mount(app, enabled)

        # Check that blueprints were registered
        blueprint_to_module = app.extensions["elder_module_by_blueprint"]
        assert len(blueprint_to_module) > 0
        # Should have entity blueprint from infrastructure module
        assert any("entit" in name for name in blueprint_to_module.keys())

    def test_mount_multiple_modules(self):
        """Test mounting multiple modules."""
        env = {"ELDER_MODULES_ENABLED": "infrastructure,ipam,sbom"}
        enabled = resolve_enabled(env)

        app = Quart(__name__)
        mount(app, enabled)

        modules = app.extensions["elder_modules"]
        assert "infrastructure" in modules
        assert "ipam" in modules
        assert "sbom" in modules


class TestPhaseZeroModules:
    """Test Phase 0 module definitions."""

    def test_all_modules_have_required_fields(self):
        """Test that all modules have required fields."""
        for manifest in MODULES:
            assert manifest.name
            assert manifest.title
            assert manifest.nav_id
            assert isinstance(manifest.scopes, tuple)
            assert len(manifest.scopes) > 0
            assert manifest.blueprints is not None
            assert callable(manifest.blueprints)

    def test_all_module_names_unique(self):
        """Test that all module names are unique."""
        names = [m.name for m in MODULES]
        assert len(names) == len(set(names))

    def test_infrastructure_module(self):
        """Test infrastructure module definition."""
        infra = next(m for m in MODULES if m.name == "infrastructure")
        assert infra.title == "Infrastructure CMDB"
        assert infra.license_feature is None
        assert infra.depends_on == ()
        assert infra.table_prefix is None
        assert "infrastructure:read" in infra.scopes
        assert "infrastructure:write" in infra.scopes
        assert "infrastructure:admin" in infra.scopes

    def test_access_reviews_license_gated(self):
        """Test that access_reviews module is license-gated."""
        ar = next(m for m in MODULES if m.name == "access_reviews")
        assert ar.license_feature == "access-reviews"
        assert "access_reviews:admin" in ar.scopes

    def test_discovery_depends_on_infrastructure(self):
        """Test that discovery module depends on infrastructure."""
        discovery = next(m for m in MODULES if m.name == "discovery")
        assert "infrastructure" in discovery.depends_on

    def test_phase_zero_count(self):
        """Test the registered-module count (bump when a module is added)."""
        # Phase 0 (9): infrastructure, ipam, sbom, services_oncall,
        # issues, discovery, secrets, webhooks_alerting, access_reviews
        # Phase 3 (+2): helpdesk, documents
        # Phase 3b (+1): pages
        assert len(MODULES) == 12
