"""Self-service DSAR (Data Subject Access Request) logic -- GDPR Art. 15/17, CCPA/CPRA.

Statutory rights (access/export, erasure, "Do Not Sell or Share" opt-out) are
available to every data subject on every license tier -- see
critical-rules.md "Feature Flags & License Tiers". Only the bulk/admin
convenience layer built on top of these (apps/api/api/v1/privacy_admin.py) is
Enterprise-gated.

Erasure anonymizes rather than hard-deletes: the identity row (and its id)
survives so audit_logs / RBAC history that reference it by id stay intact,
per docs/compliance/data-retention-policy.md's "audit log linking preserved
(anonymized)" exception, and is blocked while the owning tenant is under an
active legal hold (see that doc's Legal Holds & Data Preservation section).

Every method requires a caller-resolved ``tenant_id`` and re-verifies it
against the target identity via ``apps.api.utils.tenant_scoping.get_tenant_scoped()``
before touching a row -- the same gh-237 cross-tenant IDOR pattern used for
organizations/entities. This is defense-in-depth at the service layer: even
if a route handler's own tenant check has a gap, this layer refuses to
export/erase/update an identity that does not belong to `tenant_id`, and
raises the identical ``LookupError`` for "wrong tenant" and "doesn't exist"
so a caller can never distinguish the two (never a 403 that would let a
caller enumerate other tenants' ids).

Synchronous by design (PyDAL is sync) -- call every method here via
``run_in_threadpool`` from async route handlers, never directly in a
coroutine.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

import structlog

from apps.api.utils.tenant_scoping import get_tenant_scoped

logger = structlog.get_logger()

# Anonymized emails use a reserved, non-routable domain so a "deleted" user
# can never collide with (or accidentally notify) a real address.
ANONYMIZED_EMAIL_DOMAIN = "erased.invalid"


class LegalHoldError(Exception):
    """Raised when erasure is blocked by an active tenant legal hold."""


def _iso(value: Any) -> str | None:
    """Best-effort ISO-8601 serialization for datetime-like values."""
    return value.isoformat() if hasattr(value, "isoformat") else None


def _get_own_tenant_identity(db: Any, identity_id: int, tenant_id: int) -> Any:
    """Resolve `identity_id`, refusing it unless it belongs to `tenant_id`.

    Raises:
        LookupError: If the id doesn't exist OR belongs to another tenant --
            deliberately the same error either way (never 403; see module
            docstring).
    """
    identity = get_tenant_scoped(db, db.identities, identity_id, tenant_id)
    if not identity:
        raise LookupError(f"identity {identity_id} not found")
    return identity


class PrivacyService:
    """Synchronous PyDAL-backed DSAR operations, always tenant-scoped."""

    @staticmethod
    def export_identity(db: Any, identity_id: int, tenant_id: int) -> dict[str, Any]:
        """GDPR Art. 15 / CCPA right-to-know: the subject's own data + direct references.

        Returns a plain, explicitly-scoped dict (JSON-serializable) -- never
        the raw ORM row or its full ``__dict__`` (see security.md Output
        Validation).

        Args:
            db: penguin-dal DAL instance.
            identity_id: The identities.id of the requesting data subject.
            tenant_id: The caller's own tenant id (from validated JWT claims)
                -- `identity_id` must belong to this tenant.

        Returns:
            dict with "identity", "roles", and "group_memberships" keys.

        Raises:
            LookupError: If no identity with this id exists in `tenant_id`.
        """
        identity = _get_own_tenant_identity(db, identity_id, tenant_id)

        roles: list[Any] = []
        if "user_roles" in db.tables:
            roles = db(db.user_roles.identity_id == identity_id).select()

        group_memberships: list[Any] = []
        if "identity_group_memberships" in db.tables:
            group_memberships = db(
                db.identity_group_memberships.identity_id == identity_id
            ).select()

        return {
            "identity": {
                "id": identity.id,
                "username": identity.username,
                "email": identity.email,
                "full_name": identity.full_name,
                "identity_type": str(identity.identity_type),
                "auth_provider": str(identity.auth_provider),
                "is_active": identity.is_active,
                "mfa_enabled": identity.mfa_enabled,
                "do_not_sell_share": bool(
                    getattr(identity, "do_not_sell_share", False)
                ),
                "consent_withdrawn_at": _iso(
                    getattr(identity, "consent_withdrawn_at", None)
                ),
                "last_login_at": _iso(getattr(identity, "last_login_at", None)),
                "created_at": _iso(identity.created_at),
            },
            "roles": [
                {
                    "role_id": getattr(r, "role_id", None),
                    "team_id": getattr(r, "team_id", None),
                }
                for r in roles
            ],
            "group_memberships": [
                {"group_id": getattr(m, "group_id", None)} for m in group_memberships
            ],
        }

    @staticmethod
    def erase_identity(
        db: Any, identity_id: int, tenant_id: int, tenant_legal_hold: bool
    ) -> dict[str, Any]:
        """GDPR Art. 17 right-to-erasure: anonymize the identity's PII.

        Args:
            db: penguin-dal DAL instance.
            identity_id: The identities.id to erase.
            tenant_id: The tenant `identity_id` must belong to -- for
                self-service this is the caller's own tenant; for the
                Enterprise bulk layer, the admin's tenant (never
                caller-supplied on its own -- see privacy_admin.py).
            tenant_legal_hold: True if `tenant_id` has an active legal hold
                -- blocks erasure rather than purging preservable data.

        Returns:
            dict confirming the identity_id and anonymized state.

        Raises:
            LegalHoldError: If `tenant_legal_hold` is True.
            LookupError: If no identity with this id exists in `tenant_id`
                (including: it exists, but belongs to a different tenant).
        """
        if tenant_legal_hold:
            raise LegalHoldError(
                "erasure blocked: tenant is under an active legal hold"
            )

        _get_own_tenant_identity(db, identity_id, tenant_id)

        anon_suffix = secrets.token_hex(8)
        db(
            (db.identities.id == identity_id) & (db.identities.tenant_id == tenant_id)
        ).update(
            username=f"erased-{identity_id}-{anon_suffix}",
            email=f"erased-{identity_id}-{anon_suffix}@{ANONYMIZED_EMAIL_DOMAIN}",
            full_name=None,
            password_hash=None,
            mfa_secret=None,
            mfa_enabled=False,
            external_id=None,
            identity_metadata=None,
            is_active=False,
            anonymized_at=datetime.now(UTC),
        )
        db.commit()
        logger.info(
            "dsar_identity_anonymized", identity_id=identity_id, tenant_id=tenant_id
        )
        return {"identity_id": identity_id, "anonymized": True}

    @staticmethod
    def set_do_not_sell(
        db: Any, identity_id: int, tenant_id: int, opted_out: bool
    ) -> dict[str, Any]:
        """CCPA/CPRA "Do Not Sell or Share" opt-out + consent withdrawal flag.

        Args:
            db: penguin-dal DAL instance.
            identity_id: The identities.id to update.
            tenant_id: The tenant `identity_id` must belong to (caller's own
                tenant, from validated JWT claims).
            opted_out: True to opt out (and record consent withdrawal), False
                to opt back in (clears the withdrawal timestamp).

        Returns:
            dict confirming the identity_id and new flag state.

        Raises:
            LookupError: If no identity with this id exists in `tenant_id`.
        """
        _get_own_tenant_identity(db, identity_id, tenant_id)

        db(
            (db.identities.id == identity_id) & (db.identities.tenant_id == tenant_id)
        ).update(
            do_not_sell_share=opted_out,
            consent_withdrawn_at=datetime.now(UTC) if opted_out else None,
        )
        db.commit()
        logger.info(
            "dsar_consent_updated",
            identity_id=identity_id,
            tenant_id=tenant_id,
            opted_out=opted_out,
        )
        return {"identity_id": identity_id, "do_not_sell_share": opted_out}

    @staticmethod
    def bulk_erase(
        db: Any, identity_ids: list[int], tenant_id: int, tenant_legal_hold: bool
    ) -> dict[str, Any]:
        """Enterprise admin bulk erasure -- same anonymization, many identities at once.

        `tenant_id` is the calling admin's own tenant (never trust
        `identity_ids` alone) -- every id is re-verified against it via
        `erase_identity`'s tenant-scoped lookup, so an id belonging to
        another tenant fails closed with "not found" rather than being
        erased. Never raises on a per-identity failure -- collects per-id
        outcomes so one bad id in a bulk batch doesn't abort the rest.

        Args:
            db: penguin-dal DAL instance.
            identity_ids: identities.id values to erase.
            tenant_id: The admin's own tenant -- every id must belong to it.
            tenant_legal_hold: True if `tenant_id` has an active legal hold
                -- blocks every identity in the batch.

        Returns:
            dict with a "results" list, one entry per identity_id.
        """
        results = []
        for iid in identity_ids:
            try:
                results.append(
                    PrivacyService.erase_identity(db, iid, tenant_id, tenant_legal_hold)
                )
            except (LookupError, LegalHoldError) as exc:
                results.append(
                    {"identity_id": iid, "anonymized": False, "error": str(exc)}
                )
        return {"results": results}
