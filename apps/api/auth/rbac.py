"""Elder RBAC — roles, scopes, and permission enforcement via penguin-aaa."""

from typing import Any

from penguin_aaa.authz.rbac import RBACEnforcer, Role

# ---------------------------------------------------------------------------
# Scope catalogue — resource:action format
# ---------------------------------------------------------------------------

_OBSERVER_SCOPES = [
    "access_reviews:read",
    "ai:read",
    "audit:read",
    "certificates:read",
    "data-stores:read",
    "dependencies:read",
    "diagrams:read",
    "discovery:read",
    "documents:read",
    "entities:read",
    "flows:read",
    "helpdesk:read",
    "iam:read",
    "infrastructure:read",
    "ipam:read",
    "issues:read",
    "keys:read",
    "networking:read",
    "organizations:read",
    "pages:read",
    "projects:read",
    "sbom:read",
    "secrets:read",
    "services_oncall:read",
    "software:read",
    "streams:read",
    "vulnerabilities:read",
    "webhooks:read",
    "webhooks_alerting:read",
]

_EDITOR_SCOPES = _OBSERVER_SCOPES + [
    "access_reviews:write",
    "ai:write",
    "certificates:write",
    "data-stores:write",
    "dependencies:write",
    "diagrams:write",
    "discovery:write",
    "documents:write",
    "entities:write",
    "flows:approve",
    "flows:execute",
    "flows:write",
    "helpdesk:write",
    "iam:write",
    "infrastructure:write",
    "ipam:write",
    "issues:write",
    "keys:write",
    "networking:write",
    "organizations:write",
    "pages:write",
    "projects:write",
    "sbom:write",
    "secrets:write",
    "services_oncall:write",
    "software:write",
    "streams:execute",
    "streams:write",
    "vulnerabilities:write",
    "webhooks:write",
    "webhooks_alerting:write",
    "users:read",
]

_ADMIN_SCOPES = _EDITOR_SCOPES + [
    "access_reviews:admin",
    "ai:admin",
    "audit:write",
    "backup:read",
    "config:write",
    "diagrams:admin",
    "discovery:admin",
    "documents:admin",
    "entities:delete",
    "flows:admin",
    "helpdesk:admin",
    "infrastructure:admin",
    "ipam:admin",
    "issues:admin",
    "issues:delete",
    "keys:delete",
    "logs:read",
    "organizations:delete",
    "pages:admin",
    "sbom:admin",
    "secrets:admin",
    "secrets:delete",
    "services_oncall:admin",
    "settings:read",
    "settings:write",
    "streams:admin",
    "users:write",
    "users:delete",
    "webhooks_alerting:admin",
]

# ---------------------------------------------------------------------------
# Enforcer singleton — imported by decorators and permission helpers
# ---------------------------------------------------------------------------

enforcer = RBACEnforcer()
enforcer.register(Role("observer", _OBSERVER_SCOPES))
enforcer.register(Role("editor", _EDITOR_SCOPES))
enforcer.register(Role("admin", _ADMIN_SCOPES))

# ---------------------------------------------------------------------------
# Legacy permission name → scope mapping
# Used by _check_user_permission to bridge old-style permission strings
# ---------------------------------------------------------------------------

PERMISSION_SCOPE_MAP: dict[str, str] = {
    "manage_users": "users:write",
    "view_users": "users:read",
    "create_entity": "entities:write",
    "edit_entity": "entities:write",
    "delete_entity": "entities:delete",
    "view_entity": "entities:read",
    "create_organization": "organizations:write",
    "edit_organization": "organizations:write",
    "delete_organization": "organizations:delete",
    "view_organization": "organizations:read",
    "manage_secrets": "secrets:write",
    "view_secrets": "secrets:read",
    "manage_keys": "keys:write",
    "view_keys": "keys:read",
    "manage_discovery": "discovery:write",
    "view_discovery": "discovery:read",
    "view_audit": "audit:read",
    "manage_iam": "iam:write",
    "view_iam": "iam:read",
    "manage_webhooks": "webhooks:write",
    "edit_config": "config:write",
}


def check_permission(user, permission_name: str) -> bool:
    """Return True if user's role grants the named permission.

    Superusers always pass. For everyone else, the permission name is mapped
    to a scope via PERMISSION_SCOPE_MAP and checked against the enforcer.
    Unknown permissions default to False.
    """
    if getattr(user, "is_superuser", False):
        return True
    scope = PERMISSION_SCOPE_MAP.get(permission_name)
    if not scope:
        return False
    role = getattr(user, "portal_role", "observer") or "observer"
    return enforcer.has_scope(role, scope)


def check_org_permission(user, permission_name: str, org_id: int) -> bool:
    """Return True if user's role grants the permission in any org context.

    Elder does not yet have per-org role rows for identities (only for
    portal users via resource_roles). Until that's wired, fall back to the
    global role check so behaviour is consistent with check_permission.
    """
    return check_permission(user, permission_name)


_ROLE_ALIASES: dict[str, str] = {
    "observer": "observer",
    "reader": "observer",
    "viewer": "observer",
    "support": "observer",
    "editor": "editor",
    "maintainer": "editor",
    "admin": "admin",
    "superadmin": "admin",
}


def resolve_portal_effective_role(global_role: Any, tenant_role: Any) -> str:
    """Compute a portal user's effective role from global_role / tenant_role."""
    raw_global = global_role.strip() if isinstance(global_role, str) else ""
    raw_tenant = tenant_role.strip() if isinstance(tenant_role, str) else ""
    effective = (raw_global or raw_tenant or "observer").lower()
    if effective == "superadmin":
        return "admin"
    return effective


def role_scopes(portal_role: str) -> list[str]:
    """Return the scope list for a portal_role string, defaulting to observer."""
    normalized = portal_role.strip().lower() if isinstance(portal_role, str) else ""
    role = _ROLE_ALIASES.get(normalized, "observer")
    try:
        return enforcer.scopes_for_role(role)
    except KeyError:
        return []
