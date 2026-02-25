"""Portal authentication service for enterprise users.

Handles portal user registration, login, MFA, and session management
with tenant context for multi-tenancy support.
"""

# flake8: noqa: E501


import datetime
import secrets
from typing import Optional

import pyotp
from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash


class PortalAuthService:
    """Service for portal user authentication and management."""

    # Password requirements
    MIN_PASSWORD_LENGTH = 8
    MAX_LOGIN_ATTEMPTS = 5
    LOCKOUT_DURATION_MINUTES = 30

    @staticmethod
    def create_portal_user(
        tenant_id: int,
        email: str,
        password: str,
        full_name: Optional[str] = None,
        tenant_role: str = "reader",
        global_role: Optional[str] = None,
    ) -> dict:
        """Create a new portal user.

        Args:
            tenant_id: Tenant this user belongs to
            email: User email address
            password: Plain text password (will be hashed)
            full_name: Optional full name
            tenant_role: Role within tenant (admin/maintainer/reader)
            global_role: Optional global role (admin/support)

        Returns:
            Created user dict or error dict
        """
        # Validate password
        if len(password) < PortalAuthService.MIN_PASSWORD_LENGTH:
            return {
                "error": f"Password must be at least {PortalAuthService.MIN_PASSWORD_LENGTH} characters"
            }

        # Check if email already exists in this tenant
        existing = (
            current_app.db(
                (current_app.db.portal_users.email == email.lower())
                & (current_app.db.portal_users.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if existing:
            return {"error": "Email already registered in this tenant"}

        # Hash password
        password_hash = generate_password_hash(password)

        # Create user
        user_id = current_app.db.portal_users.insert(
            tenant_id=tenant_id,
            email=email.lower(),
            password_hash=password_hash,
            full_name=full_name,
            tenant_role=tenant_role,
            global_role=global_role,
            is_active=True,
            email_verified=False,
            failed_login_attempts=0,
            password_changed_at=datetime.datetime.now(datetime.timezone.utc),
        )
        current_app.db.commit()

        return {
            "id": user_id,
            "email": email.lower(),
            "tenant_id": tenant_id,
            "tenant_role": tenant_role,
            "global_role": global_role,
        }

    @staticmethod
    def authenticate(tenant_id: int, email: str, password: str) -> dict:
        """Authenticate a portal user.

        Args:
            tenant_id: Tenant context
            email: User email
            password: Plain text password

        Returns:
            User dict on success, error dict on failure
        """
        user = (
            current_app.db(
                (current_app.db.portal_users.email == email.lower())
                & (current_app.db.portal_users.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not user:
            return {"error": "Invalid credentials"}

        # Check if account is locked
        if user.locked_until:
            if user.locked_until > datetime.datetime.now(datetime.timezone.utc):
                return {"error": "Account locked. Try again later."}
            else:
                # Unlock account
                user.update_record(locked_until=None, failed_login_attempts=0)
                current_app.db.commit()

        # Check if account is active
        if not user.is_active:
            return {"error": "Account is deactivated"}

        # Verify password
        if not check_password_hash(user.password_hash, password):
            # Increment failed attempts
            attempts = (user.failed_login_attempts or 0) + 1
            updates = {"failed_login_attempts": attempts}

            if attempts >= PortalAuthService.MAX_LOGIN_ATTEMPTS:
                updates["locked_until"] = datetime.datetime.now(
                    datetime.timezone.utc
                ) + datetime.timedelta(
                    minutes=PortalAuthService.LOCKOUT_DURATION_MINUTES
                )

            user.update_record(**updates)
            current_app.db.commit()
            return {"error": "Invalid credentials"}

        # Reset failed attempts on successful login
        user.update_record(
            failed_login_attempts=0,
            last_login_at=datetime.datetime.now(datetime.timezone.utc),
        )
        current_app.db.commit()

        # Check if MFA is required
        if user.mfa_secret:
            return {
                "mfa_required": True,
                "user_id": user.id,
                "tenant_id": tenant_id,
            }

        return {
            "id": user.id,
            "email": user.email,
            "tenant_id": user.tenant_id,
            "tenant_role": user.tenant_role,
            "global_role": user.global_role,
            "full_name": user.full_name,
        }

    @staticmethod
    def verify_mfa(user_id: int, totp_code: str) -> dict:
        """Verify MFA TOTP code.

        Args:
            user_id: Portal user ID
            totp_code: 6-digit TOTP code

        Returns:
            User dict on success, error dict on failure
        """
        user = current_app.db.portal_users[user_id]
        if not user:
            return {"error": "User not found"}

        if not user.mfa_secret:
            return {"error": "MFA not enabled for this user"}

        totp = pyotp.TOTP(user.mfa_secret)
        if not totp.verify(totp_code):
            return {"error": "Invalid MFA code"}

        return {
            "id": user.id,
            "email": user.email,
            "tenant_id": user.tenant_id,
            "tenant_role": user.tenant_role,
            "global_role": user.global_role,
            "full_name": user.full_name,
        }

    @staticmethod
    def enable_mfa(user_id: int) -> dict:
        """Enable MFA for a user and generate secret.

        Args:
            user_id: Portal user ID

        Returns:
            MFA setup info (secret, provisioning URI)
        """
        user = current_app.db.portal_users[user_id]
        if not user:
            return {"error": "User not found"}

        # Generate new TOTP secret
        secret = pyotp.random_base32()

        # Generate backup codes
        backup_codes = [secrets.token_hex(4) for _ in range(10)]

        # Save secret (don't enable until verified)
        user.update_record(
            mfa_secret=secret,
            mfa_backup_codes=backup_codes,
        )
        current_app.db.commit()

        # Generate provisioning URI for authenticator apps
        totp = pyotp.TOTP(secret)
        provisioning_uri = totp.provisioning_uri(name=user.email, issuer_name="Elder")

        return {
            "secret": secret,
            "provisioning_uri": provisioning_uri,
            "backup_codes": backup_codes,
        }

    @staticmethod
    def disable_mfa(user_id: int) -> dict:
        """Disable MFA for a user.

        Args:
            user_id: Portal user ID

        Returns:
            Success dict
        """
        user = current_app.db.portal_users[user_id]
        if not user:
            return {"error": "User not found"}

        user.update_record(mfa_secret=None, mfa_backup_codes=None)
        current_app.db.commit()

        return {"success": True}

    @staticmethod
    def change_password(user_id: int, current_password: str, new_password: str) -> dict:
        """Change user password.

        Args:
            user_id: Portal user ID
            current_password: Current password for verification
            new_password: New password

        Returns:
            Success dict or error dict
        """
        user = current_app.db.portal_users[user_id]
        if not user:
            return {"error": "User not found"}

        # Verify current password
        if not check_password_hash(user.password_hash, current_password):
            return {"error": "Current password is incorrect"}

        # Validate new password
        if len(new_password) < PortalAuthService.MIN_PASSWORD_LENGTH:
            return {
                "error": f"Password must be at least {PortalAuthService.MIN_PASSWORD_LENGTH} characters"
            }

        # Update password
        user.update_record(
            password_hash=generate_password_hash(new_password),
            password_changed_at=datetime.datetime.now(datetime.timezone.utc),
        )
        current_app.db.commit()

        return {"success": True}

    @staticmethod
    def reset_password(email: str, tenant_id: int) -> dict:
        """Initiate password reset (generates reset token).

        Args:
            email: User email
            tenant_id: Tenant context

        Returns:
            Reset token (in production, send via email)
        """
        user = (
            current_app.db(
                (current_app.db.portal_users.email == email.lower())
                & (current_app.db.portal_users.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not user:
            # Don't reveal if user exists
            return {
                "success": True,
                "message": "If the email exists, a reset link will be sent",
            }

        # Generate reset token
        reset_token = secrets.token_urlsafe(32)

        # In production, store token hash and send email
        # For now, return token directly
        return {
            "success": True,
            "reset_token": reset_token,
            "user_id": user.id,
        }

    @staticmethod
    def assign_org_role(portal_user_id: int, organization_id: int, role: str) -> dict:
        """Assign a portal user to an organization with a role.

        Args:
            portal_user_id: Portal user ID
            organization_id: Organization ID
            role: Role (admin/maintainer/reader)

        Returns:
            Assignment dict or error
        """
        # Check if assignment exists
        existing = (
            current_app.db(
                (
                    current_app.db.portal_user_org_assignments.portal_user_id
                    == portal_user_id
                )
                & (
                    current_app.db.portal_user_org_assignments.organization_id
                    == organization_id
                )
            )
            .select()
            .first()
        )

        if existing:
            # Update existing assignment
            existing.update_record(role=role)
            current_app.db.commit()
            return {"id": existing.id, "role": role, "updated": True}

        # Create new assignment
        assignment_id = current_app.db.portal_user_org_assignments.insert(
            portal_user_id=portal_user_id,
            organization_id=organization_id,
            role=role,
        )
        current_app.db.commit()

        return {"id": assignment_id, "role": role, "created": True}

    @staticmethod
    def get_user_permissions(portal_user_id: int) -> dict:
        """Get all permissions for a portal user.

        Args:
            portal_user_id: Portal user ID

        Returns:
            Dict with global_role, tenant_role, and org_roles
        """
        user = current_app.db.portal_users[portal_user_id]
        if not user:
            return {"error": "User not found"}

        # Get org assignments
        org_assignments = current_app.db(
            current_app.db.portal_user_org_assignments.portal_user_id == portal_user_id
        ).select()

        org_roles = {a.organization_id: a.role for a in org_assignments}

        return {
            "user_id": user.id,
            "tenant_id": user.tenant_id,
            "global_role": user.global_role,
            "tenant_role": user.tenant_role,
            "org_roles": org_roles,
        }

    @staticmethod
    def generate_jwt_claims(user: dict) -> dict:
        """Generate JWT claims for a portal user.

        Args:
            user: User dict from authentication

        Returns:
            JWT claims dict
        """
        return {
            "sub": str(user["id"]),
            "email": user["email"],
            "tenant_id": user["tenant_id"],
            "tenant_role": user.get("tenant_role"),
            "global_role": user.get("global_role"),
            "type": "portal_user",
        }
