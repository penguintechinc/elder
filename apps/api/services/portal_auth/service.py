"""Portal authentication service for enterprise users.

Handles portal user registration, login, MFA, and session management
with tenant context for multi-tenancy support.
"""

# flake8: noqa: E501

import datetime
import json
import math
import secrets
from typing import Any, Optional

import pyotp
import structlog
from quart import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from apps.api.common.licensing.enforce import check_limit
from apps.api.services.webhooks.user_events import (
    UserCreatedEvent,
    schedule_user_created_webhooks,
)
from apps.api.utils.village_id import mint_village_id

logger = structlog.get_logger(__name__)


def parse_tenant_settings(raw: Any) -> dict[str, Any]:
    """Normalize a tenant.settings column value into a plain dict.

    Handles None, dict, or JSON-encoded string values defensively.
    """
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            return {}
    return {}


DEFAULT_MFA_GRACE_PERIOD_DAYS = 7
DEFAULT_MFA_PROVIDER = "google_authenticator"


def _to_utc_datetime(val: Any) -> datetime.datetime | None:
    """Convert an ISO-8601 string or datetime into a timezone-aware UTC datetime."""
    if val is None:
        return None
    if isinstance(val, str):
        raw = val.strip()
        if not raw:
            return None
        try:
            dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    elif isinstance(val, datetime.datetime):
        dt = val
    else:
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.UTC)
    return dt.astimezone(datetime.UTC)


def get_tenant_mfa_policy(tenant_id: int | None, db: Any = None) -> dict[str, Any]:
    """Read the normalized MFA policy from a tenant's settings JSON.

    Args:
        tenant_id: Tenant ID to inspect.
        db: Optional PyDAL database instance (defaults to ``current_app.db``).

    Returns:
        Dict with ``enforced``, ``enforced_at``, ``grace_period_days``, and ``provider``.
    """
    default_policy: dict[str, Any] = {
        "enforced": False,
        "enforced_at": None,
        "grace_period_days": DEFAULT_MFA_GRACE_PERIOD_DAYS,
        "provider": DEFAULT_MFA_PROVIDER,
    }
    if not tenant_id:
        return default_policy
    database = db if db is not None else current_app.db
    tenant = database(database.tenants.id == tenant_id).select().first()
    if not tenant:
        return default_policy
    settings = parse_tenant_settings(getattr(tenant, "settings", None))
    raw_grace = settings.get("mfa_grace_period_days", DEFAULT_MFA_GRACE_PERIOD_DAYS)
    try:
        grace_days = int(raw_grace)
    except (TypeError, ValueError):
        grace_days = DEFAULT_MFA_GRACE_PERIOD_DAYS
    grace_days = max(0, min(90, grace_days))
    return {
        "enforced": bool(settings.get("mfa_enforced", False)),
        "enforced_at": settings.get("mfa_enforced_at"),
        "grace_period_days": grace_days,
        "provider": settings.get("mfa_provider") or DEFAULT_MFA_PROVIDER,
    }


def is_tenant_mfa_enforced(tenant_id: int | None, db: Any = None) -> bool:
    """Return True if the given tenant enforces MFA via its settings JSON.

    Args:
        tenant_id: Tenant ID to inspect.
        db: Optional PyDAL database instance (defaults to ``current_app.db``).

    Returns:
        bool: True when ``tenant.settings["mfa_enforced"]`` is truthy.
    """
    return bool(get_tenant_mfa_policy(tenant_id, db=db)["enforced"])


def user_has_mfa_enrolled(user: Any, db: Any = None) -> bool:
    """Return True if the user has TOTP MFA or an active passkey/WebAuthn credential.

    Encapsulates MFA enrollment checks so both Google Authenticator (TOTP) and
    future Passkey / WebAuthn credentials satisfy Enterprise MFA enforcement.
    """
    if not user:
        return False
    if bool(getattr(user, "mfa_secret", None)):
        return True
    if bool(getattr(user, "mfa_enabled", False)):
        return True

    user_id = getattr(user, "id", None)
    if not user_id:
        return False

    database = db
    if database is None:
        try:
            database = current_app.db
        except RuntimeError:
            return False

    tables = getattr(database, "tables", None)
    for table_name in ("passkeys", "webauthn_credentials"):
        if tables is not None and table_name not in tables:
            continue
        try:
            table = getattr(database, table_name, None)
        except (AttributeError, KeyError):
            continue
        if table is None:
            continue
        user_col = None
        for col_name in ("portal_user_id", "user_id"):
            try:
                user_col = getattr(table, col_name, None)
            except (AttributeError, KeyError):
                user_col = None
            if user_col is not None:
                break
        if user_col is None:
            continue
        query = user_col == user_id
        try:
            is_active_col = getattr(table, "is_active", None)
        except (AttributeError, KeyError):
            is_active_col = None
        if is_active_col is not None:
            query = query & (is_active_col == True)  # noqa: E712
        try:
            if database(query).count() > 0:
                return True
        except Exception:
            continue

    return False


def compute_user_mfa_status(
    tenant_id: int | None,
    mfa_enabled: bool,
    user_created_at: Any = None,
    *,
    db: Any = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Compute per-user MFA enforcement, grace period deadline, and lockout state.

    The grace period anchor is ``max(mfa_enforced_at, user.created_at)`` in UTC,
    falling back gracefully if either timestamp is absent.

    Args:
        tenant_id: User's tenant ID.
        mfa_enabled: Whether the user has enrolled MFA.
        user_created_at: User's creation timestamp (datetime or ISO string).
        db: Optional PyDAL database instance (defaults to ``current_app.db``).
        now: Optional current UTC timestamp override (for deterministic tests).

    Returns:
        Dict with ``mfa_enforced``, ``mfa_setup_required``,
        ``mfa_grace_expires_at``, ``mfa_grace_days_remaining``, and
        ``mfa_locked_out``.
    """
    policy = get_tenant_mfa_policy(tenant_id, db=db)
    enforced = policy["enforced"]
    setup_required = enforced and not bool(mfa_enabled)

    if not setup_required:
        return {
            "mfa_enforced": enforced,
            "mfa_setup_required": False,
            "mfa_grace_expires_at": None,
            "mfa_grace_days_remaining": None,
            "mfa_locked_out": False,
        }

    current_time = _to_utc_datetime(now) or datetime.datetime.now(datetime.UTC)
    enforced_dt = _to_utc_datetime(policy["enforced_at"])
    created_dt = _to_utc_datetime(user_created_at)

    if enforced_dt is not None and created_dt is not None:
        anchor = max(enforced_dt, created_dt)
    elif enforced_dt is not None:
        anchor = enforced_dt
    elif created_dt is not None:
        anchor = created_dt
    else:
        anchor = current_time

    grace_days = policy["grace_period_days"]
    grace_expires_at = anchor + datetime.timedelta(days=grace_days)
    remaining_seconds = (grace_expires_at - current_time).total_seconds()
    days_remaining = max(0, math.ceil(remaining_seconds / 86400))
    grace_expired = current_time >= grace_expires_at

    return {
        "mfa_enforced": True,
        "mfa_setup_required": True,
        "mfa_grace_expires_at": grace_expires_at.isoformat(),
        "mfa_grace_days_remaining": days_remaining,
        "mfa_locked_out": grace_expired,
    }


def _admin_kind(tenant_role: str | None, global_role: str | None) -> str | None:
    """Determine the license-enforcement kind for a portal user's role.

    Mirrors apps/api/common/licensing/counters.py's PortalUser branches (see
    its module docstring for the full admin heuristic): `global_role` in
    {"admin", "superadmin"} is a global admin; `tenant_role == "admin"` is a
    tenant admin; anything else (including "reader"/"maintainer") is an
    unlimited member.

    Args:
        tenant_role: Role within the tenant (admin/maintainer/reader).
        global_role: Optional global role (admin/superadmin/support).

    Returns:
        "global_admin", "tenant_admin", or None (unlimited member -- skip
        enforcement).
    """
    if global_role in ("admin", "superadmin"):
        return "global_admin"
    if tenant_role == "admin":
        return "tenant_admin"
    return None


class PortalAuthService:
    """Service for portal user authentication and management."""

    # Password requirements
    MIN_PASSWORD_LENGTH = 8
    MAX_LOGIN_ATTEMPTS = 5
    LOCKOUT_DURATION_MINUTES = 30

    @staticmethod
    async def create_portal_user(
        tenant_id: int,
        email: str,
        password: str,
        full_name: str | None = None,
        tenant_role: str = "reader",
        global_role: str | None = None,
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
            Created user dict or error dict. A license-limit block sets
            `status_code: 402` on the error dict (see `_admin_kind`);
            callers that previously assumed 400 on any `"error"` key must
            read `status_code` (defaults to 400) rather than hardcoding it.
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

        # License enforcement: block minting a new global/tenant admin once
        # the tier's admin limit is reached (observe-only unless the
        # elder.license-enforcement flag is ON -- see
        # apps/api/common/licensing/enforce.py). Non-admin members are
        # unlimited on every tier, so this is a no-op for the common
        # self-registration path (tenant_role="reader", global_role=None).
        kind = _admin_kind(tenant_role, global_role)
        if kind:
            blocked = await check_limit(kind, tenant_id)
            if blocked is not None:
                return {"error": "limit_reached", "status_code": 402, "limit": kind}

        # Hash password
        password_hash = generate_password_hash(password)

        # Create user
        user_id = current_app.db.portal_users.insert(
            tenant_id=tenant_id,
            village_id=mint_village_id(tenant_id),
            email=email.lower(),
            password_hash=password_hash,
            full_name=full_name,
            tenant_role=tenant_role,
            global_role=global_role,
            is_active=True,
            email_verified=False,
            failed_login_attempts=0,
            password_changed_at=datetime.datetime.now(datetime.UTC),
        )
        current_app.db.commit()

        try:
            schedule_user_created_webhooks(
                current_app.db,
                UserCreatedEvent(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    user_source="portal_user",
                    identity_type="portal_user",
                    auth_provider="local",
                    global_role=global_role,
                    tenant_role=tenant_role,
                    is_active=True,
                ),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "user_created_webhook_schedule_failed",
                user_id=user_id,
                error=str(exc)[:200],
            )

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
            if user.locked_until > datetime.datetime.now(datetime.UTC):
                return {"error": "Account locked. Try again later."}
            else:
                # Unlock account
                current_app.db(current_app.db.portal_users.id == user.id).update(
                    locked_until=None,
                    failed_login_attempts=0,
                    updated_at=datetime.datetime.now(datetime.UTC),
                )
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
                    datetime.UTC
                ) + datetime.timedelta(
                    minutes=PortalAuthService.LOCKOUT_DURATION_MINUTES
                )

            updates["updated_at"] = datetime.datetime.now(datetime.UTC)
            current_app.db(current_app.db.portal_users.id == user.id).update(**updates)
            current_app.db.commit()
            return {"error": "Invalid credentials"}

        # Reset failed attempts on successful login
        current_app.db(current_app.db.portal_users.id == user.id).update(
            failed_login_attempts=0,
            last_login_at=datetime.datetime.now(datetime.UTC),
            updated_at=datetime.datetime.now(datetime.UTC),
        )
        current_app.db.commit()

        # Check if MFA is required
        if user.mfa_secret:
            return {
                "mfa_required": True,
                "user_id": user.id,
                "tenant_id": tenant_id,
            }

        mfa_status = compute_user_mfa_status(
            user.tenant_id,
            mfa_enabled=user_has_mfa_enrolled(user),
            user_created_at=getattr(user, "created_at", None),
        )
        return {
            "id": user.id,
            "email": user.email,
            "tenant_id": user.tenant_id,
            "tenant_role": user.tenant_role,
            "global_role": user.global_role,
            "full_name": user.full_name,
            **mfa_status,
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
        current_app.db(current_app.db.portal_users.id == user.id).update(
            mfa_secret=secret,
            mfa_backup_codes=backup_codes,
            updated_at=datetime.datetime.now(datetime.UTC),
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
    def confirm_mfa(user_id: int, totp_code: str) -> dict:
        """Confirm MFA enrollment by validating a TOTP code against the user's secret.

        Args:
            user_id: Portal user ID
            totp_code: 6-digit TOTP code from Google Authenticator

        Returns:
            ``{"verified": True}`` on success, or an error dict with ``status_code``
        """
        user = current_app.db.portal_users[user_id]
        if not user:
            return {"error": "User not found", "status_code": 404}

        if not user.mfa_secret:
            return {
                "error": "MFA has not been initialized for this user",
                "status_code": 400,
            }

        totp = pyotp.TOTP(user.mfa_secret)
        if not totp.verify(str(totp_code).strip(), valid_window=1):
            return {"error": "Invalid verification code", "status_code": 400}

        return {"verified": True}

    @staticmethod
    def disable_mfa(user_id: int) -> dict:
        """Disable MFA for a user.

        Refuses with HTTP 403 if the user's tenant enforces MFA via its
        Enterprise security policy.

        Args:
            user_id: Portal user ID

        Returns:
            Success dict or error dict (with optional ``status_code``)
        """
        user = current_app.db.portal_users[user_id]
        if not user:
            return {"error": "User not found"}

        if is_tenant_mfa_enforced(user.tenant_id):
            return {
                "error": (
                    "MFA is enforced by your organization's Enterprise security "
                    "policy and cannot be disabled."
                ),
                "status_code": 403,
            }

        current_app.db(current_app.db.portal_users.id == user.id).update(
            mfa_secret=None,
            mfa_backup_codes=None,
            updated_at=datetime.datetime.now(datetime.UTC),
        )
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
        current_app.db(current_app.db.portal_users.id == user.id).update(
            password_hash=generate_password_hash(new_password),
            password_changed_at=datetime.datetime.now(datetime.UTC),
            updated_at=datetime.datetime.now(datetime.UTC),
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
            current_app.db(
                current_app.db.portal_user_org_assignments.id == existing.id
            ).update(role=role)
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
        from apps.api.auth.rbac import resolve_portal_effective_role, role_scopes

        effective_role = resolve_portal_effective_role(
            user.get("global_role"), user.get("tenant_role")
        )
        return {
            "sub": str(user["id"]),
            "email": user["email"],
            "tenant_id": user["tenant_id"],
            "tenant_role": user.get("tenant_role"),
            "global_role": user.get("global_role"),
            "roles": [effective_role],
            "scope": role_scopes(effective_role),
            "type": "portal_user",
        }
