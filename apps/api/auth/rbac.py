"""Elder RBAC — roles, scopes, and permission enforcement via penguin-aaa."""

from penguin_aaa.authz.rbac import RBACEnforcer, Role

# ---------------------------------------------------------------------------
# Scope catalogue — resource:action format
# ---------------------------------------------------------------------------

_OBSERVER_SCOPES = [
    "audit:read",
    "certificates:read",
    "data-stores:read",
    "dependencies:read",
    "discovery:read",
    "entities:read",
    "iam:read",
    "issues:read",
    "keys:read",
    "networking:read",
    "organizations:read",
    "projects:read",
    "sbom:read",
    "secrets:read",
    "software:read",
    "vulnerabilities:read",
    "webhooks:read",
]

_EDITOR_SCOPES = _OBSERVER_SCOPES + [
    "certificates:write",
    "data-stores:write",
    "dependencies:write",
    "discovery:write",
    "entities:write",
    "iam:write",
    "issues:write",
    "keys:write",
    "networking:write",
    "organizations:write",
    "projects:write",
    "sbom:write",
    "secrets:write",
    "software:write",
    "vulnerabilities:write",
    "webhooks:write",
    "users:read",
]

_ADMIN_SCOPES = _EDITOR_SCOPES + [
    "audit:write",
    "config:write",
    "entities:delete",
    "issues:delete",
    "keys:delete",
    "organizations:delete",
    "secrets:delete",
    "users:write",
    "users:delete",
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


def role_scopes(portal_role: str) -> list[str]:
    """Return the scope list for a portal_role string, defaulting to observer."""
    role = portal_role if portal_role in ("observer", "editor", "admin") else "observer"
    try:
        return enforcer.scopes_for_role(role)
    except KeyError:
        return []
