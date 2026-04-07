"""SCIM 2.0 Server implementation for user provisioning.

Implements SCIM 2.0 endpoints for user and group provisioning
from identity providers like Okta, Azure AD, etc.
"""

# flake8: noqa: E501


import datetime
import secrets
from typing import Optional

from flask import current_app


class SCIMService:
    """SCIM 2.0 Server implementation."""

    # SCIM Schema URIs
    USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
    GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"

    @staticmethod
    def create_scim_config(
        tenant_id: int,
        endpoint_url: Optional[str] = None,
    ) -> dict:
        """Create SCIM configuration for a tenant.

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
    def get_scim_config(tenant_id: int) -> Optional[dict]:
        """Get SCIM configuration for a tenant.

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
        """Validate SCIM bearer token.

        Args:
            tenant_id: Tenant ID
            token: Bearer token to validate

        Returns:
            True if valid, False otherwise
        """
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
    def create_user(tenant_id: int, scim_user: dict) -> dict:
        """Create a user from SCIM request.

        Args:
            tenant_id: Tenant ID
            scim_user: SCIM user resource

        Returns:
            SCIM user response
        """
        db = current_app.db
        # Extract user attributes from SCIM format
        email = None
        for email_obj in scim_user.get("emails", []):
            if email_obj.get("primary", False) or not email:
                email = email_obj.get("value")

        if not email:
            return {"error": "Email is required", "status": 400}

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
            return {"error": "User already exists", "status": 409}

        # Build name
        name_obj = scim_user.get("name", {})
        full_name = (
            scim_user.get("displayName")
            or f"{name_obj.get('givenName', '')} {name_obj.get('familyName', '')}".strip()
        )

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
            last_sync_at=datetime.datetime.now(datetime.timezone.utc)
        )
        db.commit()

        return SCIMService._user_to_scim(user_id, email.lower(), full_name, True)

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
            return {"error": "User not found", "status": 404}

        return SCIMService._user_to_scim(
            user.id, user.email, user.full_name, user.is_active
        )

    @staticmethod
    def update_user(tenant_id: int, user_id: int, scim_user: dict) -> dict:
        """Update a user from SCIM request.

        Args:
            tenant_id: Tenant ID
            user_id: Portal user ID
            scim_user: SCIM user resource

        Returns:
            Updated SCIM user response
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
            return {"error": "User not found", "status": 404}

        # Extract updates
        updates = {}

        # Email
        for email_obj in scim_user.get("emails", []):
            if email_obj.get("primary", False):
                updates["email"] = email_obj.get("value", "").lower()
                break

        # Name
        name_obj = scim_user.get("name", {})
        if "displayName" in scim_user or name_obj:
            updates["full_name"] = (
                scim_user.get("displayName")
                or f"{name_obj.get('givenName', '')} {name_obj.get('familyName', '')}".strip()
            )

        # Active status
        if "active" in scim_user:
            updates["is_active"] = scim_user["active"]

        if updates:
            db(db.portal_users.id == user_id).update(**updates)

        # Refresh user data
        user = db.portal_users[user_id]

        return SCIMService._user_to_scim(
            user.id, user.email, user.full_name, user.is_active
        )

    @staticmethod
    def delete_user(tenant_id: int, user_id: int) -> dict:
        """Delete a user (SCIM deprovision).

        Args:
            tenant_id: Tenant ID
            user_id: Portal user ID

        Returns:
            Success status
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
            return {"error": "User not found", "status": 404}

        # Soft delete - deactivate instead of delete
        db(db.portal_users.id == user_id).update(is_active=False)

        return {"status": 204}

    @staticmethod
    def list_users(
        tenant_id: int,
        start_index: int = 1,
        count: int = 100,
        filter_str: Optional[str] = None,
    ) -> dict:
        """List users in SCIM format.

        Args:
            tenant_id: Tenant ID
            start_index: Starting index (1-based)
            count: Number of results
            filter_str: Optional SCIM filter

        Returns:
            SCIM ListResponse
        """
        db = current_app.db
        query = db.portal_users.tenant_id == tenant_id

        # Simple filter parsing (basic support)
        if filter_str:
            if "userName eq" in filter_str:
                # Extract email from filter
                email = filter_str.split('"')[1] if '"' in filter_str else ""
                query &= db.portal_users.email == email.lower()

        users = db(query).select(limitby=(start_index - 1, start_index - 1 + count))
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
        user_id: int, email: str, full_name: Optional[str], is_active: bool
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

        Args:
            tenant_id: Tenant ID

        Returns:
            New bearer token
        """
        db = current_app.db
        config = db(db.scim_configurations.tenant_id == tenant_id).select().first()

        if not config:
            return {"error": "SCIM not configured for this tenant"}

        new_token = f"scim_{secrets.token_urlsafe(32)}"
        db(db.scim_configurations.tenant_id == tenant_id).update(bearer_token=new_token)

        return {"bearer_token": new_token}
