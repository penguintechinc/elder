"""Module registry and resolution logic for Elder Platform.

Implements lazy loading and conditional mounting of feature modules based on:
1. Deployment configuration (env vars)
2. License entitlement (pending Phase 2)
3. Per-tenant configuration (pending Phase 2)
4. Per-feature PostHog flags (pending Phase 2)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import structlog
from quart import Blueprint, Quart

logger = structlog.get_logger(__name__)


@dataclass(slots=True, frozen=True)
class ModuleManifest:
    """Frozen dataclass describing a toggleable feature module.

    Attributes:
        name: Unique module identifier (e.g., 'infrastructure', 'helpdesk')
        title: Human-readable module name (e.g., 'Infrastructure CMDB')
        license_feature: License key (e.g., 'access-reviews'); None = always available
        depends_on: Tuple of module names this depends on; validated at resolution time
        blueprints: Zero-arg callable returning list of (Blueprint, url_prefix) tuples;
                   lazy to avoid circular imports
        models_import: Tuple of dotted module paths to import for table registration
                      (e.g., ('apps.api.models.issue', 'apps.api.models.sbom'))
        table_prefix: Prefix for module-specific tables (e.g., 'hd_', 'dg_', 'ai_');
                     None for Elder core modules
        nav_id: Sidebar navigation category ID for this module (e.g., 'nav_infrastructure')
        scopes: Tuple of OIDC scope strings for this module
               (e.g., ('infrastructure:read', 'infrastructure:write', 'infrastructure:admin'))
        worker_task_groups: Tuple of worker task group names for this module
                           (e.g., ('discovery', 'sbom_scan'))
        optional_services: Tuple of optional service names (e.g., ('neo4j', 'minio'))
        default_enabled: Whether module is on by default; False → opt-in via config
    """

    name: str
    title: str
    license_feature: str | None
    depends_on: tuple[str, ...]
    blueprints: Callable[[], list[tuple[Blueprint, str]]]
    models_import: tuple[str, ...]
    table_prefix: str | None
    nav_id: str
    scopes: tuple[str, ...]
    worker_task_groups: tuple[str, ...]
    optional_services: tuple[str, ...]
    default_enabled: bool = True


def resolve_enabled(env: Mapping[str, str]) -> list[ModuleManifest]:
    """Resolve which modules are enabled based on environment configuration.

    Reads ELDER_MODULES_ENABLED (CSV list or "all") and per-module overrides
    ELDER_MODULE_<UPPERNAME>=true|false. Validates dependency graph and returns
    topologically sorted list of enabled modules.

    Args:
        env: Environment variables (typically os.environ)

    Returns:
        Topologically sorted list of enabled ModuleManifest objects

    Raises:
        ValueError: If unknown module requested, or dependency validation fails
    """
    # Import here to avoid circular import at module-load time
    from apps.api.modules import MODULES

    # Build name → manifest map
    manifests_by_name = {m.name: m for m in MODULES}

    # Parse ELDER_MODULES_ENABLED
    enabled_str = env.get("ELDER_MODULES_ENABLED", "all").strip()
    if enabled_str.lower() == "all":
        enabled_names = {m.name for m in MODULES if m.default_enabled}
    else:
        enabled_names = {n.strip() for n in enabled_str.split(",") if n.strip()}

    # Apply per-module overrides
    for module in MODULES:
        env_key = f"ELDER_MODULE_{module.name.upper()}"
        if env_key in env:
            override = env[env_key].lower() in ("true", "1", "yes")
            if override:
                enabled_names.add(module.name)
            else:
                enabled_names.discard(module.name)

    # Validate all names exist
    unknown = enabled_names - set(manifests_by_name.keys())
    if unknown:
        raise ValueError(f"Unknown modules requested: {unknown}")

    # Validate dependency graph
    enabled_manifests = [manifests_by_name[name] for name in enabled_names]
    for manifest in enabled_manifests:
        missing_deps = set(manifest.depends_on) - enabled_names
        if missing_deps:
            raise ValueError(
                f"Module '{manifest.name}' depends on disabled modules: {missing_deps}"
            )

    # Topological sort (simple depth-first)
    sorted_manifests = []
    visited = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        manifest = manifests_by_name[name]
        for dep_name in manifest.depends_on:
            if dep_name in enabled_names:
                visit(dep_name)
        if name in enabled_names:
            sorted_manifests.append(manifest)

    for name in enabled_names:
        visit(name)

    logger.info(
        "modules_resolved",
        enabled_count=len(sorted_manifests),
        enabled_modules=[m.name for m in sorted_manifests],
    )

    return sorted_manifests


def mount(app: Quart, enabled: list[ModuleManifest]) -> None:
    """Register enabled module blueprints and store metadata on app.

    Calls each manifest's blueprints() callable, registers all returned
    (Blueprint, url_prefix) pairs, and stores:
    - app.extensions["elder_modules"]: {name: manifest}
    - app.extensions["elder_module_by_blueprint"]: {blueprint.name: module_name}

    Args:
        app: Quart application instance
        enabled: Topologically sorted list of enabled ModuleManifest objects
    """
    modules_dict = {}
    blueprint_to_module = {}

    for manifest in enabled:
        try:
            # Call lazy blueprints() to get list of (blueprint, url_prefix) pairs
            blueprint_pairs = manifest.blueprints()

            for blueprint, url_prefix in blueprint_pairs:
                # If url_prefix is None, use blueprint's internal prefix (don't override)
                if url_prefix is not None:
                    app.register_blueprint(blueprint, url_prefix=url_prefix)
                else:
                    app.register_blueprint(blueprint)
                blueprint_to_module[blueprint.name] = manifest.name
                logger.debug(
                    "blueprint_registered",
                    module=manifest.name,
                    blueprint=blueprint.name,
                    url_prefix=url_prefix,
                )

            modules_dict[manifest.name] = manifest

        except Exception as e:
            logger.error(
                "module_mount_failed",
                module=manifest.name,
                error=str(e),
            )
            raise

    # Store metadata on app for later inspection
    if "elder_modules" not in app.extensions:
        app.extensions["elder_modules"] = {}
    if "elder_module_by_blueprint" not in app.extensions:
        app.extensions["elder_module_by_blueprint"] = {}

    app.extensions["elder_modules"].update(modules_dict)
    app.extensions["elder_module_by_blueprint"].update(blueprint_to_module)

    logger.info(
        "modules_mounted",
        module_count=len(modules_dict),
        modules=[m.name for m in enabled],
    )
