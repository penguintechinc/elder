"""Resolvable type registry for cross-reference resolution.

Declarative single source of truth for all types that can be referenced via
village_id or module:type:id format. Generalizes the hardcoded lookup_village_id.py
by providing a registry-driven resolver.

Types can be registered at startup via register() for extensibility by modules.
"""

# flake8: noqa: E501

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from shared.utils.village_id import is_valid_village_id, parse_village_id

if TYPE_CHECKING:
    from pydal.objects import Table


@dataclass(slots=True, frozen=True)
class ResolvableType:
    """Declarative resolvable resource type with table and query information.

    Attributes:
        type: Resource type identifier (e.g., "entity", "identity")
        module: Module name owning this type (e.g., "infrastructure", "tenant")
        table: Table name in database (e.g., "entities")
        id_column: Column name for the resource ID (default: "id")
        village_id_column: Column name for the village_id (default: "village_id")
        url_pattern: URL template for navigation (e.g., "/entities/{id}")
        title_column: Column name for resource display title (e.g., "name")
        tenant_id_is_string: True when the table stores ``tenant_id`` as VARCHAR
            rather than INTEGER (e.g. ``geo_regions``, ``geo_locations``).
    """

    type: str
    module: str
    table: str
    id_column: str = "id"
    village_id_column: str = "village_id"
    url_pattern: str = "/{type}/{id}"
    title_column: str = "name"
    tenant_id_is_string: bool = False


# Tables without a tenant_id or organization_id column whose village_id encodes
# the owning tenant in its 8-hex-char prefix (TTTTTTTT-OOOOOOOOOOOOOOOO).
_VID_PREFIX_SCOPED_TABLES = frozenset(
    {
        "identity_groups",
        "backups",
        "backup_jobs",
        "sync_configs",
        "issue_labels",
    }
)

# Registry: all resolvable types (mutable, seeded at startup)
_REGISTRY: dict[str, ResolvableType] = {}


def register(resolvable_type: ResolvableType) -> None:
    """Register a resolvable type in the registry.

    Enables modules to extend the registry at startup.

    Args:
        resolvable_type: ResolvableType instance to register

    Raises:
        ValueError: If type is already registered (prevent silent overwrites)
    """
    if resolvable_type.type in _REGISTRY:
        raise ValueError(f"ResolvableType '{resolvable_type.type}' already registered")
    _REGISTRY[resolvable_type.type] = resolvable_type


def _init_registry() -> None:
    """Initialize the registry with all known resolvable types.

    URL patterns match the exact React Router routes in ``web/src/App.tsx``
    and ``web/src/modules/*/index.tsx``.
    """
    # Core tenant/org types
    register(
        ResolvableType(
            type="tenant",
            module="tenant",
            table="tenants",
            url_pattern="/admin/tenants/{id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="organization",
            module="infrastructure",
            table="organizations",
            url_pattern="/organizations/{id}",
            title_column="name",
        )
    )

    # Infrastructure (entities covers compute/VMs/K8s/LXD/servers/devices)
    register(
        ResolvableType(
            type="entity",
            module="infrastructure",
            table="entities",
            url_pattern="/entities/{id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="data_store",
            module="infrastructure",
            table="data_stores",
            url_pattern="/data-inventory?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="networking_resource",
            module="infrastructure",
            table="networking_resources",
            url_pattern="/networking?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="dependency",
            module="infrastructure",
            table="dependencies",
            url_pattern="/dependencies?highlight={id}",
            title_column="dependency_type",
        )
    )
    register(
        ResolvableType(
            type="geo_region",
            module="infrastructure",
            table="geo_regions",
            url_pattern="/map?region={id}",
            title_column="name",
            tenant_id_is_string=True,
        )
    )
    register(
        ResolvableType(
            type="geo_location",
            module="infrastructure",
            table="geo_locations",
            url_pattern="/map?location={id}",
            title_column="name",
            tenant_id_is_string=True,
        )
    )

    # Identity & IAM
    register(
        ResolvableType(
            type="identity",
            module="identity",
            table="identities",
            url_pattern="/iam?highlight={id}",
            title_column="email",
        )
    )
    register(
        ResolvableType(
            type="identity_group",
            module="identity",
            table="identity_groups",
            url_pattern="/iam?tab=groups&highlight={id}",
            title_column="name",
        )
    )

    # SBOM / Software / Services / Vulnerabilities / License Policies
    register(
        ResolvableType(
            type="software",
            module="sbom",
            table="software",
            url_pattern="/software?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="service",
            module="sbom",
            table="services",
            url_pattern="/services?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="vulnerability",
            module="sbom",
            table="vulnerabilities",
            url_pattern="/vulnerabilities?highlight={id}",
            title_column="cve_id",
        )
    )
    register(
        ResolvableType(
            type="sbom_scan",
            module="sbom",
            table="sbom_scans",
            url_pattern="/sbom?highlight={id}",
            title_column="scan_type",
        )
    )
    register(
        ResolvableType(
            type="sbom_component",
            module="sbom",
            table="sbom_components",
            url_pattern="/sbom?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="sbom_scan_schedule",
            module="sbom",
            table="sbom_scan_schedules",
            url_pattern="/sbom?highlight={id}",
            title_column="schedule_cron",
        )
    )
    register(
        ResolvableType(
            type="license_policy",
            module="sbom",
            table="license_policies",
            url_pattern="/admin/license-policies?highlight={id}",
            title_column="name",
        )
    )

    # Services & On-Call
    register(
        ResolvableType(
            type="on_call_rotation",
            module="services_oncall",
            table="on_call_rotations",
            url_pattern="/on-call-rotations?highlight={id}",
            title_column="name",
        )
    )

    # IPAM
    register(
        ResolvableType(
            type="ipam_prefix",
            module="ipam",
            table="ipam_prefixes",
            url_pattern="/ipam?tab=prefixes&highlight={id}",
            title_column="prefix",
        )
    )
    register(
        ResolvableType(
            type="ipam_address",
            module="ipam",
            table="ipam_addresses",
            url_pattern="/ipam?tab=addresses&highlight={id}",
            title_column="address",
        )
    )
    register(
        ResolvableType(
            type="ipam_vlan",
            module="ipam",
            table="ipam_vlans",
            url_pattern="/ipam?tab=vlans&highlight={id}",
            title_column="name",
        )
    )

    # Issues / Work Management
    register(
        ResolvableType(
            type="issue",
            module="issues",
            table="issues",
            url_pattern="/issues/{id}",
            title_column="title",
        )
    )
    register(
        ResolvableType(
            type="project",
            module="issues",
            table="projects",
            url_pattern="/projects/{id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="milestone",
            module="issues",
            table="milestones",
            url_pattern="/milestones?highlight={id}",
            title_column="title",
        )
    )
    register(
        ResolvableType(
            type="label",
            module="issues",
            table="labels",
            url_pattern="/labels?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="issue_label",
            module="issues",
            table="issue_labels",
            url_pattern="/labels?highlight={id}",
            title_column="name",
        )
    )

    # Documents (knowledge base)
    # slug is the resolvable id for wiki-link resolution: [[slug]] or [[document:slug]]
    register(
        ResolvableType(
            type="document",
            module="documents",
            table="doc_documents",
            id_column="slug",
            url_pattern="/documents/{id}",
            title_column="title",
        )
    )
    register(
        ResolvableType(
            type="doc_collection",
            module="documents",
            table="doc_collections",
            url_pattern="/documents?collection={id}",
            title_column="name",
        )
    )

    # Pages
    register(
        ResolvableType(
            type="page",
            module="pages",
            table="pg_pages",
            id_column="slug",
            url_pattern="/pages/{id}",
            title_column="title",
        )
    )

    # Streams (automation playbooks)
    register(
        ResolvableType(
            type="playbook",
            module="streams",
            table="stream_playbooks",
            url_pattern="/streams/{id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="stream_template",
            module="streams",
            table="stream_templates",
            url_pattern="/streams?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="stream_custom_module",
            module="streams",
            table="stream_custom_modules",
            url_pattern="/streams?highlight={id}",
            title_column="name",
        )
    )

    # Diagrams
    register(
        ResolvableType(
            type="diagram",
            module="diagrams",
            table="dg_diagrams",
            url_pattern="/diagrams/{id}",
            title_column="title",
        )
    )
    register(
        ResolvableType(
            type="dg_collection",
            module="diagrams",
            table="dg_collections",
            url_pattern="/diagrams?collection={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="dg_template",
            module="diagrams",
            table="dg_templates",
            url_pattern="/diagrams?highlight={id}",
            title_column="name",
        )
    )

    # Flows (CI/CD)
    register(
        ResolvableType(
            type="flow",
            module="flows",
            table="iceflows",
            url_pattern="/flows/{id}",
            title_column="name",
        )
    )

    # Helpdesk
    register(
        ResolvableType(
            type="ticket",
            module="helpdesk",
            table="hd_tickets",
            url_pattern="/helpdesk/tickets/{id}",
            title_column="subject",
        )
    )
    register(
        ResolvableType(
            type="helpdesk_team",
            module="helpdesk",
            table="hd_teams",
            url_pattern="/helpdesk/teams?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="hd_sla_policy",
            module="helpdesk",
            table="hd_sla_policies",
            url_pattern="/helpdesk/sla-policies?highlight={id}",
            title_column="name",
        )
    )

    # AI Resources
    register(
        ResolvableType(
            type="ai_resource",
            module="ai",
            table="ai_resources",
            url_pattern="/ai?highlight={id}",
            title_column="name",
        )
    )

    # Secrets, Keys & Certificates
    register(
        ResolvableType(
            type="secret",
            module="secrets",
            table="secrets",
            url_pattern="/secrets?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="builtin_secret",
            module="secrets",
            table="builtin_secrets",
            url_pattern="/secrets?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="secret_provider",
            module="secrets",
            table="secret_providers",
            url_pattern="/secrets?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="crypto_key",
            module="secrets",
            table="crypto_keys",
            url_pattern="/keys?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="key_provider",
            module="secrets",
            table="key_providers",
            url_pattern="/keys?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="certificate",
            module="secrets",
            table="certificates",
            url_pattern="/certificates?highlight={id}",
            title_column="name",
        )
    )

    # Webhooks & Backups
    register(
        ResolvableType(
            type="webhook",
            module="webhooks_alerting",
            table="webhooks",
            url_pattern="/webhooks?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="inbound_webhook",
            module="webhooks_alerting",
            table="inbound_webhooks",
            url_pattern="/webhooks?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="backup",
            module="backup",
            table="backups",
            url_pattern="/backups?highlight={id}",
            title_column="filename",
        )
    )
    register(
        ResolvableType(
            type="backup_job",
            module="backup",
            table="backup_jobs",
            url_pattern="/backups?highlight={id}",
            title_column="name",
        )
    )

    # Access Reviews
    register(
        ResolvableType(
            type="access_review",
            module="access_reviews",
            table="access_reviews",
            url_pattern="/access-reviews?highlight={id}",
            title_column="status",
        )
    )
    register(
        ResolvableType(
            type="group_access_request",
            module="access_reviews",
            table="group_access_requests",
            url_pattern="/access-reviews?highlight={id}",
            title_column="reason",
        )
    )

    # Discovery / Sync / Cloud / IAM Integrations
    register(
        ResolvableType(
            type="discovery_job",
            module="discovery",
            table="discovery_jobs",
            url_pattern="/admin/integrations?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="sync_config",
            module="discovery",
            table="sync_configs",
            url_pattern="/admin/integrations?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="iam_provider",
            module="discovery",
            table="iam_providers",
            url_pattern="/admin/integrations?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="google_workspace_provider",
            module="discovery",
            table="google_workspace_providers",
            url_pattern="/admin/integrations?highlight={id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="cloud_account",
            module="discovery",
            table="cloud_accounts",
            url_pattern="/admin/integrations?highlight={id}",
            title_column="name",
        )
    )


# Initialize on module import
_init_registry()


def get_registry() -> dict[str, ResolvableType]:
    """Get the full registry as a read-only dict.

    Returns:
        Dictionary mapping type strings to ResolvableType instances
    """
    return dict(_REGISTRY)


def get_type(type_name: str) -> ResolvableType | None:
    """Look up a resolvable type by name.

    Args:
        type_name: Resource type identifier

    Returns:
        ResolvableType if found, None otherwise
    """
    return _REGISTRY.get(type_name)


def _extract_title(row: Any, resolvable: ResolvableType, resource_id: Any) -> str:
    """Extract a human-readable display title from a resolved row."""
    raw = getattr(row, resolvable.title_column, None)
    if raw is not None and str(raw).strip():
        return str(raw)
    for fallback_col in (
        "name",
        "title",
        "username",
        "email",
        "slug",
        "filename",
        "code",
    ):
        val = getattr(row, fallback_col, None)
        if val is not None and str(val).strip():
            return str(val)
    return str(resource_id)


def _parse_json_config(raw: Any) -> dict[str, Any]:
    """Safely coerce a JSON/dict config column value to a dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            return {}
    return {}


def _get_table_safe(db: Any, table_name: str) -> Any | None:
    """Safely fetch a table from a PyDAL/penguin-dal DB handle.

    ``penguin_dal`` raises ``TableNotFoundError`` (not ``AttributeError``) when
    accessing an uncreated/missing table, which bypasses ``hasattr()``.
    """
    try:
        return getattr(db, table_name)
    except Exception:
        return None


def _get_column_safe(table: Any, col_name: str) -> Any | None:
    """Safely fetch a column from a PyDAL/penguin-dal table handle."""
    try:
        return getattr(table, col_name)
    except Exception:
        return None


def _get_tenant_org_ids(db: Any, tenant_id: int) -> set[int]:
    """Return all organization IDs belonging to ``tenant_id``."""
    orgs = _get_table_safe(db, "organizations")
    if orgs is None:
        return set()
    rows = db(orgs.tenant_id == tenant_id).select(orgs.id)
    return {int(r.id) for r in rows if getattr(r, "id", None) is not None}


def _get_tenant_string_values(db: Any, tenant_id: int) -> list[str]:
    """Return allowed string tenant_id values for string-tenant tables (geo_*)."""
    values = [str(tenant_id)]
    tenants = _get_table_safe(db, "tenants")
    if tenants is not None:
        row = db(tenants.id == tenant_id).select(tenants.slug).first()
        slug = getattr(row, "slug", None) if row else None
        if slug and str(slug) not in values:
            values.append(str(slug))
    return values


def _vid_matches_tenant_prefix(village_id: str, tenant_id: int) -> bool:
    """Return True if ``village_id`` is well-formed and embeds ``tenant_id``."""
    if not is_valid_village_id(village_id):
        return False
    return parse_village_id(village_id).tenant_id == tenant_id


def resolve_by_village_id(
    db: Any, village_id: str, tenant_id: int | None = None
) -> dict[str, Any] | None:
    """Resolve a village_id to its resource type, ID, title, module, and table.

    Searches all registered types for a matching village_id, strictly scoped to
    ``tenant_id``. Supports:
    - Direct ``tenant_id`` integer columns
    - ``tenants`` self-resolution (``table.id == tenant_id``)
    - String ``tenant_id`` columns (``geo_regions``, ``geo_locations``)
    - Organization-scoped tables (``entities``, ``networking_resources``,
      ``secrets``, ``builtin_secrets``, ``crypto_keys``, ``cloud_accounts``,
      etc.) via ``organizations.tenant_id == tenant_id``
    - ``discovery_jobs`` via ``config_json["_organization_id"]`` in tenant orgs
    - Tenantless global tables via the server-minted ``village_id`` tenant prefix

    Fail-closed (regression: gh-237): when ``tenant_id is None``, immediately
    returns ``None`` without querying any table.

    Args:
        db: PyDAL database instance
        village_id: The village_id to resolve
        tenant_id: Tenant ID for scoping (required for security)

    Returns:
        Dict with ``type``, ``id``, ``table_name``, ``title``, ``module``,
        or ``None`` if not found or tenant mismatch.
    """
    if not village_id or not isinstance(village_id, str):
        return None

    # Fail-closed (regression: gh-237): no tenant_id means ownership of ANY row
    # cannot be verified, so never resolve one -- skip every table rather than
    # falling through to an unscoped query.
    if tenant_id is None:
        return None

    tenant_org_ids_cache: set[int] | None = None
    tenant_str_values_cache: list[str] | None = None

    def get_org_ids() -> set[int]:
        nonlocal tenant_org_ids_cache
        if tenant_org_ids_cache is None:
            tenant_org_ids_cache = _get_tenant_org_ids(db, tenant_id)
        return tenant_org_ids_cache

    def get_tenant_strs() -> list[str]:
        nonlocal tenant_str_values_cache
        if tenant_str_values_cache is None:
            tenant_str_values_cache = _get_tenant_string_values(db, tenant_id)
        return tenant_str_values_cache

    for type_name, resolvable in _REGISTRY.items():
        table = _get_table_safe(db, resolvable.table)
        if table is None:
            continue

        village_id_field = _get_column_safe(table, resolvable.village_id_column)
        if village_id_field is None:
            continue

        tenant_col = _get_column_safe(table, "tenant_id")
        org_col = _get_column_safe(table, "organization_id")
        row = None

        if type_name == "tenant":
            # Special case: tenants table — a tenant can only resolve itself
            row = (
                db((village_id_field == village_id) & (table.id == tenant_id))
                .select()
                .first()
            )
        elif resolvable.tenant_id_is_string and tenant_col is not None:
            allowed_strs = get_tenant_strs()
            row = (
                db(
                    (village_id_field == village_id)
                    & (tenant_col.belongs(allowed_strs))
                )
                .select()
                .first()
            )
        elif tenant_col is not None:
            # Table has explicit integer tenant_id column
            row = (
                db((village_id_field == village_id) & (tenant_col == tenant_id))
                .select()
                .first()
            )
            # Legacy rows on nullable-tenant_id tables (e.g. projects/milestones/webhooks)
            # may still link tenancy through organization_id
            if row is None and org_col is not None:
                org_ids = get_org_ids()
                if org_ids:
                    row = (
                        db(
                            (village_id_field == village_id)
                            & (tenant_col == None)  # noqa: E711
                            & (org_col.belongs(list(org_ids)))
                        )
                        .select()
                        .first()
                    )
        elif org_col is not None:
            # Org-scoped tables (entities, networking_resources, secrets,
            # builtin_secrets, crypto_keys, cloud_accounts, iam_providers, etc.)
            org_ids = get_org_ids()
            if org_ids:
                row = (
                    db(
                        (village_id_field == village_id)
                        & (org_col.belongs(list(org_ids)))
                    )
                    .select()
                    .first()
                )
        elif resolvable.table == "discovery_jobs":
            # discovery_jobs stores _organization_id inside config_json
            org_ids = get_org_ids()
            if org_ids:
                candidate = db(village_id_field == village_id).select().first()
                if candidate is not None:
                    cfg = _parse_json_config(getattr(candidate, "config_json", None))
                    job_org_id = cfg.get("_organization_id")
                    if job_org_id is not None:
                        try:
                            if int(job_org_id) in org_ids:
                                row = candidate
                        except (TypeError, ValueError):
                            row = None
        elif resolvable.table == "sync_configs":
            candidate = db(village_id_field == village_id).select().first()
            if candidate is not None:
                cfg = _parse_json_config(getattr(candidate, "config_json", None))
                cfg_org_id = cfg.get("elder_organization_id") or cfg.get(
                    "organization_id"
                )
                if cfg_org_id is not None:
                    try:
                        if int(cfg_org_id) in get_org_ids():
                            row = candidate
                    except (TypeError, ValueError):
                        row = None
                elif _vid_matches_tenant_prefix(village_id, tenant_id):
                    row = candidate
        elif resolvable.table in _VID_PREFIX_SCOPED_TABLES:
            # Global/tenantless tables: enforce strict tenant match via the
            # server-minted 8-hex-char tenant prefix embedded in village_id
            if _vid_matches_tenant_prefix(village_id, tenant_id):
                row = db(village_id_field == village_id).select().first()
        else:
            # Cannot safely scope — skip this table
            continue

        if row is not None:
            resource_id = getattr(row, resolvable.id_column)
            return {
                "type": type_name,
                "id": resource_id,
                "table_name": resolvable.table,
                "title": _extract_title(row, resolvable, resource_id),
                "module": resolvable.module,
            }

    return None


def resolve_ref(
    db: Any,
    module: str,
    type_name: str,
    resource_id: Any,
    tenant_id: int | None = None,
) -> dict[str, Any] | None:
    """Resolve a module:type:id reference to its resource row.

    Args:
        db: PyDAL database instance
        module: Module name
        type_name: Resource type
        resource_id: Resource ID value
        tenant_id: Tenant ID for scoping (required for security)

    Returns:
        Dict with id, title, type, module, or None if not found or tenant mismatch
    """
    resolvable = get_type(type_name)
    if not resolvable or resolvable.module != module:
        return None

    # Check if table exists
    table = _get_table_safe(db, resolvable.table)
    if table is None:
        return None

    id_field = _get_column_safe(table, resolvable.id_column)
    if id_field is None:
        return None

    tenant_col = _get_column_safe(table, "tenant_id")
    org_col = _get_column_safe(table, "organization_id")

    # Guard integer PK columns against non-numeric strings (avoids PG syntax error)
    query_id: Any = resource_id
    if resolvable.id_column == "id" and isinstance(resource_id, str):
        if not resource_id.isdigit():
            return None
        query_id = int(resource_id)

    row = None
    # Tenant scoping is mandatory for security
    if tenant_id is not None:
        if type_name == "tenant":
            row = db((id_field == query_id) & (table.id == tenant_id)).select().first()
        elif resolvable.tenant_id_is_string and tenant_col is not None:
            allowed_strs = _get_tenant_string_values(db, tenant_id)
            row = (
                db((id_field == query_id) & (tenant_col.belongs(allowed_strs)))
                .select()
                .first()
            )
        elif tenant_col is not None:
            row = (
                db((id_field == query_id) & (tenant_col == tenant_id)).select().first()
            )
        elif org_col is not None:
            org_ids = _get_tenant_org_ids(db, tenant_id)
            if not org_ids:
                return None
            row = (
                db((id_field == query_id) & (org_col.belongs(list(org_ids))))
                .select()
                .first()
            )
        elif resolvable.table == "discovery_jobs":
            org_ids = _get_tenant_org_ids(db, tenant_id)
            if not org_ids:
                return None
            candidate = db(id_field == query_id).select().first()
            if candidate is not None:
                cfg = _parse_json_config(getattr(candidate, "config_json", None))
                job_org_id = cfg.get("_organization_id")
                if job_org_id is not None:
                    try:
                        if int(job_org_id) in org_ids:
                            row = candidate
                    except (TypeError, ValueError):
                        row = None
        else:
            # Table has no tenant_id/organization_id — cannot safely scope by numeric id
            return None
    else:
        # No tenant_id provided — cannot safely scope unscopable tables
        if tenant_col is not None or type_name == "tenant":
            row = db(id_field == query_id).select().first()
        else:
            return None

    if not row:
        return None

    title = _extract_title(row, resolvable, resource_id)
    return {
        "id": resource_id,
        "title": title,
        "type": type_name,
        "module": module,
    }
