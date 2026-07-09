"""Resolvable type registry for cross-reference resolution.

Declarative single source of truth for all types that can be referenced via
village_id or module:type:id format. Generalizes the hardcoded lookup_village_id.py
by providing a registry-driven resolver.

Types can be registered at startup via register() for extensibility by modules.
"""

# flake8: noqa: E501

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional

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
    """

    type: str
    module: str
    table: str
    id_column: str = "id"
    village_id_column: str = "village_id"
    url_pattern: str = "/{type}/{id}"
    title_column: str = "name"


# Registry: all resolvable types (mutable, seeded at startup)
_REGISTRY: Dict[str, ResolvableType] = {}


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

    Extracts types from RESOURCE_URL_MAP in the legacy lookup_village_id.py
    and from actual database tables with village_id columns.
    """
    # Core tenant/org types
    register(
        ResolvableType(
            type="tenant",
            module="tenant",
            table="tenants",
            url_pattern="/tenants/{id}",
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

    # Infrastructure
    register(
        ResolvableType(
            type="entity",
            module="infrastructure",
            table="entities",
            url_pattern="/entities/{id}",
            title_column="name",
        )
    )

    # Identity
    register(
        ResolvableType(
            type="identity",
            module="identity",
            table="identities",
            url_pattern="/identities/{id}",
            title_column="email",
        )
    )

    # SBOM/Software
    register(
        ResolvableType(
            type="software",
            module="sbom",
            table="software",
            url_pattern="/software/{id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="service",
            module="sbom",
            table="services",
            url_pattern="/services/{id}",
            title_column="name",
        )
    )

    # IPAM
    register(
        ResolvableType(
            type="ipam_prefix",
            module="ipam",
            table="ipam_prefixes",
            url_pattern="/ipam/prefixes/{id}",
            title_column="name",
        )
    )
    register(
        ResolvableType(
            type="ipam_address",
            module="ipam",
            table="ipam_addresses",
            url_pattern="/ipam/addresses/{id}",
            title_column="address",
        )
    )
    register(
        ResolvableType(
            type="ipam_vlan",
            module="ipam",
            table="ipam_vlans",
            url_pattern="/ipam/vlans/{id}",
            title_column="vlan_name",
        )
    )

    # Issues
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
            url_pattern="/milestones/{id}",
            title_column="title",
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


# Initialize on module import
_init_registry()


def get_registry() -> Dict[str, ResolvableType]:
    """Get the full registry as a read-only dict.

    Returns:
        Dictionary mapping type strings to ResolvableType instances
    """
    return dict(_REGISTRY)


def get_type(type_name: str) -> Optional[ResolvableType]:
    """Look up a resolvable type by name.

    Args:
        type_name: Resource type identifier

    Returns:
        ResolvableType if found, None otherwise
    """
    return _REGISTRY.get(type_name)


def resolve_by_village_id(
    db: Any, village_id: str, tenant_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """Resolve a village_id to its resource type and ID.

    Searches all registered types for a matching village_id, scoped to tenant if provided.

    Args:
        db: PyDAL database instance
        village_id: The village_id to resolve
        tenant_id: Tenant ID for scoping (required for security)

    Returns:
        Dict with type, id, table_name, or None if not found or tenant mismatch
    """
    for type_name, resolvable in _REGISTRY.items():
        # Check if table exists in database
        if not hasattr(db, resolvable.table):
            continue

        table: Table = getattr(db, resolvable.table)

        # Check if table has village_id field
        if not hasattr(table, resolvable.village_id_column):
            continue

        # Build query with tenant scoping
        village_id_field = getattr(table, resolvable.village_id_column)

        # Tenant scoping is mandatory for security
        if tenant_id is not None:
            if hasattr(table, "tenant_id"):
                # Table has explicit tenant_id column
                row = (
                    db(
                        (village_id_field == village_id)
                        & (table.tenant_id == tenant_id)
                    )
                    .select()
                    .first()
                )
            elif type_name == "tenant":
                # Special case: tenants table — a tenant can only resolve itself
                row = (
                    db((village_id_field == village_id) & (table.id == tenant_id))
                    .select()
                    .first()
                )
            else:
                # Table has no tenant_id and is not the tenant table
                # Cannot safely scope — skip this table
                continue
        else:
            # No tenant_id provided — cannot safely scope unscopable tables
            if hasattr(table, "tenant_id") or type_name == "tenant":
                row = db(village_id_field == village_id).select().first()
            else:
                # Cannot scope without tenant_id and no column available
                continue

        if row:
            return {
                "type": type_name,
                "id": getattr(row, resolvable.id_column),
                "table_name": resolvable.table,
            }

    return None


def resolve_ref(
    db: Any,
    module: str,
    type_name: str,
    resource_id: Any,
    tenant_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
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
    if not hasattr(db, resolvable.table):
        return None

    table: Table = getattr(db, resolvable.table)
    id_field = getattr(table, resolvable.id_column)

    # Tenant scoping is mandatory for security
    if tenant_id is not None:
        if hasattr(table, "tenant_id"):
            # Table has explicit tenant_id column (organizations, identities, software, etc.)
            row = (
                db((id_field == resource_id) & (table.tenant_id == tenant_id))
                .select()
                .first()
            )
        elif type_name == "tenant":
            # Special case: tenants table — a tenant can only resolve itself
            row = (
                db((id_field == resource_id) & (table.id == tenant_id)).select().first()
            )
        else:
            # Table has no tenant_id and is not the tenant table
            # Cannot safely scope — return None to prevent cross-tenant leak
            return None
    else:
        # No tenant_id provided — cannot safely scope unscopable tables
        if hasattr(table, "tenant_id") or type_name == "tenant":
            row = db(id_field == resource_id).select().first()
        else:
            # Cannot scope without tenant_id and no column available
            return None

    if not row:
        return None

    title = getattr(row, resolvable.title_column, str(resource_id))
    return {
        "id": resource_id,
        "title": title,
        "type": type_name,
        "module": module,
    }
