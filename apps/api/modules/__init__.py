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
    "apps.api.models.references",
    "apps.api.models.security",
    "apps.api.models.tenant",
    "apps.api.models.tenant_modules",
)


def _infrastructure_blueprints() -> list[tuple[Blueprint, str]]:
    """Load infrastructure module blueprints (entities, compute, storage, deps, graph)."""
    from apps.api.modules.infrastructure.routes import (
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
    from apps.api.modules.ipam import routes as ipam

    api_prefix = "/api/v1"
    return [(ipam.bp, f"{api_prefix}/ipam")]


def _sbom_blueprints() -> list[tuple[Blueprint, str]]:
    """Load SBOM module blueprints (software, SBOM, vulnerabilities, licenses)."""
    from apps.api.modules.sbom.routes import (
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
    from apps.api.modules.services_oncall.routes import on_call_rotations

    api_prefix = "/api/v1"
    return [(on_call_rotations.bp, f"{api_prefix}/on-call")]


def _issues_blueprints() -> list[tuple[Blueprint, str]]:
    """Load issues module blueprints (issues, projects, milestones, labels, comments)."""
    from apps.api.modules.issues.routes import (
        comments,
        issues,
        labels,
        metadata,
        milestones,
        projects,
    )

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
    from apps.api.modules.discovery.routes import discovery, google_workspace, iam, sync

    api_prefix = "/api/v1"
    return [
        (discovery.bp, f"{api_prefix}/discovery"),
        (sync.bp, f"{api_prefix}/sync"),
        (google_workspace.bp, f"{api_prefix}/google-workspace"),
        (iam.bp, f"{api_prefix}/iam"),
    ]


def _secrets_blueprints() -> list[tuple[Blueprint, str]]:
    """Load secrets module blueprints (secrets, keys, certificates, builtin secrets)."""
    from apps.api.modules.secrets.routes import (
        builtin_secrets,
        certificates,
        keys,
        secrets,
    )

    api_prefix = "/api/v1"
    return [
        (secrets.bp, f"{api_prefix}/secrets"),
        (keys.bp, f"{api_prefix}/keys"),
        (certificates.bp, f"{api_prefix}/certificates"),
        (builtin_secrets.bp, None),  # Already has /api/v1/builtin-secrets prefix
    ]


def _webhooks_alerting_blueprints() -> list[tuple[Blueprint, str]]:
    """Load webhooks and alerting blueprints."""
    from apps.api.modules.webhooks_alerting.routes import costs, webhooks

    api_prefix = "/api/v1"
    return [
        (webhooks.bp, f"{api_prefix}/webhooks"),
        (costs.bp, f"{api_prefix}/costs"),
    ]


def _access_reviews_blueprints() -> list[tuple[Blueprint, str]]:
    """Load access reviews module blueprints (enterprise feature)."""
    from apps.api.modules.access_reviews.routes import (
        access_reviews,
        group_membership,
        resource_roles,
    )

    api_prefix = "/api/v1"
    return [
        (resource_roles.bp, f"{api_prefix}/resource-roles"),
        (access_reviews.bp, api_prefix),
        (group_membership.bp, f"{api_prefix}/group-membership"),
    ]


def _documents_blueprints() -> list[tuple[Blueprint, str]]:
    """Load documents module blueprints (knowledge base, collections, versions)."""
    from apps.api.modules.documents.routes import collections, documents, versions

    api_prefix = "/api/v1"
    return [
        (documents.bp, f"{api_prefix}/documents"),
        (collections.bp, f"{api_prefix}/collections"),
        (versions.bp, f"{api_prefix}/documents"),  # Versions share doc prefix
    ]


def _pages_blueprints() -> list[tuple[Blueprint, str]]:
    """Load pages module blueprints (wiki-style pages, collections)."""
    from apps.api.modules.pages.routes import pages

    api_prefix = "/api/v1"
    return [
        (pages.bp, f"{api_prefix}/pages"),
    ]


def _diagrams_blueprints() -> list[tuple[Blueprint, str]]:
    """Load diagrams module blueprints (drawings, versioning, sharing, collaboration).

    Phase 4b-1: CRUD + version save/load.
    Phase 4b-2: Sharing + collections.
    Phase 4b-3: Comments, templates, shape libraries.
    Phase 4b-4: Storage providers + export (JSON, SVG, raster).
    Phase 4d-1: Real-time collaboration (WebSocket + Redis pub/sub).
    """
    from apps.api.modules.diagrams.routes import (
        collab,
        collections,
        comments,
        diagrams,
        export,
        libraries,
        shares,
        storage_providers,
        templates,
    )

    api_prefix = "/api/v1"
    return [
        (diagrams.bp, f"{api_prefix}/diagrams"),
        (shares.bp, f"{api_prefix}/diagrams"),
        (collab.collab_bp, f"{api_prefix}/diagrams"),
        (collections.bp, f"{api_prefix}/diagram-collections"),
        (comments.bp, f"{api_prefix}/diagrams"),
        (templates.bp, f"{api_prefix}/diagram-templates"),
        (libraries.bp, f"{api_prefix}/diagram-libraries"),
        (storage_providers.storage_bp, f"{api_prefix}/diagram-storage"),
        (export.export_bp, f"{api_prefix}/diagrams"),
    ]


def _streams_blueprints() -> list[tuple[Blueprint, str]]:
    """Load streams module blueprints (Phase 4b: CRUD + execution + webhooks + approvals + hooks)."""
    from apps.api.modules.streams.routes import approvals, hooks, streams, webhooks

    api_prefix = "/api/v1"
    return [
        (streams.bp, f"{api_prefix}/streams"),
        (webhooks.bp, f"{api_prefix}/streams"),
        (approvals.bp, f"{api_prefix}/streams"),
        (hooks.bp, f"{api_prefix}/hooks"),
    ]


def _flows_blueprints() -> list[tuple[Blueprint, str]]:
    """Load flows module blueprints (CI/CD pipeline orchestration: pipelines, stages, credentials, promotions, webhooks)."""
    from apps.api.modules.flows.routes import (
        credentials,
        hooks,
        pipelines,
        promotions,
        stage_children,
        stages,
    )

    api_prefix = "/api/v1"
    return [
        (pipelines.bp, f"{api_prefix}/flows"),
        (stages.bp, f"{api_prefix}/flows"),
        (stage_children.bp, f"{api_prefix}/flows"),
        (credentials.bp, f"{api_prefix}/flows/credentials"),
        (promotions.bp, f"{api_prefix}/flows"),
        (hooks.bp, f"{api_prefix}/flows-hooks"),
    ]


def _helpdesk_blueprints() -> list[tuple[Blueprint, str]]:
    """Load helpdesk module blueprints (tickets, messages, dashboard, settings, CRM, email, forms)."""
    from apps.api.modules.helpdesk.routes import (
        canned_responses,
        companies,
        contacts,
        dashboard,
        email_accounts,
        messages,
        sla_policies,
        teams,
        ticket_forms,
        tickets,
    )

    api_prefix = "/api/v1"
    return [
        (tickets.bp, f"{api_prefix}/tickets"),
        (messages.bp, f"{api_prefix}/tickets"),
        (email_accounts.bp, f"{api_prefix}/email-accounts"),
        (ticket_forms.bp, f"{api_prefix}/ticket-forms"),
        (dashboard.bp, f"{api_prefix}/dashboard"),
        (sla_policies.bp, f"{api_prefix}/sla-policies"),
        (canned_responses.bp, f"{api_prefix}/canned-responses"),
        (teams.bp, f"{api_prefix}/teams"),
        (companies.bp, f"{api_prefix}/companies"),
        (contacts.bp, f"{api_prefix}/contacts"),
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
            "apps.api.modules.infrastructure.models.entity",
            "apps.api.modules.infrastructure.models.organization",
            "apps.api.modules.infrastructure.models.infrastructure",
            "apps.api.modules.infrastructure.models.dependency",
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
        models_import=("apps.api.modules.ipam.models",),
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
        models_import=("apps.api.modules.sbom.models.assets",),
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
        models_import=("apps.api.modules.services_oncall.models",),
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
            "apps.api.modules.issues.models.issue",
            "apps.api.modules.issues.models.project",
            "apps.api.modules.issues.models.metadata",
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
        models_import=("apps.api.modules.discovery.models",),
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
        models_import=("apps.api.modules.secrets.models",),
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
        models_import=(
            "apps.api.modules.webhooks_alerting.models.webhooks",
            "apps.api.modules.webhooks_alerting.models.alert_config",
            "apps.api.modules.webhooks_alerting.models.cost",
        ),
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
            "apps.api.modules.access_reviews.models.access_review",
            "apps.api.modules.access_reviews.models.resource_role",
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
    ModuleManifest(
        name="documents",
        title="Documents & Knowledge Base",
        license_feature=None,
        depends_on=(),
        blueprints=_documents_blueprints,
        models_import=("apps.api.modules.documents.models.documents",),
        table_prefix="doc_",
        nav_id="nav_documents",
        scopes=("documents:read", "documents:write", "documents:admin"),
        worker_task_groups=(),
        optional_services=(),
        default_enabled=True,
    ),
    ModuleManifest(
        name="pages",
        title="Pages & Documentation",
        license_feature=None,
        depends_on=("documents",),
        blueprints=_pages_blueprints,
        models_import=("apps.api.modules.pages.models.pages",),
        table_prefix="pg_",
        nav_id="nav_pages",
        scopes=("pages:read", "pages:write", "pages:admin"),
        worker_task_groups=(),
        optional_services=(),
        default_enabled=True,
    ),
    ModuleManifest(
        name="diagrams",
        title="Diagrams & Drawing",
        license_feature=None,
        depends_on=(),
        blueprints=_diagrams_blueprints,
        models_import=("apps.api.modules.diagrams.models.diagrams",),
        table_prefix="dg_",
        nav_id="nav_diagrams",
        scopes=("diagrams:read", "diagrams:write", "diagrams:admin"),
        worker_task_groups=(),
        optional_services=("minio",),
        default_enabled=True,
    ),
    ModuleManifest(
        name="streams",
        title="Streams (Workflows)",
        license_feature=None,
        depends_on=(),
        blueprints=_streams_blueprints,
        models_import=("apps.api.modules.streams.models.streams",),
        table_prefix="stream_",
        nav_id="nav_streams",
        scopes=("streams:read", "streams:write", "streams:admin", "streams:execute"),
        worker_task_groups=("streams",),
        optional_services=(),
        default_enabled=True,
    ),
    ModuleManifest(
        name="flows",
        title="Flows (CI/CD)",
        license_feature=None,
        depends_on=(),
        blueprints=_flows_blueprints,
        models_import=("apps.api.modules.flows.models.flows",),
        table_prefix="iceflows_",
        nav_id="nav_flows",
        scopes=(
            "flows:read",
            "flows:write",
            "flows:admin",
            "flows:approve",
            "flows:execute",
        ),
        worker_task_groups=(),
        optional_services=(),
        default_enabled=True,
    ),
    ModuleManifest(
        name="helpdesk",  # default_enabled=True like all modules; prod rollout gated OFF via base ConfigMap ELDER_MODULE_HELPDESK=false
        title="Helpdesk & Support",
        license_feature=None,
        depends_on=(),
        blueprints=_helpdesk_blueprints,
        models_import=("apps.api.modules.helpdesk.models.helpdesk",),
        table_prefix="hd_",
        nav_id="nav_helpdesk",
        scopes=("helpdesk:read", "helpdesk:write", "helpdesk:admin"),
        worker_task_groups=(
            "helpdesk_email_send",
            "helpdesk_email_poll",
            "helpdesk_sla_breach",
        ),
        optional_services=(),
        default_enabled=True,
    ),
)

__all__ = ["MODULES", "CORE_MODELS", "ModuleManifest"]
