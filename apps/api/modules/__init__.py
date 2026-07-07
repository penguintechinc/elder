"""Elder module registry and manifest definitions.

Phase 0: All modules re-export existing blueprints from apps.api.api.v1
with zero file moves and zero behavior changes when all are enabled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from quart import Blueprint

from apps.api.modules.registry import ModuleManifest

# Core models owned by no module — always loaded for Base.metadata discovery
CORE_MODELS = (
    "apps.api.models.audit",
    "apps.api.models.auth_providers",
    "apps.api.models.identity",
    "apps.api.models.rbac",
    "apps.api.models.security",
    "apps.api.models.tenant",
)


def _infrastructure_blueprints() -> list[tuple[Blueprint, str]]:
    """Load infrastructure module blueprints (entities, compute, storage, deps, graph)."""
    from apps.api.api.v1 import (
        data_stores,
        dependencies,
        entities,
        entity_types,
        graph,
        networking,
        organization_tree,
        organizations_pydal,
    )

    api_prefix = "/api/v1"
    return [
        (organizations_pydal.bp, f"{api_prefix}/organizations"),
        (entities.bp, f"{api_prefix}/entities"),
        (entity_types.bp, f"{api_prefix}/entity-types"),
        (dependencies.bp, f"{api_prefix}/dependencies"),
        (graph.bp, f"{api_prefix}/graph"),
        (networking.bp, None),  # Already has /api/v1/networking prefix
        (data_stores.bp, f"{api_prefix}/data-stores"),
        (organization_tree.bp, api_prefix),
    ]


def _ipam_blueprints() -> list[tuple[Blueprint, str]]:
    """Load IPAM module blueprints."""
    from apps.api.api.v1 import ipam

    api_prefix = "/api/v1"
    return [(ipam.bp, f"{api_prefix}/ipam")]


def _sbom_blueprints() -> list[tuple[Blueprint, str]]:
    """Load SBOM module blueprints (software, SBOM, vulnerabilities, licenses)."""
    from apps.api.api.v1 import (
        license_policies,
        sbom,
        sbom_scans,
        sbom_schedules,
        services,
        software,
        vulnerabilities,
    )

    api_prefix = "/api/v1"
    return [
        (software.bp, f"{api_prefix}/software"),
        (services.bp, f"{api_prefix}/services"),
        (sbom.bp, f"{api_prefix}/sbom/components"),
        (sbom_scans.bp, f"{api_prefix}/sbom/scans"),
        (sbom_schedules.bp, f"{api_prefix}/sbom/schedules"),
        (vulnerabilities.bp, f"{api_prefix}/vulnerabilities"),
        (license_policies.bp, f"{api_prefix}/license-policies"),
    ]


def _services_oncall_blueprints() -> list[tuple[Blueprint, str]]:
    """Load service catalog and on-call rotation blueprints."""
    from apps.api.api.v1 import on_call_rotations

    api_prefix = "/api/v1"
    return [(on_call_rotations.bp, f"{api_prefix}/on-call")]


def _issues_blueprints() -> list[tuple[Blueprint, str]]:
    """Load issues module blueprints (issues, projects, milestones, labels, comments)."""
    from apps.api.api.v1 import comments, issues, labels, metadata, milestones, projects

    api_prefix = "/api/v1"
    return [
        (issues.bp, f"{api_prefix}/issues"),
        (labels.bp, f"{api_prefix}/labels"),
        (milestones.bp, f"{api_prefix}/milestones"),
        (projects.bp, f"{api_prefix}/projects"),
        (metadata.bp, f"{api_prefix}/metadata"),
        (comments.bp, f"{api_prefix}/issues"),
    ]


def _discovery_blueprints() -> list[tuple[Blueprint, str]]:
    """Load discovery and sync blueprints (discovery, sync, connectors, IAM)."""
    from apps.api.api.v1 import discovery, google_workspace, iam, sync

    api_prefix = "/api/v1"
    return [
        (discovery.bp, f"{api_prefix}/discovery"),
        (sync.bp, f"{api_prefix}/sync"),
        (google_workspace.bp, f"{api_prefix}/google-workspace"),
        (iam.bp, f"{api_prefix}/iam"),
    ]


def _secrets_blueprints() -> list[tuple[Blueprint, str]]:
    """Load secrets module blueprints (secrets, keys, certificates, builtin secrets)."""
    from apps.api.api.v1 import builtin_secrets, certificates, keys, secrets

    api_prefix = "/api/v1"
    return [
        (secrets.bp, f"{api_prefix}/secrets"),
        (keys.bp, f"{api_prefix}/keys"),
        (certificates.bp, f"{api_prefix}/certificates"),
        (builtin_secrets.bp, None),  # Already has /api/v1/builtin-secrets prefix
    ]


def _webhooks_alerting_blueprints() -> list[tuple[Blueprint, str]]:
    """Load webhooks and alerting blueprints."""
    from apps.api.api.v1 import costs, webhooks

    api_prefix = "/api/v1"
    return [
        (webhooks.bp, f"{api_prefix}/webhooks"),
        (costs.bp, f"{api_prefix}/costs"),
    ]


def _access_reviews_blueprints() -> list[tuple[Blueprint, str]]:
    """Load access reviews module blueprints (enterprise feature)."""
    from apps.api.api.v1 import access_reviews, group_membership, resource_roles

    api_prefix = "/api/v1"
    return [
        (resource_roles.bp, f"{api_prefix}/resource-roles"),
        (access_reviews.bp, api_prefix),
        (group_membership.bp, f"{api_prefix}/group-membership"),
    ]


# Explicit module registry — Phase 0 modules
MODULES = (
    ModuleManifest(
        name="infrastructure",
        title="Infrastructure CMDB",
        license_feature=None,
        depends_on=(),
        blueprints=_infrastructure_blueprints,
        models_import=(
            "apps.api.models.entity",
            "apps.api.models.organization",
            "apps.api.models.infrastructure",
            "apps.api.models.dependency",
        ),
        table_prefix=None,
        nav_id="nav_infrastructure",
        scopes=(
            "infrastructure:read",
            "infrastructure:write",
            "infrastructure:admin",
        ),
        worker_task_groups=(),
        optional_services=(),
        default_enabled=True,
    ),
    ModuleManifest(
        name="ipam",
        title="IP Address Management",
        license_feature=None,
        depends_on=(),
        blueprints=_ipam_blueprints,
        models_import=("apps.api.models.ipam",),
        table_prefix=None,
        nav_id="nav_ipam",
        scopes=("ipam:read", "ipam:write", "ipam:admin"),
        worker_task_groups=(),
        optional_services=(),
        default_enabled=True,
    ),
    ModuleManifest(
        name="sbom",
        title="Software Bill of Materials",
        license_feature=None,
        depends_on=(),
        blueprints=_sbom_blueprints,
        models_import=(),  # TODO: add sbom-related model imports
        table_prefix=None,
        nav_id="nav_sbom",
        scopes=("sbom:read", "sbom:write", "sbom:admin"),
        worker_task_groups=("sbom_scan",),
        optional_services=(),
        default_enabled=True,
    ),
    ModuleManifest(
        name="services_oncall",
        title="Service Catalog & On-Call",
        license_feature=None,
        depends_on=(),
        blueprints=_services_oncall_blueprints,
        models_import=("apps.api.models.oncall",),
        table_prefix=None,
        nav_id="nav_services_oncall",
        scopes=(
            "services_oncall:read",
            "services_oncall:write",
            "services_oncall:admin",
        ),
        worker_task_groups=(),
        optional_services=(),
        default_enabled=True,
    ),
    ModuleManifest(
        name="issues",
        title="Issues & Project Tracking",
        license_feature=None,
        depends_on=(),
        blueprints=_issues_blueprints,
        models_import=(
            "apps.api.models.issue",
            "apps.api.models.project",
            "apps.api.models.metadata",
        ),
        table_prefix=None,
        nav_id="nav_issues",
        scopes=("issues:read", "issues:write", "issues:admin"),
        worker_task_groups=(),
        optional_services=(),
        default_enabled=True,
    ),
    ModuleManifest(
        name="discovery",
        title="Discovery & Sync",
        license_feature=None,
        depends_on=("infrastructure",),
        blueprints=_discovery_blueprints,
        models_import=("apps.api.models.discovery",),
        table_prefix=None,
        nav_id="nav_discovery",
        scopes=("discovery:read", "discovery:write", "discovery:admin"),
        worker_task_groups=("discovery",),
        optional_services=(),
        default_enabled=True,
    ),
    ModuleManifest(
        name="secrets",
        title="Secrets Management",
        license_feature=None,
        depends_on=(),
        blueprints=_secrets_blueprints,
        models_import=("apps.api.models.secrets",),
        table_prefix=None,
        nav_id="nav_secrets",
        scopes=("secrets:read", "secrets:write", "secrets:admin"),
        worker_task_groups=(),
        optional_services=(),
        default_enabled=True,
    ),
    ModuleManifest(
        name="webhooks_alerting",
        title="Webhooks & Alerting",
        license_feature=None,
        depends_on=(),
        blueprints=_webhooks_alerting_blueprints,
        models_import=("apps.api.models.webhooks", "apps.api.models.alert_config"),
        table_prefix=None,
        nav_id="nav_webhooks_alerting",
        scopes=(
            "webhooks_alerting:read",
            "webhooks_alerting:write",
            "webhooks_alerting:admin",
        ),
        worker_task_groups=(),
        optional_services=(),
        default_enabled=True,
    ),
    ModuleManifest(
        name="access_reviews",
        title="Access Reviews",
        license_feature="access-reviews",
        depends_on=(),
        blueprints=_access_reviews_blueprints,
        models_import=(
            "apps.api.models.access_review",
            "apps.api.models.resource_role",
        ),
        table_prefix=None,
        nav_id="nav_access_reviews",
        scopes=(
            "access_reviews:read",
            "access_reviews:write",
            "access_reviews:admin",
        ),
        worker_task_groups=(),
        optional_services=(),
        default_enabled=True,
    ),
)

__all__ = ["MODULES", "CORE_MODELS", "ModuleManifest"]
