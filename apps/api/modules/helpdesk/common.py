"""Shared helpers for helpdesk routes."""

from __future__ import annotations

from typing import Any


def identity_in_tenant(db: Any, identity_id: int | None, tenant_id: int) -> bool:
    """Return True if identity_id is unset or belongs to tenant_id.

    Guards against cross-tenant IDOR whenever an identity_id is accepted from a
    request body (ticket requester/assignee, team member, contact link, ...).
    A None id is treated as valid (the reference is simply absent).
    """
    if identity_id is None:
        return True
    return (
        db((db.identities.id == identity_id) & (db.identities.tenant_id == tenant_id))
        .select()
        .first()
        is not None
    )
