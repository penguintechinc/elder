"""Email delivery helpers for email verification and admin onboarding invites.

Never logs raw tokens or email PII.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from typing import Any

import structlog
from werkzeug.security import generate_password_hash

from apps.api.services.portal_auth import is_tenant_mfa_enforced
from apps.api.utils.email import (
    EmailDeliveryError,
    EmailNotConfiguredError,
    is_smtp_configured,
    send_email,
)
from apps.api.utils.onboarding_tokens import (
    ADMIN_INVITE_TTL_SECONDS,
    EMAIL_VERIFY_TTL_SECONDS,
    PURPOSE_ADMIN_INVITE,
    PURPOSE_EMAIL_VERIFY,
    issue_onboarding_token,
)
from apps.api.utils.village_id import mint_village_id

logger = structlog.get_logger(__name__)


def _site_url() -> str:
    return os.environ.get("SITE_URL", "").strip().rstrip("/")


async def send_verification_email(
    *,
    to_email: str,
    token: str,
    tenant_name: str | None = None,
) -> dict[str, Any]:
    """Send a self-signup email verification link over SMTP.

    Raises ``EmailNotConfiguredError`` or ``EmailDeliveryError`` on failure.
    """
    site_url = _site_url()
    verify_link = (
        f"{site_url}/verify-email?token={token}"
        if site_url
        else f"Verification token: {token}"
    )
    org_line = f" for {tenant_name}" if tenant_name else ""
    body_text = (
        f"Welcome to Elder! Please verify your email address to activate your account{org_line}.\n\n"
        f"Verify your email:\n{verify_link}\n\n"
        "This link expires in 24 hours and can only be used once.\n"
        "If you did not create this account, you can safely ignore this email."
    )
    return await send_email(
        to_addresses=[to_email],
        subject="Verify your Elder email address",
        body_text=body_text,
    )


async def send_admin_invite_email(
    *,
    to_email: str,
    token: str,
    full_name: str | None = None,
    tenant_name: str | None = None,
    mfa_enforced: bool = False,
) -> dict[str, Any]:
    """Send a one-time onboarding invitation email to an admin-created user.

    Clearly notes whether multi-factor authentication (MFA) setup is required
    by tenant policy or optional during onboarding.
    """
    site_url = _site_url()
    setup_link = (
        f"{site_url}/setup-account?token={token}"
        if site_url
        else f"Account setup token: {token}"
    )
    greeting = f"Hello {full_name}," if full_name else "Hello,"
    org_line = f" to {tenant_name} on Elder" if tenant_name else " to Elder"
    if mfa_enforced:
        mfa_note = (
            "Multi-factor authentication (MFA) is REQUIRED by your organization's "
            "security policy. After setting your password, you will be guided through "
            "enrolling your authenticator app before signing in."
        )
    else:
        mfa_note = (
            "Multi-factor authentication (MFA) is optional for your organization, "
            "and you will have the option to enable it during account setup."
        )

    body_text = (
        f"{greeting}\n\n"
        f"An administrator has invited you{org_line}.\n\n"
        f"Complete your one-time account setup (set your password):\n{setup_link}\n\n"
        f"{mfa_note}\n\n"
        "This invitation link expires in 72 hours and can only be used once.\n"
        "If you were not expecting this invitation, please contact your administrator."
    )
    return await send_email(
        to_addresses=[to_email],
        subject="You're invited to Elder — Complete your account setup",
        body_text=body_text,
    )


async def ensure_portal_user_and_send_invite(
    *,
    db: Any,
    tenant_id: int,
    email: str,
    full_name: str | None = None,
    tenant_role: str = "reader",
    global_role: str | None = None,
) -> dict[str, Any]:
    """Ensure a ``portal_users`` row exists for ``(tenant_id, email)`` and send an invite.

    Used by admin user/identity creation endpoints (``POST /api/v1/users`` and
    ``POST /api/v1/identities``) when ``send_invite`` is requested. Never logs
    raw tokens or email PII.
    """
    normalized_email = email.strip().lower()
    existing = (
        db(
            (db.portal_users.tenant_id == tenant_id)
            & (db.portal_users.email == normalized_email)
        )
        .select()
        .first()
    )
    if existing is not None:
        portal_user_id = existing.id
    else:
        now = datetime.now(UTC)
        placeholder_password = secrets.token_urlsafe(48)
        portal_user_id = db.portal_users.insert(
            tenant_id=tenant_id,
            village_id=mint_village_id(tenant_id),
            email=normalized_email,
            password_hash=generate_password_hash(placeholder_password),
            full_name=full_name,
            tenant_role=tenant_role,
            global_role=global_role,
            is_active=True,
            email_verified=False,
            failed_login_attempts=0,
            password_changed_at=now,
        )
        db.commit()

    tenant = db(db.tenants.id == tenant_id).select().first()
    tenant_name = getattr(tenant, "name", None) if tenant else None
    mfa_enforced = is_tenant_mfa_enforced(tenant_id, db=db)

    token = issue_onboarding_token(
        PURPOSE_ADMIN_INVITE,
        {
            "user_id": int(portal_user_id),
            "tenant_id": int(tenant_id),
        },
        ttl_seconds=ADMIN_INVITE_TTL_SECONDS,
    )

    invite_sent = False
    if is_smtp_configured():
        try:
            await send_admin_invite_email(
                to_email=normalized_email,
                token=token,
                full_name=full_name,
                tenant_name=tenant_name,
                mfa_enforced=mfa_enforced,
            )
            invite_sent = True
        except (EmailNotConfiguredError, EmailDeliveryError, ValueError) as exc:
            logger.warning(
                "admin_invite_email_failed",
                tenant_id=tenant_id,
                portal_user_id=portal_user_id,
                error_type=type(exc).__name__,
            )

    return {
        "portal_user_id": portal_user_id,
        "invite_sent": invite_sent,
        "mfa_enforced": mfa_enforced,
    }


def issue_email_verify_token(user_id: int, tenant_id: int) -> str:
    """Issue a 24h single-use email verification token for a portal user."""
    return issue_onboarding_token(
        PURPOSE_EMAIL_VERIFY,
        {"user_id": int(user_id), "tenant_id": int(tenant_id)},
        ttl_seconds=EMAIL_VERIFY_TTL_SECONDS,
    )
