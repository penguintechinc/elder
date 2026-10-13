"""References service layer for cross-reference management.

Provides CRUD and query operations on the references table using penguin-dal.
All operations are tenant-scoped.
"""

# flake8: noqa: E501

from __future__ import annotations

from datetime import UTC, datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from pydal.objects import Row


def create_reference(
    db: Any,
    tenant_id: int,
    source_module: str,
    source_type: str,
    source_id: str,
    target_module: str,
    target_type: str,
    target_id: str,
    ref_type: str = "link",
    context: dict[str, Any] | None = None,
    created_by: str | None = None,
) -> Row | None:
    """Create a new reference record.

    Args:
        db: PyDAL database instance
        tenant_id: Tenant ID for scoping
        source_module: Source resource module
        source_type: Source resource type
        source_id: Source resource ID
        target_module: Target resource module
        target_type: Target resource type
        target_id: Target resource ID
        ref_type: Reference type (default: "link")
        context: Optional JSON context
        created_by: User UUID who created the reference

    Returns:
        Inserted row or None on failure

    Raises:
        Exception: On database error (caller should handle)
    """
    row_id = db.references.insert(
        tenant_id=tenant_id,
        source_module=source_module,
        source_type=source_type,
        source_id=source_id,
        target_module=target_module,
        target_type=target_type,
        target_id=target_id,
        ref_type=ref_type,
        context=context,
        created_by=created_by,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    # Retrieve the inserted row by ID
    row = db(db.references.id == row_id).select().first()
    return row


def delete_references_for_source(
    db: Any,
    source_module: str,
    source_type: str,
    source_id: str,
) -> int:
    """Delete all references originating from a source.

    Used for rebuild-on-save: when a resource is modified, delete and
    recreate all its outbound references.

    Args:
        db: PyDAL database instance
        source_module: Source resource module
        source_type: Source resource type
        source_id: Source resource ID

    Returns:
        Number of deleted references

    Raises:
        Exception: On database error (caller should handle)
    """
    query = db(
        (db.references.source_module == source_module)
        & (db.references.source_type == source_type)
        & (db.references.source_id == source_id)
    )
    count = len(query.select())
    query.delete()
    return count


def backlinks_for(
    db: Any,
    target_module: str,
    target_type: str,
    target_id: str,
    tenant_id: int,
) -> list[Row]:
    """Get all references pointing to a target resource.

    Tenant-scoped: only returns references within the target's tenant.
    Indexed reverse query on (target_module, target_type, target_id, tenant_id).

    Args:
        db: PyDAL database instance
        target_module: Target resource module
        target_type: Target resource type
        target_id: Target resource ID
        tenant_id: Tenant ID for scoping

    Returns:
        List of Reference rows pointing to the target
    """
    query = db(
        (db.references.target_module == target_module)
        & (db.references.target_type == target_type)
        & (db.references.target_id == target_id)
        & (db.references.tenant_id == tenant_id)
    )
    return query.select(orderby=~db.references.created_at)


def outbound_for(
    db: Any,
    source_module: str,
    source_type: str,
    source_id: str,
    tenant_id: int,
) -> list[Row]:
    """Get all references originating from a source resource.

    Tenant-scoped: only returns references within the source's tenant.

    Args:
        db: PyDAL database instance
        source_module: Source resource module
        source_type: Source resource type
        source_id: Source resource ID
        tenant_id: Tenant ID for scoping

    Returns:
        List of Reference rows from the source
    """
    query = db(
        (db.references.source_module == source_module)
        & (db.references.source_type == source_type)
        & (db.references.source_id == source_id)
        & (db.references.tenant_id == tenant_id)
    )
    return query.select(orderby=~db.references.created_at)
