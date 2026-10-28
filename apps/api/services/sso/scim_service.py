"""SCIM 2.0 Server implementation for user provisioning.

Implements SCIM 2.0 endpoints for user and group provisioning
from identity providers like Okta, Azure AD, etc.

Tenant isolation (regression: scim-cross-tenant): every read and every write
below carries the tenant predicate, including the ``UPDATE`` itself (not just
the preceding existence check), so a SCIM bearer token -- which is bound to one
tenant by ``validate_bearer_token`` -- can never touch another tenant's rows.
SCIM also never manages platform-privileged (``global_role``) accounts.
"""

# flake8: noqa: E501

import datetime
import secrets
from typing import Any

from quart import current_app

# Upper bound on one SCIM page; larger ``count`` values are clamped.
MAX_PAGE_SIZE = 200
_MAX_FULL_NAME_CHARS = 255
_MAX_EMAIL_CHARS = 255


def _error(message: str, status: int) -> dict[str, Any]:
    """Build the service-level error dict the SCIM routes render as SCIM errors."""
    return {"error": message, "status": status}


class SCIMService:
    """SCIM 2.0 Server implementation."""

    # SCIM Schema URIs
    USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
    GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"

    @staticmethod
    def tenant_exists(tenant_id: int) -> bool:
        """Return True if ``tenant_id`` is an existing tenant (used before creating a config).

        Args:
            tenant_id: Tenant ID

        Returns:
            True if a tenants row with that id exists
        """
        db = current_app.db
        return db(db.tenants.id == tenant_id).count() > 0

    @staticmethod
    def create_scim_config(
        tenant_id: int,
        endpoint_url: str | None = None,
    ) -> dict:
        """Create SCIM configuration for a tenant.

        The bearer token in the return value is the only time it is ever
        disclosed; ``get_scim_config`` never returns it.

        Args:
            tenant_id: Tenant ID
            endpoint_url: Optional custom endpoint URL

        Returns:
            SCIM configuration with bearer token
        """
        db = current_app.db
        # Generate secure bearer token
        bearer_token = f"scim_{secrets.token_urlsafe(32)}"

        config_id = db.scim_configurations.insert(
            tenant_id=tenant_id,
            endpoint_url=endpoint_url or f"/api/v1/scim/{tenant_id}",
            bearer_token=bearer_token,
            sync_groups=True,
            is_active=True,
        )
        db.commit()

        return {
            "id": config_id,
            "tenant_id": tenant_id,
            "bearer_token": bearer_token,
            "endpoint_url": endpoint_url or f"/api/v1/scim/{tenant_id}",
        }

    @staticmethod
    def get_scim_config(tenant_id: int) -> dict | None:
        """Get SCIM configuration for a tenant (never includes the bearer token).

        Args:
            tenant_id: Tenant ID

        Returns:
            SCIM configuration dict or None
        """
        db = current_app.db
        config = (
            db(
                (db.scim_configurations.tenant_id == tenant_id)
                & (db.scim_configurations.is_active == True)  # noqa: E712  # noqa: E712
            )
            .select()
            .first()
        )

        if not config:
            return None

        return {
            "id": config.id,
            "tenant_id": config.tenant_id,
            "endpoint_url": config.endpoint_url,
            "sync_groups": config.sync_groups,
            "last_sync_at": (
                config.last_sync_at.isoformat() if config.last_sync_at else None
            ),
        }

    @staticmethod
    def validate_bearer_token(tenant_id: int, token: str) -> bool:
        """Validate a SCIM bearer token for exactly one tenant.

        The lookup matches on tenant AND token, so a token issued for tenant X
        is never valid for tenant Y.

        Args:
            tenant_id: Tenant ID the request is addressed to
            token: Bearer token to validate

        Returns:
            True if valid, False otherwise
        """
        if not isinstance(token, str) or not token:
            return False
        db = current_app.db
        config = (
            db(
                (db.scim_configurations.tenant_id == tenant_id)
                & (db.scim_configurations.bearer_token == token)
                & (db.scim_configurations.is_active == True)  # noqa: E712
            )
            .select()
            .first()
        )

        return config is not None

    @staticmethod
    def _emails(scim_user: dict) -> list[dict] | None:
        """Return the ``emails`` list if it is a list of objects, else None."""
        emails = scim_user.get("emails", [])
        if emails is None:
            return []
        if not isinstance(emails, list) or not all(isinstance(e, dict) for e in emails):
            return None
        return emails

    @staticmethod
    def _full_name(scim_user: dict) -> tuple[bool, str | None]:
        """Extract the display name; returns ``(present, name)``; raises ValueError if malformed."""
        name_obj = scim_user.get("name")
        if name_obj is None:
            name_obj = {}
        if not isinstance(name_obj, dict):
            raise ValueError("name must be an object")
        display = scim_user.get("displayName")
        if display is not None and not isinstance(display, str):
            raise ValueError("displayName must be a string")
        given, family = name_obj.get("givenName", ""), name_obj.get("familyName", "")
        if not isinstance(given, str) or not isinstance(family, str):
            raise ValueError("name parts must be strings")
        present = "displayName" in scim_user or bool(name_obj)
        full_name = display or f"{given} {family}".strip()
        return present, full_name[:_MAX_FULL_NAME_CHARS]

    @staticmethod
    def create_user(tenant_id: int, scim_user: dict) -> dict:
        """Create a user from SCIM request.

        Args:
            tenant_id: Tenant ID
            scim_user: SCIM user resource

        Returns:
            SCIM user response
        """
        if not isinstance(scim_user, dict):
            return _error("SCIM User resource must be an object", 400)
        emails = SCIMService._emails(scim_user)
        if emails is None:
            return _error("emails must be a list of objects", 400)
        if not isinstance(scim_user.get("active", True), bool):
            return _error("active must be a boolean", 400)
        try:
            _present, full_name = SCIMService._full_name(scim_user)
        except ValueError as exc:
            return _error(str(exc), 400)

        db = current_app.db
        # Extract user attributes from SCIM format
        email = None
        for email_obj in emails:
            value = email_obj.get("value")
            if not isinstance(value, str) or not value.strip():
                continue
            if email_obj.get("primary", False) or not email:
                email = value.strip()

        if not email:
            return _error("Email is required", 400)
        if len(email) > _MAX_EMAIL_CHARS:
            return _error("Email is too long", 400)

        # Check if user already exists
        existing = (
            db(
                (db.portal_users.email == email.lower())
                & (db.portal_users.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if existing:
            return _error("User already exists", 409)

        # Create portal user
        user_id = db.portal_users.insert(
            tenant_id=tenant_id,
            email=email.lower(),
            password_hash=None,  # SCIM users authenticate via SSO
            full_name=full_name or None,
            tenant_role="reader",  # Default role
            is_active=scim_user.get("active", True),
            email_verified=True,  # SCIM validates email
        )
        db.commit()

        # Update SCIM sync timestamp
        db(db.scim_configurations.tenant_id == tenant_id).update(
            last_sync_at=datetime.datetime.now(datetime.UTC)
        )
        db.commit()

        return SCIMService._user_to_scim(
            user_id, email.lower(), full_name, scim_user.get("active", True)
        )

    @staticmethod
    def get_user(tenant_id: int, user_id: int) -> dict:
        """Get a user in SCIM format.

        Args:
            tenant_id: Tenant ID
            user_id: Portal user ID

        Returns:
            SCIM user resource
        """
        db = current_app.db
        user = (
            db(
                (db.portal_users.id == user_id)
                & (db.portal_users.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not user:
            return _error("User not found", 404)

        return SCIMService._user_to_scim(
            user.id, user.email, user.full_name, user.is_active
        )

    @staticmethod
    def update_user(tenant_id: int, user_id: int, scim_user: dict) -> dict:
        """Update a user from SCIM request.

        The write is scoped to ``(id, tenant_id)`` itself, and platform-
        privileged accounts (a stored ``global_role``) are refused (403) -- a
        tenant's SCIM token must not rewrite or deactivate a platform admin.

        Args:
            tenant_id: Tenant ID
            user_id: Portal user ID
            scim_user: SCIM user resource

        Returns:
            Updated SCIM user response
        """
        if not isinstance(scim_user, dict):
            return _error("SCIM User resource must be an object", 400)

        db = current_app.db
        scoped = (db.portal_users.id == user_id) & (
            db.portal_users.tenant_id == tenant_id
        )
        user = db(scoped).select().first()

        if not user:
            return _error("User not found", 404)
        if user.global_role:
            return _error(
                "Platform-privileged accounts cannot be managed through SCIM", 403
            )

        # Extract updates
        updates: dict[str, Any] = {}

        # Email
        emails = SCIMService._emails(scim_user)
        if emails is None:
            return _error("emails must be a list of objects", 400)
        for email_obj in emails:
            if email_obj.get("primary", False):
                value = email_obj.get("value")
                if not isinstance(value, str) or not value.strip():
                    return _error("email value must be a non-empty string", 400)
                if len(value.strip()) > _MAX_EMAIL_CHARS:
                    return _error("Email is too long", 400)
                updates["email"] = value.strip().lower()
                break

        # Name
        try:
            name_present, full_name = SCIMService._full_name(scim_user)
        except ValueError as exc:
            return _error(str(exc), 400)
        if name_present:
            updates["full_name"] = full_name

        # Active status
        if "active" in scim_user:
            if not isinstance(scim_user["active"], bool):
                return _error("active must be a boolean", 400)
            updates["is_active"] = scim_user["active"]

        if "email" in updates and updates["email"] != user.email:
            clash = db(
                (db.portal_users.email == updates["email"])
                & (db.portal_users.tenant_id == tenant_id)
                & (db.portal_users.id != user_id)
            ).count()
            if clash:
                return _error("User already exists", 409)

        if updates:
            db(scoped).update(**updates)
            db.commit()

        # Refresh user data (same tenant-scoped predicate as the write)
        user = db(scoped).select().first()

        return SCIMService._user_to_scim(
            user.id, user.email, user.full_name, user.is_active
        )

    @staticmethod
    def delete_user(tenant_id: int, user_id: int) -> dict:
        """Delete a user (SCIM deprovision).

        Soft delete only, scoped to ``(id, tenant_id)``; platform-privileged
        accounts are refused (403).

        Args:
            tenant_id: Tenant ID
            user_id: Portal user ID

        Returns:
            Success status
        """
        db = current_app.db
        scoped = (db.portal_users.id == user_id) & (
            db.portal_users.tenant_id == tenant_id
        )
        user = db(scoped).select().first()

        if not user:
            return _error("User not found", 404)
        if user.global_role:
            return _error(
                "Platform-privileged accounts cannot be managed through SCIM", 403
            )

        # Soft delete - deactivate instead of delete
        db(scoped).update(is_active=False)
        db.commit()

        return {"status": 204}

    @staticmethod
    def list_users(
        tenant_id: int,
        start_index: int = 1,
        count: int = 100,
        filter_str: str | None = None,
    ) -> dict:
        """List users in SCIM format.

        ``startIndex`` is clamped to >= 1 and ``count`` to ``0..MAX_PAGE_SIZE``
        (a negative offset used to 500, an unbounded count to a full-table read).

        Args:
            tenant_id: Tenant ID
            start_index: Starting index (1-based)
            count: Number of results
            filter_str: Optional SCIM filter

        Returns:
            SCIM ListResponse
        """
        start_index = max(1, start_index)
        count = min(max(0, count), MAX_PAGE_SIZE)
        db = current_app.db
        query = db.portal_users.tenant_id == tenant_id

        # Simple filter parsing (basic support)
        if filter_str:
            if "userName eq" in filter_str:
                # Extract email from filter
                email = filter_str.split('"')[1] if '"' in filter_str else ""
                query &= db.portal_users.email == email.lower()

        users = db(query).select(
            orderby=db.portal_users.id,
            limitby=(start_index - 1, start_index - 1 + count),
        )
        total = db(query).count()

        resources = [
            SCIMService._user_to_scim(u.id, u.email, u.full_name, u.is_active)
            for u in users
        ]

        return {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": total,
            "startIndex": start_index,
            "itemsPerPage": len(resources),
            "Resources": resources,
        }

    @staticmethod
    def _user_to_scim(
        user_id: int, email: str, full_name: str | None, is_active: bool
    ) -> dict:
        """Convert portal user to SCIM format.

        Args:
            user_id: User ID
            email: Email address
            full_name: Full name
            is_active: Active status

        Returns:
            SCIM user resource
        """
        # Parse name parts
        name_parts = (full_name or "").split(" ", 1)
        given_name = name_parts[0] if name_parts else ""
        family_name = name_parts[1] if len(name_parts) > 1 else ""

        return {
            "schemas": [SCIMService.USER_SCHEMA],
            "id": str(user_id),
            "userName": email,
            "name": {
                "givenName": given_name,
                "familyName": family_name,
                "formatted": full_name or "",
            },
            "displayName": full_name or email,
            "emails": [
                {
                    "value": email,
                    "primary": True,
                    "type": "work",
                }
            ],
            "active": is_active,
            "meta": {
                "resourceType": "User",
            },
        }

    @staticmethod
    def regenerate_token(tenant_id: int) -> dict:
        """Regenerate SCIM bearer token.

        The caller (route) must already have verified that ``tenant_id`` is the
        authenticated admin's own tenant (or that they are a global admin).
        The new token is returned once and never readable again.

        Args:
            tenant_id: Tenant ID

        Returns:
            New bearer token, or ``{"error": ...}`` if SCIM is not configured
        """
        db = current_app.db
        config = db(db.scim_configurations.tenant_id == tenant_id).select().first()

        if not config:
            return {"error": "SCIM not configured"}

        new_token = f"scim_{secrets.token_urlsafe(32)}"
        db(db.scim_configurations.tenant_id == tenant_id).update(bearer_token=new_token)
        db.commit()

        return {"bearer_token": new_token}
