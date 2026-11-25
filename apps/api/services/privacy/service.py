"""Self-service DSAR (Data Subject Access Request) logic -- GDPR Art. 7/15/17, CCPA/CPRA.

Statutory rights (access/export, erasure, "Do Not Sell or Share" opt-out,
consent withdrawal) are available to every data subject on every license
tier and are not behind any feature flag -- see critical-rules.md "Feature
Flags & License Tiers". Only the bulk/admin convenience layer built on top
(apps/api/api/v1/privacy_admin.py) is Enterprise-gated.

Erasure is driven by the declarative inventory in ``inventory.py``: every
table/column that links to the subject is deleted, unlinked, or retained
with a recorded legal basis, the subject's own row is overwritten into an
anonymised tombstone (the row and id survive so retained business records
keep a valid foreign key, but nothing in it identifies a person), and a
PII-free receipt is stored and returned. Erasure is blocked while the owning
tenant is under an active legal hold.

penguin-dal auto-commits every statement, so a multi-table erasure cannot be
one database transaction. It is instead ordered and resumable: the subject
is deactivated first (every issued token is rejected from that point), the
sweep is idempotent, and ``anonymized_at`` is only set by the final step --
an interrupted run leaves an in-progress receipt and is completed by simply
calling erase again.

Every method requires a caller-resolved ``tenant_id`` and re-verifies it
against the target subject via ``get_tenant_scoped()`` before touching a
row (gh-237 cross-tenant IDOR pattern), raising the identical
``LookupError`` for "wrong tenant" and "doesn't exist".

Synchronous by design (penguin-dal sync DAL) -- call every method here via
``run_in_threadpool`` from async route handlers.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pyotp
import structlog
from werkzeug.security import check_password_hash

from apps.api.services.privacy.inventory import (
    IDENTITY_PII_COLUMNS,
    LEGAL_BASIS,
    NOT_ERASED_SYNCHRONOUSLY,
    PORTAL_USER_PII_COLUMNS,
    Action,
    ConsentPurpose,
    ConsentState,
    InventoryEntry,
    LawfulBasis,
    ReceiptStatus,
    SubjectType,
    inventory_for,
)
from apps.api.utils.tenant_scoping import get_tenant_scoped
from shared.observability import get_meter, get_tracer

logger = structlog.get_logger()

_tracer = get_tracer("elder")
_meter = get_meter("elder")
_request_counter = _meter.create_counter(
    "elder.privacy.requests",
    description="Statutory privacy-rights operations by outcome",
)
_duration_ms = _meter.create_histogram(
    "elder.privacy.duration_ms",
    unit="ms",
    description="Duration of statutory privacy-rights operations",
)

# Anonymized emails use a reserved, non-routable domain so a "deleted" user
# can never collide with (or accidentally notify) a real address.
ANONYMIZED_EMAIL_DOMAIN = "erased.invalid"

# Per-table cap on rows returned by an export; hitting it is reported in the
# bundle's "truncated" list rather than silently dropped.
EXPORT_ROW_LIMIT = 5000

REDACTED = "[erased]"
_PII_DETAIL_KEYS = frozenset(
    {"email", "username", "full_name", "name", "ip", "ip_address", "user_agent"}
)
# audit_logs.resource_type values whose resource_id is an identities.id.
_IDENTITY_RESOURCE_TYPES = ("identity", "identities")

# Aliases shorter than this, or in the deny-list, are never used to search
# free text: they would match records about other people.
MIN_ALIAS_LENGTH = 4
_ALIAS_DENY_LIST = frozenset(
    {
        "admin",
        "administrator",
        "anonymous",
        "default",
        "demo",
        "elder",
        "false",
        "guest",
        "info",
        "none",
        "null",
        "operator",
        "owner",
        "root",
        "service",
        "support",
        "system",
        "test",
        "tester",
        "true",
        "unknown",
        "user",
        "users",
    }
)


class LegalHoldError(Exception):
    """Raised when erasure is blocked by an active tenant legal hold."""


class LastAdminError(Exception):
    """Raised when erasure would remove the tenant's only active administrator."""


def _iso(value: Any) -> str | None:
    """Best-effort ISO-8601 serialization for datetime-like values."""
    return value.isoformat() if hasattr(value, "isoformat") else None


def _jsonable(value: Any) -> Any:
    """Coerce a column value into something ``jsonify`` can emit."""
    if value is None or isinstance(value, (str, int, float, bool, dict, list)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _has_table(db: Any, table: str) -> bool:
    """True if `table` exists in this deployment's schema.

    Optional modules (and databases not yet migrated to revision 054) may
    lack a table; callers record the absence instead of crashing a
    statutory right.
    """
    try:
        return table in db.tables
    except TypeError:
        return False


def _subject_table(db: Any, subject_type: SubjectType) -> Any:
    """Return the DAL table holding subjects of `subject_type`."""
    return db.portal_users if subject_type is SubjectType.PORTAL_USER else db.identities


def _get_subject(
    db: Any, subject_id: int, tenant_id: int, subject_type: SubjectType
) -> Any:
    """Resolve the subject row, refusing it unless it belongs to `tenant_id`.

    Raises:
        LookupError: If the id doesn't exist OR belongs to another tenant --
            deliberately the same error either way (never a 403 that would
            let a caller enumerate other tenants' ids).
    """
    subject = get_tenant_scoped(
        db, _subject_table(db, subject_type), subject_id, tenant_id
    )
    if not subject:
        raise LookupError(f"subject {subject_id} not found")
    return subject


def _get_own_tenant_identity(db: Any, identity_id: int, tenant_id: int) -> Any:
    """Resolve an identities row scoped to `tenant_id` (see `_get_subject`)."""
    return _get_subject(db, identity_id, tenant_id, SubjectType.IDENTITY)


def _tenant_on_legal_hold(db: Any, tenant_id: int) -> bool:
    """True if `tenant_id` currently has an active legal hold.

    Re-read inside the service (not only in the route) so a hold placed
    between the route's check and the erasure still blocks it.
    """
    tenant = db(db.tenants.id == tenant_id).select().first()
    return getattr(tenant, "legal_hold", False) is True


def _admin_count(db: Any, tenant_id: int) -> int:
    """Number of active administrators of `tenant_id` across both identity tables.

    An administrator is an active identity that is a superuser or has
    ``portal_role == "admin"``, or an active portal user whose global or
    tenant role is ``"admin"`` -- the same notion ``@admin_required`` uses.
    """
    identities = db.identities
    count = db(
        (identities.tenant_id == tenant_id)
        & (identities.is_active == True)  # noqa: E712 -- DAL field comparison
        & (
            (identities.portal_role == "admin") | (identities.is_superuser == True)  # noqa: E712
        )
    ).count()
    if _has_table(db, "portal_users"):
        portal = db.portal_users
        count += db(
            (portal.tenant_id == tenant_id)
            & (portal.is_active == True)  # noqa: E712
            & ((portal.global_role == "admin") | (portal.tenant_role == "admin"))
        ).count()
    return int(count)


def _is_last_tenant_admin(
    db: Any, subject: Any, tenant_id: int, subject_type: SubjectType
) -> bool:
    """True if erasing `subject` would leave `tenant_id` with no administrator.

    Erasure deactivates the subject, so a sole admin erasing themselves
    would orphan the tenant (nobody could manage users, holds or other
    subjects' requests). The right is deferred, not denied: it proceeds as
    soon as another active admin exists.
    """
    if getattr(subject, "is_active", None) is not True:
        return False  # an inactive subject is not counted among the admins
    if subject_type is SubjectType.PORTAL_USER:
        is_admin = "admin" in (
            getattr(subject, "global_role", None),
            getattr(subject, "tenant_role", None),
        )
    else:
        is_admin = (
            getattr(subject, "portal_role", None) == "admin"
            or getattr(subject, "is_superuser", None) is True
        )
    return is_admin and _admin_count(db, tenant_id) <= 1


def _is_erased(subject: Any, subject_type: SubjectType) -> bool:
    """True if the subject row is already an anonymised tombstone."""
    if subject_type is SubjectType.PORTAL_USER:
        email = getattr(subject, "email", None)
        return isinstance(email, str) and email.endswith(f"@{ANONYMIZED_EMAIL_DOMAIN}")
    return getattr(subject, "anonymized_at", None) is not None


def _usable_alias(value: Any) -> str | None:
    """Return `value` if it is specific enough to search-and-redact with.

    Short or generic strings ("admin", "test", a two-letter username) would
    match text that has nothing to do with the subject, so they are never
    used for free-text matching at all.
    """
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if len(cleaned) < MIN_ALIAS_LENGTH or cleaned.lower() in _ALIAS_DENY_LIST:
        return None
    return cleaned


def _aliases(
    db: Any, subject: Any, tenant_id: int, subject_type: SubjectType
) -> dict[str, str]:
    """The subject's own identifiers, captured once when the erasure starts.

    Used only to find and redact copies of them. E-mail and username are
    the subject's stored identifiers; the display name is included only
    when no other subject in the tenant shares it, because redacting a
    shared name would alter records about a different person.
    """
    found: dict[str, str] = {}
    for key in ("username", "email"):
        alias = _usable_alias(getattr(subject, key, None))
        if alias:
            found[key] = alias
    name = _usable_alias(getattr(subject, "full_name", None))
    if name:
        table = _subject_table(db, subject_type)
        same_name = db(
            (table.tenant_id == tenant_id) & (table.full_name == name)
        ).count()
        if same_name == 1:
            found["full_name"] = name
    return found


def _alias_pattern(aliases: dict[str, str]) -> re.Pattern[str] | None:
    """Case-insensitive whole-token matcher for the subject's aliases.

    An alias matches only when it is not part of a longer word or dotted /
    hyphenated / e-mail token: "bob" matches "login by bob." but not
    "bobcat", "bob-smith" or "bob@example.com". Longest alias first so an
    e-mail wins over the username it contains.
    """
    values = sorted(set(aliases.values()), key=len, reverse=True)
    if not values:
        return None
    body = "|".join(re.escape(v) for v in values)
    return re.compile(
        r"(?<![A-Za-z0-9_])(?<![A-Za-z0-9][.\-@+])"
        rf"(?:{body})"
        r"(?![A-Za-z0-9_])(?![.\-@+][A-Za-z0-9])",
        re.IGNORECASE,
    )


def _sha256(value: Any) -> str:
    """SHA-256 hex digest of a redacted original value (for the receipt)."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _redact(
    value: Any,
    pattern: re.Pattern[str] | None,
    hashes: dict[str, int],
    redact_keys: bool,
) -> Any:
    """Recursively remove the subject's identifiers from a JSON value.

    Every occurrence of an alias (whole-token match) is replaced in place,
    leaving the rest of the string intact. When `redact_keys` is True --
    only for events the subject performed themselves -- values under
    PII-named keys are replaced outright; on events performed by someone
    else those keys describe the other actor and are left alone. Each
    redacted original is tallied in `hashes` by SHA-256, never in plaintext.
    """
    if isinstance(value, dict):
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            if (
                redact_keys
                and str(key).lower() in _PII_DETAIL_KEYS
                and isinstance(item, (str, int, float))
                and not isinstance(item, bool)
                and item != REDACTED
            ):
                digest = _sha256(item)
                hashes[digest] = hashes.get(digest, 0) + 1
                cleaned[key] = REDACTED
            else:
                cleaned[key] = _redact(item, pattern, hashes, redact_keys)
        return cleaned
    if isinstance(value, list):
        return [_redact(item, pattern, hashes, redact_keys) for item in value]
    if isinstance(value, str) and pattern is not None:

        def _swap(match: re.Match[str]) -> str:
            digest = _sha256(match.group(0).lower())
            hashes[digest] = hashes.get(digest, 0) + 1
            return REDACTED

        return pattern.sub(_swap, value)
    return value


def _record(
    operation: str, outcome: str, subject_type: SubjectType, started: float
) -> None:
    """Emit the request counter + duration histogram (low-cardinality labels only)."""
    attrs = {
        "operation": operation,
        "outcome": outcome,
        "subject_type": subject_type.name.lower(),
    }
    _request_counter.add(1, attributes=attrs)
    _duration_ms.record((time.monotonic() - started) * 1000.0, attributes=attrs)


class PrivacyService:
    """Synchronous penguin-dal-backed DSAR operations, always tenant-scoped."""

    @staticmethod
    def export_identity(
        db: Any,
        identity_id: int,
        tenant_id: int,
        subject_type: SubjectType = SubjectType.IDENTITY,
    ) -> dict[str, Any]:
        """GDPR Art. 15 / CCPA right-to-know: a machine-readable bundle of the subject's data.

        Covers every category in the personal-data inventory. Each table
        contributes only its explicit column allow-list and only rows linked
        to this subject -- never a raw row, a secret, or another subject's
        data (see security.md Output Validation).

        Args:
            db: penguin-dal DAL instance.
            identity_id: Id of the requesting data subject.
            tenant_id: The caller's own tenant id (from validated JWT claims).
            subject_type: Whether the id is an identity or a portal user.

        Returns:
            JSON-serializable dict: "identity", "roles", "group_memberships",
            "consent", "categories", "truncated" plus bundle metadata.

        Raises:
            LookupError: If no such subject exists in `tenant_id`.
        """
        started = time.monotonic()
        with _tracer.start_as_current_span("privacy.export"):
            try:
                subject = _get_subject(db, identity_id, tenant_id, subject_type)
            except LookupError:
                _record("export", "not_found", subject_type, started)
                raise

            categories: dict[str, dict[str, list[dict[str, Any]]]] = {}
            truncated: list[str] = []
            for entry in inventory_for(subject_type):
                if entry.action is Action.SCRUB_MATCH or not _has_table(
                    db, entry.table
                ):
                    continue
                field = getattr(getattr(db, entry.table), entry.column)
                rows = list(
                    db(field == identity_id).select(limitby=(0, EXPORT_ROW_LIMIT + 1))
                )
                key = f"{entry.table}.{entry.column}"
                if len(rows) > EXPORT_ROW_LIMIT:
                    truncated.append(key)
                    rows = rows[:EXPORT_ROW_LIMIT]
                categories.setdefault(entry.category, {})[key] = [
                    {col: _jsonable(row.get(col)) for col in entry.export}
                    for row in rows
                ]

            access = categories.get("access", {})
            bundle = {
                "schema_version": 2,
                "generated_at": datetime.now(UTC).isoformat(),
                "subject": {
                    "type": int(subject_type),
                    "id": identity_id,
                    "tenant_id": tenant_id,
                },
                "identity": PrivacyService._profile(subject, subject_type),
                "roles": [
                    {"role_id": r.get("role_id"), "team_id": r.get("scope_id")}
                    for r in access.get("user_roles.identity_id", [])
                ],
                "group_memberships": [
                    {"group_id": m.get("group_id")}
                    for m in access.get("identity_group_memberships.identity_id", [])
                ],
                "consent": PrivacyService._consent_snapshot(
                    db, subject, identity_id, tenant_id, subject_type
                ),
                "categories": categories,
                "truncated": truncated,
            }
            logger.info(
                "dsar_export_generated",
                subject_id=identity_id,
                subject_type=int(subject_type),
                tenant_id=tenant_id,
                categories=len(categories),
            )
            _record("export", "ok", subject_type, started)
            return bundle

    @staticmethod
    def _profile(subject: Any, subject_type: SubjectType) -> dict[str, Any]:
        """Explicit profile DTO for the subject's own row -- never secrets.

        Password hashes, MFA secrets and backup codes are credentials, not
        data the subject provided for processing, and are never exported.
        """
        if subject_type is SubjectType.PORTAL_USER:
            return {
                "id": subject.id,
                "email": getattr(subject, "email", None),
                "full_name": getattr(subject, "full_name", None),
                "global_role": getattr(subject, "global_role", None),
                "tenant_role": getattr(subject, "tenant_role", None),
                "is_active": getattr(subject, "is_active", None),
                "email_verified": getattr(subject, "email_verified", None),
                "last_login_at": _jsonable(getattr(subject, "last_login_at", None)),
            }
        return {
            "id": subject.id,
            "username": subject.username,
            "email": subject.email,
            "full_name": subject.full_name,
            "identity_type": str(subject.identity_type),
            "auth_provider": str(subject.auth_provider),
            "auth_provider_id": getattr(subject, "auth_provider_id", None),
            "external_id": getattr(subject, "external_id", None),
            "profile_metadata": _jsonable(getattr(subject, "metadata", None)),
            "is_active": subject.is_active,
            "mfa_enabled": subject.mfa_enabled,
            "do_not_sell_share": bool(getattr(subject, "do_not_sell_share", False)),
            "consent_withdrawn_at": _iso(
                getattr(subject, "consent_withdrawn_at", None)
            ),
            "last_login_at": _jsonable(getattr(subject, "last_login_at", None)),
            "created_at": _iso(subject.created_at),
        }

    @staticmethod
    def _consent_rows(
        db: Any, subject_id: int, tenant_id: int, subject_type: SubjectType
    ) -> list[Any]:
        """The subject's consent-ledger rows, oldest first, tenant-scoped."""
        if not _has_table(db, "privacy_consent_records"):
            return []
        table = db.privacy_consent_records
        return list(
            db(
                (table.subject_id == subject_id)
                & (table.subject_type == int(subject_type))
                & (table.tenant_id == tenant_id)
            ).select(orderby=table.id, limitby=(0, EXPORT_ROW_LIMIT))
        )

    @staticmethod
    def _consent_snapshot(
        db: Any,
        subject: Any,
        subject_id: int,
        tenant_id: int,
        subject_type: SubjectType,
    ) -> dict[str, Any]:
        """Current per-purpose consent state plus the full ledger history."""
        history = [
            {
                "id": row.get("public_id"),
                "purpose": row.get("purpose"),
                "state": row.get("state"),
                "lawful_basis": row.get("lawful_basis"),
                "recorded_at": _jsonable(row.get("created_at")),
            }
            for row in PrivacyService._consent_rows(
                db, subject_id, tenant_id, subject_type
            )
        ]
        current: dict[int, dict[str, Any]] = {}
        for item in history:
            current[item["purpose"]] = item
        sale = current.get(int(ConsentPurpose.SALE_OR_SHARING))
        if sale is not None:
            opted_out = sale["state"] == int(ConsentState.WITHDRAWN)
        else:
            opted_out = bool(getattr(subject, "do_not_sell_share", False) is True)
        return {
            "do_not_sell_share": opted_out,
            "purposes": [current[p] for p in sorted(current)],
            "history": history,
        }

    @staticmethod
    def get_consent(
        db: Any,
        identity_id: int,
        tenant_id: int,
        subject_type: SubjectType = SubjectType.IDENTITY,
    ) -> dict[str, Any]:
        """Return the subject's current consent state and ledger history.

        Args:
            db: penguin-dal DAL instance.
            identity_id: Id of the requesting data subject.
            tenant_id: The caller's own tenant id.
            subject_type: Whether the id is an identity or a portal user.

        Returns:
            dict with "do_not_sell_share", "purposes" and "history".

        Raises:
            LookupError: If no such subject exists in `tenant_id`.
        """
        subject = _get_subject(db, identity_id, tenant_id, subject_type)
        return PrivacyService._consent_snapshot(
            db, subject, identity_id, tenant_id, subject_type
        )

    @staticmethod
    def record_consent(
        db: Any,
        identity_id: int,
        tenant_id: int,
        purpose: ConsentPurpose,
        granted: bool,
        subject_type: SubjectType = SubjectType.IDENTITY,
    ) -> dict[str, Any]:
        """Append a consent grant or withdrawal to the ledger for one purpose.

        The ledger row carries the lawful basis: Art. 6(1)(a) consent for a
        grant or its withdrawal, Art. 6(1)(c) legal obligation for honouring
        a CCPA/CPRA sale-or-sharing opt-out. For identities the legacy
        ``do_not_sell_share`` / ``consent_withdrawn_at`` columns are kept in
        step so existing readers stay correct.

        Args:
            db: penguin-dal DAL instance.
            identity_id: Id of the data subject.
            tenant_id: The tenant the subject must belong to.
            purpose: Processing purpose the choice applies to.
            granted: True to grant, False to withdraw / opt out.
            subject_type: Whether the id is an identity or a portal user.

        Returns:
            dict with the subject id, purpose, state, lawful basis and
            (for the sale-or-sharing purpose) ``do_not_sell_share``.

        Raises:
            LookupError: If no such subject exists in `tenant_id`.
        """
        started = time.monotonic()
        with _tracer.start_as_current_span("privacy.consent"):
            try:
                _get_subject(db, identity_id, tenant_id, subject_type)
            except LookupError:
                _record("consent", "not_found", subject_type, started)
                raise

            state = ConsentState.GRANTED if granted else ConsentState.WITHDRAWN
            is_sale = purpose is ConsentPurpose.SALE_OR_SHARING
            basis = (
                LawfulBasis.LEGAL_OBLIGATION
                if is_sale and not granted
                else LawfulBasis.CONSENT
            )
            record_id: str | None = None
            if _has_table(db, "privacy_consent_records"):
                record_id = str(uuid.uuid4())
                db.privacy_consent_records.insert(
                    tenant_id=tenant_id,
                    public_id=record_id,
                    subject_type=int(subject_type),
                    subject_id=identity_id,
                    purpose=int(purpose),
                    state=int(state),
                    lawful_basis=int(basis),
                    created_at=datetime.now(UTC),
                )
            else:
                logger.warning("dsar_consent_ledger_missing", tenant_id=tenant_id)

            if subject_type is SubjectType.IDENTITY:
                if is_sale:
                    changes: dict[str, Any] = {
                        "do_not_sell_share": not granted,
                        "consent_withdrawn_at": None if granted else datetime.now(UTC),
                    }
                elif not granted:
                    changes = {"consent_withdrawn_at": datetime.now(UTC)}
                else:
                    changes = {}
                if changes:
                    db(
                        (db.identities.id == identity_id)
                        & (db.identities.tenant_id == tenant_id)
                    ).update(**changes)
            db.commit()

            logger.info(
                "dsar_consent_updated",
                identity_id=identity_id,
                subject_type=int(subject_type),
                tenant_id=tenant_id,
                purpose=int(purpose),
                state=int(state),
                opted_out=not granted,
            )
            _record("consent", "ok", subject_type, started)
            result: dict[str, Any] = {
                "identity_id": identity_id,
                "record_id": record_id,
                "purpose": int(purpose),
                "state": int(state),
                "lawful_basis": int(basis),
            }
            if is_sale:
                result["do_not_sell_share"] = not granted
            return result

    @staticmethod
    def set_do_not_sell(
        db: Any,
        identity_id: int,
        tenant_id: int,
        opted_out: bool,
        subject_type: SubjectType = SubjectType.IDENTITY,
    ) -> dict[str, Any]:
        """CCPA/CPRA "Do Not Sell or Share" opt-out (or opt back in).

        Thin wrapper over `record_consent` for the sale-or-sharing purpose.

        Args:
            db: penguin-dal DAL instance.
            identity_id: Id of the data subject.
            tenant_id: The tenant the subject must belong to.
            opted_out: True to opt out, False to opt back in.
            subject_type: Whether the id is an identity or a portal user.

        Returns:
            dict confirming the subject id and new flag state.

        Raises:
            LookupError: If no such subject exists in `tenant_id`.
        """
        return PrivacyService.record_consent(
            db,
            identity_id,
            tenant_id,
            ConsentPurpose.SALE_OR_SHARING,
            granted=not opted_out,
            subject_type=subject_type,
        )

    @staticmethod
    def withdraw_all_consent(
        db: Any,
        identity_id: int,
        tenant_id: int,
        subject_type: SubjectType = SubjectType.IDENTITY,
    ) -> dict[str, Any]:
        """GDPR Art. 7(3): withdraw consent for every purpose in one call.

        Also opts the subject out of sale/sharing. Idempotent -- repeating
        it only appends further withdrawal entries to the ledger.

        Args:
            db: penguin-dal DAL instance.
            identity_id: Id of the data subject.
            tenant_id: The tenant the subject must belong to.
            subject_type: Whether the id is an identity or a portal user.

        Returns:
            dict with the subject id and one ledger result per purpose.

        Raises:
            LookupError: If no such subject exists in `tenant_id`.
        """
        results = [
            PrivacyService.record_consent(
                db, identity_id, tenant_id, purpose, False, subject_type
            )
            for purpose in ConsentPurpose
        ]
        return {
            "identity_id": identity_id,
            "do_not_sell_share": True,
            "withdrawn": results,
        }

    @staticmethod
    def has_local_credential(
        db: Any,
        identity_id: int,
        tenant_id: int,
        subject_type: SubjectType = SubjectType.IDENTITY,
    ) -> bool:
        """True if the subject can re-authenticate with a password or MFA code.

        SSO/SCIM-provisioned subjects have neither; the route then falls
        back to "signed in within the last few minutes" so the right is
        never made unreachable for them.

        Raises:
            LookupError: If no such subject exists in `tenant_id`.
        """
        subject = _get_subject(db, identity_id, tenant_id, subject_type)
        return bool(
            getattr(subject, "password_hash", None)
            or getattr(subject, "mfa_secret", None)
        )

    @staticmethod
    def verify_reauth(
        db: Any,
        identity_id: int,
        tenant_id: int,
        subject_type: SubjectType,
        password: str | None,
        mfa_code: str | None,
    ) -> bool:
        """Re-authenticate the subject before a destructive action.

        Accepts the current password, or a valid TOTP code when the subject
        has MFA enrolled -- one factor is enough, so erasure is no harder
        than signing in. Never logs or returns the supplied secret.

        Args:
            db: penguin-dal DAL instance.
            identity_id: Id of the data subject.
            tenant_id: The tenant the subject must belong to.
            subject_type: Whether the id is an identity or a portal user.
            password: The subject's current password, if supplied.
            mfa_code: A current TOTP code, if supplied.

        Returns:
            True only if a supplied factor verifies.

        Raises:
            LookupError: If no such subject exists in `tenant_id`.
        """
        subject = _get_subject(db, identity_id, tenant_id, subject_type)
        password_hash = getattr(subject, "password_hash", None)
        if (
            isinstance(password, str)
            and password
            and isinstance(password_hash, str)
            and check_password_hash(password_hash, password)
        ):
            return True
        secret = getattr(subject, "mfa_secret", None)
        if isinstance(mfa_code, str) and mfa_code.strip() and isinstance(secret, str):
            try:
                return bool(pyotp.TOTP(secret).verify(mfa_code.strip(), valid_window=1))
            except Exception:
                # A malformed stored secret must fail closed, not 500.
                logger.warning(
                    "dsar_reauth_mfa_unverifiable",
                    subject_id=identity_id,
                    tenant_id=tenant_id,
                )
                return False
        return False

    @staticmethod
    def _apply_entry(
        db: Any,
        entry: InventoryEntry,
        subject_id: int,
        tenant_id: int,
        aliases: dict[str, str],
    ) -> dict[str, Any]:
        """Erase/unlink/retain the rows one inventory entry points at.

        Returns the PII-free receipt line for the entry (names, counts,
        action and legal basis only).
        """
        line: dict[str, Any] = {
            "table": entry.table,
            "column": entry.column,
            "action": int(entry.action),
            "action_name": entry.action.name.lower(),
            "scrubbed_columns": list(entry.scrub),
            "legal_basis": LEGAL_BASIS[entry.basis],
            "rows": 0,
        }
        if not _has_table(db, entry.table):
            line["skipped"] = "table_absent"
            return line

        table = getattr(db, entry.table)
        field = getattr(table, entry.column)
        if entry.action is Action.DELETE and entry.keep_if_referenced_by:
            # Rows another retained record still points at cannot be deleted
            # (NOT NULL FK); they are kept, minus their own PII columns.
            ref_table, ref_column = entry.keep_if_referenced_by
            kept: list[int] = []
            if _has_table(db, ref_table):
                ref_field = getattr(getattr(db, ref_table), ref_column)
                for row in db(field == subject_id).select(table.id):
                    if db(ref_field == row.get("id")).count():
                        kept.append(row.get("id"))
            query = field == subject_id
            for row_id in kept:
                query = query & (table.id != row_id)
            line["rows"] = db(query).delete()
            line["retained_rows"] = len(kept)
            line["retained_basis"] = LEGAL_BASIS["access_review_evidence"]
            if kept and entry.scrub:
                db(field == subject_id).update(**dict.fromkeys(entry.scrub))
        elif entry.action is Action.DELETE:
            line["rows"] = db(field == subject_id).delete()
        elif entry.action is Action.UNLINK:
            changes: dict[str, Any] = dict.fromkeys(entry.scrub)
            changes[entry.column] = None
            line["rows"] = db(field == subject_id).update(**changes)
        elif entry.action is Action.RETAIN:
            line["rows"] = db(field == subject_id).count()
        elif entry.action is Action.SCRUB_MATCH:
            # Free-text match, so it MUST stay inside the subject's tenant:
            # the same e-mail may legitimately exist in another tenant.
            # Exact (whole-value) match only, with the same alias rules as
            # audit redaction: never a short or generic token.
            total = 0
            redactions = []
            for key in ("username", "email"):
                alias = aliases.get(key)
                if alias:
                    count = db(
                        (field == alias) & (table.tenant_id == tenant_id)
                    ).update(**{entry.column: None})
                    total += count
                    if count:
                        redactions.append({"sha256": _sha256(alias), "count": count})
            line["rows"] = total
            line["redactions"] = redactions
        return line

    @staticmethod
    def _redact_audit_details(
        db: Any, subject_id: int, tenant_id: int, aliases: dict[str, str]
    ) -> dict[str, Any]:
        """Strip the subject's identifiers out of audit ``details`` JSON.

        Two row sets, both confined to the subject's tenant (audit_logs has
        no tenant column, so the tenant is enforced through the actor):

        * events the subject performed (``identity_id`` = the tenant-verified
          subject) -- alias occurrences and PII-keyed values are redacted;
        * events another identity OF THE SAME TENANT performed on the
          subject's identity -- only whole-token alias occurrences are
          redacted; keys naming the actor are never touched. Rows whose
          actor is unknown or belongs to another tenant are skipped.

        Must run before the audit rows are unlinked. The event itself
        (action, resource, time, outcome) stays. The receipt line carries a
        count and SHA-256 per redacted original value, never plaintext.
        """
        line: dict[str, Any] = {
            "table": "audit_logs",
            "column": "details",
            "action": int(Action.SCRUB_MATCH),
            "action_name": "redact_json",
            "scrubbed_columns": ["details"],
            "legal_basis": LEGAL_BASIS["security_audit"],
            "rows": 0,
            "redacted_values": 0,
            "redactions": [],
        }
        if not _has_table(db, "audit_logs"):
            line["skipped"] = "table_absent"
            return line

        table = db.audit_logs
        pattern = _alias_pattern(aliases)
        hashes: dict[str, int] = {}

        own_rows = list(
            db(table.identity_id == subject_id).select(table.id, table.details)
        )
        about_rows = list(
            db(
                table.resource_type.belongs(_IDENTITY_RESOURCE_TYPES)
                & (table.resource_id == subject_id)
                & (table.identity_id != subject_id)
            ).select(table.id, table.identity_id, table.details)
        )
        actor_ids = sorted(
            {r.get("identity_id") for r in about_rows if r.get("identity_id")}
        )
        same_tenant_actors: set[int] = set()
        if actor_ids:
            same_tenant_actors = {
                r.get("id")
                for r in db(
                    db.identities.id.belongs(actor_ids)
                    & (db.identities.tenant_id == tenant_id)
                ).select(db.identities.id)
            }

        work = [(row, subject_id, True) for row in own_rows] + [
            (row, row.get("identity_id"), False)
            for row in about_rows
            if row.get("identity_id") in same_tenant_actors
        ]
        for row, actor_id, redact_keys in work:
            original = row.get("details")
            details = original
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except ValueError:
                    details = None
            if details is None:
                continue
            cleaned = _redact(details, pattern, hashes, redact_keys)
            if cleaned != details:
                # The actor filter keeps the UPDATE inside the tenant-verified
                # row set even if ids were reused concurrently.
                line["rows"] += db(
                    (table.id == row.get("id")) & (table.identity_id == actor_id)
                ).update(details=cleaned)
        line["redactions"] = [
            {"sha256": digest, "count": count}
            for digest, count in sorted(hashes.items())
        ]
        line["redacted_values"] = sum(hashes.values())
        return line

    @staticmethod
    def _stored_receipt(
        db: Any, subject_id: int, tenant_id: int, subject_type: SubjectType
    ) -> Any:
        """Latest erasure-receipt row for the subject, or None."""
        if not _has_table(db, "privacy_erasure_receipts"):
            return None
        table = db.privacy_erasure_receipts
        return (
            db(
                (table.subject_id == subject_id)
                & (table.subject_type == int(subject_type))
                & (table.tenant_id == tenant_id)
            )
            .select(orderby=~table.id, limitby=(0, 1))
            .first()
        )

    @staticmethod
    def erase_identity(
        db: Any,
        identity_id: int,
        tenant_id: int,
        tenant_legal_hold: bool,
        subject_type: SubjectType = SubjectType.IDENTITY,
        requested_by_id: int | None = None,
    ) -> dict[str, Any]:
        """GDPR Art. 17 right-to-erasure across the whole personal-data inventory.

        Order: legal-hold check -> deactivate the subject (revokes every
        stateless token) -> redact audit detail JSON -> walk the inventory
        (delete / unlink / retain) -> overwrite the subject row into an
        anonymised tombstone -> finalise the PII-free receipt. Idempotent:
        an already-erased subject returns its stored receipt unchanged; an
        interrupted run is finished by calling again.

        Args:
            db: penguin-dal DAL instance.
            identity_id: Id of the subject to erase.
            tenant_id: The tenant the subject must belong to -- the caller's
                own tenant (self-service) or the admin's tenant (bulk layer).
            tenant_legal_hold: True if the caller already knows the tenant is
                on legal hold; the service re-checks either way.
            subject_type: Whether the id is an identity or a portal user.
            requested_by_id: Actor id for the receipt (defaults to the subject).

        Returns:
            dict with "identity_id", "anonymized", "already_erased" and the
            PII-free "receipt".

        Raises:
            LegalHoldError: If the tenant is under an active legal hold.
            LastAdminError: If the subject is the tenant's only active admin.
            LookupError: If no such subject exists in `tenant_id`.
        """
        started = time.monotonic()
        with _tracer.start_as_current_span("privacy.erase"):
            if tenant_legal_hold:
                logger.info(
                    "dsar_erasure_blocked_legal_hold",
                    subject_id=identity_id,
                    tenant_id=tenant_id,
                )
                _record("erase", "legal_hold", subject_type, started)
                raise LegalHoldError(
                    "erasure blocked: tenant is under an active legal hold"
                )

            try:
                subject = _get_subject(db, identity_id, tenant_id, subject_type)
            except LookupError:
                _record("erase", "not_found", subject_type, started)
                raise

            stored = PrivacyService._stored_receipt(
                db, identity_id, tenant_id, subject_type
            )
            completed = stored is not None and stored.get("status") == int(
                ReceiptStatus.COMPLETED
            )
            # A completed receipt (or an already-tombstoned row) short-circuits
            # every repeat call: nothing is rewritten, the receipt is replayed.
            if completed or _is_erased(subject, subject_type):
                _record("erase", "already_erased", subject_type, started)
                return {
                    "identity_id": identity_id,
                    "anonymized": True,
                    "already_erased": True,
                    "receipt": stored.get("receipt") if stored else None,
                }

            # Re-read the hold here (not only in the route) so one placed
            # after the route's check still blocks the erasure.
            if _tenant_on_legal_hold(db, tenant_id):
                logger.info(
                    "dsar_erasure_blocked_legal_hold",
                    subject_id=identity_id,
                    tenant_id=tenant_id,
                )
                _record("erase", "legal_hold", subject_type, started)
                raise LegalHoldError(
                    "erasure blocked: tenant is under an active legal hold"
                )

            if _is_last_tenant_admin(db, subject, tenant_id, subject_type):
                logger.info(
                    "dsar_erasure_blocked_last_admin",
                    subject_id=identity_id,
                    tenant_id=tenant_id,
                )
                _record("erase", "last_admin", subject_type, started)
                raise LastAdminError(
                    "erasure blocked: you are the only active administrator "
                    "of this tenant -- transfer the admin role to another "
                    "user first, then request erasure again"
                )

            subject_table = _subject_table(db, subject_type)
            subject_query = (subject_table.id == identity_id) & (
                subject_table.tenant_id == tenant_id
            )
            receipt_public_id = str(uuid.uuid4())
            receipt_row_id: Any = None
            if stored is not None:
                # An interrupted run left an in-progress receipt: finish it
                # rather than opening a second one.
                receipt_row_id = stored.get("id")
                receipt_public_id = stored.get("public_id")
            elif _has_table(db, "privacy_erasure_receipts"):
                receipt_row_id = db.privacy_erasure_receipts.insert(
                    tenant_id=tenant_id,
                    public_id=receipt_public_id,
                    subject_type=int(subject_type),
                    subject_id=identity_id,
                    requested_by_id=requested_by_id or identity_id,
                    status=int(ReceiptStatus.IN_PROGRESS),
                    created_at=datetime.now(UTC),
                )
            else:
                logger.warning("dsar_receipt_table_missing", tenant_id=tenant_id)

            # 1. Revoke first: auth rejects an inactive subject on every
            #    request, so access/refresh tokens and API keys die here.
            db(subject_query).update(is_active=False)

            aliases = _aliases(db, subject, tenant_id, subject_type)
            lines: list[dict[str, Any]] = []
            if subject_type is SubjectType.IDENTITY:
                lines.append(
                    PrivacyService._redact_audit_details(
                        db, identity_id, tenant_id, aliases
                    )
                )
            for entry in inventory_for(subject_type):
                lines.append(
                    PrivacyService._apply_entry(
                        db, entry, identity_id, tenant_id, aliases
                    )
                )

            # 2. Tombstone the subject row last: `anonymized_at` doubles as
            #    the "erasure complete" marker that makes a retry safe.
            anon_suffix = secrets.token_hex(8)
            now = datetime.now(UTC)
            if subject_type is SubjectType.PORTAL_USER:
                pii_columns = PORTAL_USER_PII_COLUMNS
                db(subject_query).update(
                    email=(
                        f"erased-portal-{identity_id}-{anon_suffix}"
                        f"@{ANONYMIZED_EMAIL_DOMAIN}"
                    ),
                    full_name=None,
                    password_hash=None,
                    mfa_secret=None,
                    mfa_backup_codes=None,
                    last_login_at=None,
                    is_active=False,
                )
            else:
                pii_columns = IDENTITY_PII_COLUMNS
                db(subject_query).update(
                    username=f"erased-{identity_id}-{anon_suffix}",
                    email=f"erased-{identity_id}-{anon_suffix}@{ANONYMIZED_EMAIL_DOMAIN}",
                    full_name=None,
                    password_hash=None,
                    mfa_secret=None,
                    mfa_enabled=False,
                    auth_provider_id=None,
                    external_id=None,
                    metadata=None,
                    last_login_at=None,
                    is_active=False,
                    anonymized_at=now,
                )
            lines.insert(
                0,
                {
                    "table": (
                        "portal_users"
                        if subject_type is SubjectType.PORTAL_USER
                        else "identities"
                    ),
                    "column": "id",
                    "action": int(Action.ANONYMIZE),
                    "action_name": "anonymize",
                    "scrubbed_columns": list(pii_columns),
                    "legal_basis": LEGAL_BASIS["erasure"],
                    "rows": 1,
                },
            )

            receipt = {
                "receipt_id": receipt_public_id,
                "subject_type": int(subject_type),
                "subject_id": identity_id,
                "tenant_id": tenant_id,
                "completed_at": now.isoformat(),
                "revocation": {
                    "subject_deactivated": True,
                    "api_keys_deleted": next(
                        (ln["rows"] for ln in lines if ln["table"] == "api_keys"), 0
                    ),
                    "tokens": (
                        "stateless JWT access/refresh tokens are rejected "
                        "because the subject is inactive"
                    ),
                },
                "tables": lines,
                "not_erased_synchronously": list(NOT_ERASED_SYNCHRONOUSLY),
            }
            if receipt_row_id is not None:
                db(db.privacy_erasure_receipts.id == receipt_row_id).update(
                    status=int(ReceiptStatus.COMPLETED),
                    receipt=receipt,
                    completed_at=now,
                )
            db.commit()

            logger.info(
                "dsar_identity_anonymized",
                identity_id=identity_id,
                subject_type=int(subject_type),
                tenant_id=tenant_id,
                tables=len(lines),
                rows=sum(int(ln["rows"]) for ln in lines),
            )
            _record("erase", "ok", subject_type, started)
            return {
                "identity_id": identity_id,
                "anonymized": True,
                "already_erased": False,
                "receipt": receipt,
            }

    @staticmethod
    def bulk_erase(
        db: Any,
        identity_ids: list[int],
        tenant_id: int,
        tenant_legal_hold: bool,
        requested_by_id: int | None = None,
    ) -> dict[str, Any]:
        """Enterprise admin bulk erasure -- the same full erasure, many identities at once.

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
            requested_by_id: The admin's identity id, recorded on receipts.

        Returns:
            dict with a "results" list, one entry per identity_id.
        """
        results = []
        for iid in identity_ids:
            try:
                results.append(
                    PrivacyService.erase_identity(
                        db,
                        iid,
                        tenant_id,
                        tenant_legal_hold,
                        requested_by_id=requested_by_id,
                    )
                )
            except (LookupError, LegalHoldError, LastAdminError) as exc:
                results.append(
                    {"identity_id": iid, "anonymized": False, "error": str(exc)}
                )
        return {"results": results}
