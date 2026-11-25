"""Portal authentication service for enterprise users.

Handles portal user registration, login, MFA, and session management
with tenant context for multi-tenancy support.
"""

# flake8: noqa: E501

import datetime
import hashlib
import hmac
import json
import math
import re
import secrets
from typing import Any, Optional

import jwt
import pyotp
import structlog
from quart import current_app
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from apps.api.common.licensing.enforce import check_limit
from apps.api.services.webhooks.user_events import (
    UserCreatedEvent,
    schedule_user_created_webhooks,
)
from apps.api.utils.mfa_crypto import (
    MfaKeyring,
    build_keyring,
    derive_subkey,
    encrypt_totp_secret,
    hash_backup_code,
    hash_challenge_id,
    is_encrypted,
    match_backup_code,
    needs_reseal,
    normalize_backup_code,
    open_totp_secret,
)
from apps.api.utils.village_id import mint_village_id
from shared.observability import get_meter, get_tracer

logger = structlog.get_logger(__name__)

_tracer = get_tracer(__name__)
_mfa_verify_counter = get_meter("elder").create_counter(
    "elder.portal_auth.mfa_verify",
    unit="1",
    description="Portal MFA verification attempts by outcome",
)

MFA_TOTP_INTERVAL_SECONDS = 30
MFA_TOTP_VALID_WINDOW = 1
MFA_BACKUP_CODE_COUNT = 10
_TOTP_CODE_RE = re.compile(r"^[0-9]{6}$")
_MFA_LOCKED_ERROR = "Too many failed MFA attempts. Try again later."
MFA_ENROLLMENT_TOKEN_TYPE = "portal_mfa_enrollment"
MFA_ENROLLMENT_TOKEN_AUDIENCE = "elder-mfa-enrollment"
_MFA_ENROLLMENT_KEY_INFO = b"elder/portal-auth/mfa-enrollment-token/hs256/v1"


def _mfa_enrollment_key() -> bytes:
    """Return the signing key reserved for MFA enrolment-only tokens.

    HKDF-derived from the JWT secret under its own label, so an enrolment
    token fails signature verification in every other token verifier.
    """
    master = current_app.config.get("JWT_SECRET_KEY") or current_app.config.get(
        "SECRET_KEY"
    )
    return derive_subkey(str(master or ""), _MFA_ENROLLMENT_KEY_INFO)


def _fingerprint_hash(fingerprint: str | None) -> str | None:
    """Hash a client fingerprint (User-Agent) for storage with a challenge.

    Only a digest is kept so the challenge table never holds raw client
    metadata; None means the client sent nothing to bind to.
    """
    if not fingerprint:
        return None
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def _record_mfa_outcome(outcome: str) -> None:
    """Count one MFA verification outcome without ever failing the request.

    ``outcome`` is a small fixed vocabulary (success, backup_code, invalid,
    replay, locked) so the metric label stays bounded.
    """
    try:
        _mfa_verify_counter.add(1, {"outcome": outcome})
    except Exception:  # pragma: no cover - telemetry must never break auth
        logger.debug("mfa_verify_metric_failed", outcome=outcome)


def _mfa_keyring() -> MfaKeyring:
    """Build the MFA keyring from the running application's configuration.

    Raises loudly (``MfaKeyUnavailableError``) when no key is configured --
    callers must not catch it and continue without MFA.
    """
    return build_keyring(current_app.config)


def _mfa_aad(user_id: Any) -> str:
    """Return the row binding used as AES-GCM associated data.

    Ties each envelope to its ``portal_users`` row so a ciphertext copied
    onto another account does not decrypt.
    """
    return f"portal_users:{int(user_id)}"


def _seal_totp_secret(user_id: Any, secret: str) -> str:
    """Encrypt a TOTP secret for storage -- the only write path for the column.

    Every writer goes through here so no call site ever stores plaintext.
    """
    return encrypt_totp_secret(secret, _mfa_keyring(), aad=_mfa_aad(user_id))


def _load_totp_secret(user: Any) -> str:
    """Return the usable TOTP secret for a user row -- the only read path.

    Decrypts the stored envelope; a pre-encryption plaintext value is passed
    through (and re-sealed on the next successful verification). Decryption
    problems raise instead of being treated as "no MFA".
    """
    stored = user.mfa_secret
    if not is_encrypted(stored):
        logger.warning("mfa_secret_legacy_plaintext_row", user_id=user.id)
        return str(stored)
    return _open_totp_secret(user.id, stored)


def _open_totp_secret(user_id: Any, stored: str) -> str:
    """Decrypt one sealed TOTP secret (active or pending) for a user row.

    Shared by the active-secret accessor and the pending-enrolment check so
    both use the same row binding.
    """
    return open_totp_secret(stored, _mfa_keyring(), aad=_mfa_aad(user_id))


def _pending_backup_codes(user: Any) -> list[Any]:
    """Return the hashed backup codes waiting on a pending enrolment.

    Tolerates the JSON column arriving as a decoded list or a JSON string.
    """
    raw = getattr(user, "mfa_pending_backup_codes", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return list(raw) if isinstance(raw, list) else []


def _match_totp_counter(secret: str, code: str, now: datetime.datetime) -> int | None:
    """Return the RFC 6238 time-step counter that ``code`` is valid for.

    Every step in the accepted window is compared in constant time with no
    early exit; the matched counter is returned so the caller can reject a
    code that was already used (replay protection).
    """
    if not _TOTP_CODE_RE.fullmatch(code):
        return None
    totp = pyotp.TOTP(secret)
    base = int(now.timestamp()) // MFA_TOTP_INTERVAL_SECONDS
    matched: int | None = None
    for offset in range(-MFA_TOTP_VALID_WINDOW, MFA_TOTP_VALID_WINDOW + 1):
        counter = base + offset
        if hmac.compare_digest(totp.generate_otp(counter), code):
            matched = counter
    return matched


def _new_backup_codes() -> tuple[list[str], list[str]]:
    """Generate a fresh backup-code set as ``(plaintext, hashed)`` lists.

    The plaintext list is shown to the user exactly once; only the hashed
    list is ever persisted.
    """
    plaintext = [secrets.token_hex(4) for _ in range(MFA_BACKUP_CODE_COUNT)]
    return plaintext, [hash_backup_code(code) for code in plaintext]


def _stored_backup_codes(user: Any) -> list[Any]:
    """Return a user's stored backup-code entries as a list.

    Tolerates the JSON column arriving as a decoded list or a JSON string.
    """
    raw = getattr(user, "mfa_backup_codes", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return list(raw) if isinstance(raw, list) else []


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
    # Second-factor attempts are budgeted separately from password attempts:
    # a correct password must not reset the TOTP guess counter.
    MAX_MFA_ATTEMPTS = 5
    MFA_LOCKOUT_DURATION_MINUTES = 30
    # Login MFA challenge: short-lived, single-use, attempt-capped.
    MFA_CHALLENGE_TTL_SECONDS = 300
    MAX_MFA_CHALLENGE_ATTEMPTS = 5
    MAX_LIVE_MFA_CHALLENGES = 5
    MFA_CHALLENGE_PRUNE_BATCH = 500
    # A started-but-unconfirmed enrolment is discarded after this long.
    MFA_PENDING_TTL_MINUTES = 10
    # Lifetime of the enrolment-only token issued once MFA grace has expired.
    MFA_ENROLLMENT_TOKEN_TTL_MINUTES = 15

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
    def _mfa_locked_response(user_id: Any) -> dict:
        """Build the lockout error dict and record the outcome.

        Kept in one place so every lockout path returns the same 429 shape
        and none of them reveals whether the submitted code was correct.
        """
        _record_mfa_outcome("locked")
        logger.warning("mfa_verify_locked", user_id=user_id)
        return {"error": _MFA_LOCKED_ERROR, "status_code": 429, "locked": True}

    @staticmethod
    def _register_mfa_failure(user_id: Any, now: datetime.datetime) -> None:
        """Lock MFA verification once the failed-attempt budget is spent.

        The attempt itself was already counted atomically before the code
        was checked; this only converts an exhausted budget into a timed lock.
        """
        db = current_app.db
        table = db.portal_users
        fresh = table[user_id]
        attempts = int(getattr(fresh, "mfa_failed_attempts", 0) or 0)
        if attempts >= PortalAuthService.MAX_MFA_ATTEMPTS:
            db(table.id == user_id).update(
                mfa_locked_until=now
                + datetime.timedelta(
                    minutes=PortalAuthService.MFA_LOCKOUT_DURATION_MINUTES
                ),
            )
            db.commit()
            logger.warning("mfa_verify_lockout_started", user_id=user_id)

    @staticmethod
    def verify_mfa(user_id: int, totp_code: str) -> dict:
        """Verify an MFA TOTP code or single-use backup code.

        Hardened second-factor check: failed attempts are budgeted and lock
        verification for a period, a TOTP code is accepted at most once
        (per-user last-accepted time step), and backup codes are compared
        against stored hashes and consumed on use.

        Args:
            user_id: Portal user ID
            totp_code: 6-digit TOTP code or 8-character backup code

        Returns:
            User dict on success, error dict on failure (``status_code`` 429
            while locked out)
        """
        with _tracer.start_as_current_span("portal_auth.verify_mfa"):
            return PortalAuthService._verify_mfa(user_id, totp_code)

    @staticmethod
    def _verify_mfa(user_id: int, totp_code: str) -> dict:
        """Run the MFA verification state machine for ``verify_mfa``.

        Split out so the public entry point stays a thin traced wrapper.
        """
        db = current_app.db
        table = db.portal_users
        user = table[user_id]
        if not user:
            return {"error": "User not found"}

        if not user.mfa_secret:
            return {"error": "MFA not enabled for this user"}

        now = datetime.datetime.now(datetime.UTC)
        locked_until = _to_utc_datetime(getattr(user, "mfa_locked_until", None))
        if locked_until is not None:
            if locked_until > now:
                return PortalAuthService._mfa_locked_response(user.id)
            # Lock expired: start a fresh attempt budget.
            db(table.id == user.id).update(mfa_locked_until=None, mfa_failed_attempts=0)
            db.commit()

        # Reserve the attempt BEFORE checking the code, in one atomic UPDATE,
        # so a burst of concurrent guesses cannot exceed the budget.
        attempts = table.mfa_failed_attempts
        reserved = db(
            (table.id == user.id)
            & (
                (attempts == None)  # noqa: E711 - SQL NULL comparison
                | (attempts < PortalAuthService.MAX_MFA_ATTEMPTS)
            )
        ).update(mfa_failed_attempts=func.coalesce(attempts.column, 0) + 1)
        db.commit()
        if reserved != 1:
            PortalAuthService._register_mfa_failure(user.id, now)
            return PortalAuthService._mfa_locked_response(user.id)

        # Raises (never falls through) if the key is missing or wrong.
        secret = _load_totp_secret(user)
        normalized_code = str(totp_code).strip()
        outcome = "invalid"

        counter = _match_totp_counter(secret, normalized_code, now)
        if counter is not None:
            updates: dict[str, Any] = {
                "mfa_last_totp_counter": counter,
                "mfa_failed_attempts": 0,
                "mfa_locked_until": None,
                "updated_at": now,
            }
            if needs_reseal(user.mfa_secret, _mfa_keyring()):
                updates["mfa_secret"] = _seal_totp_secret(user.id, secret)
            last = table.mfa_last_totp_counter
            # Conditional UPDATE: only the first use of a time step wins.
            accepted = db(
                (table.id == user.id) & ((last == None) | (last < counter))  # noqa: E711
            ).update(**updates)
            db.commit()
            if accepted == 1:
                _record_mfa_outcome("success")
                return PortalAuthService._mfa_success(user)
            outcome = "replay"
            logger.warning("mfa_totp_replay_rejected", user_id=user.id)
        else:
            backup_code = normalize_backup_code(normalized_code)
            if backup_code is not None:
                stored_codes = _stored_backup_codes(user)
                matched_idx = match_backup_code(stored_codes, backup_code)
                if matched_idx is not None:
                    remaining = (
                        stored_codes[:matched_idx] + stored_codes[matched_idx + 1 :]
                    )
                    # Optimistic guard on updated_at so two concurrent uses of
                    # the same backup code cannot both succeed.
                    consumed = db(
                        (table.id == user.id) & (table.updated_at == user.updated_at)
                    ).update(
                        mfa_backup_codes=remaining,
                        mfa_failed_attempts=0,
                        mfa_locked_until=None,
                        updated_at=now,
                    )
                    db.commit()
                    if consumed == 1:
                        _record_mfa_outcome("backup_code")
                        logger.info(
                            "mfa_backup_code_consumed",
                            user_id=user.id,
                            remaining=len(remaining),
                        )
                        return PortalAuthService._mfa_success(user)

        _record_mfa_outcome(outcome)
        PortalAuthService._register_mfa_failure(user.id, now)
        return {"error": "Invalid MFA code"}

    @staticmethod
    def _mfa_success(user: Any) -> dict:
        """Build the post-MFA user dict handed to token generation.

        An explicit field list -- never the row itself -- so MFA columns can
        not leak into a response.
        """
        return {
            "id": user.id,
            "email": user.email,
            "tenant_id": user.tenant_id,
            "tenant_role": user.tenant_role,
            "global_role": user.global_role,
            "full_name": user.full_name,
        }

    @staticmethod
    def create_mfa_challenge(
        user_id: int, tenant_id: int, fingerprint: str | None = None
    ) -> str:
        """Create a server-side MFA login challenge and return its opaque id.

        Called only after the password step succeeded, so holding a challenge
        id proves the password was presented. Only the id's hash is stored;
        the row is short-lived, single-use and attempt-capped.
        """
        db = current_app.db
        table = db.portal_mfa_challenges
        now = datetime.datetime.now(datetime.UTC)
        # Opportunistic pruning: expired rows go table-wide (in bounded
        # batches), and each user keeps at most MAX_LIVE_MFA_CHALLENGES live
        # challenges, the oldest being evicted first.
        expired_ids = [
            r.id
            for r in db(table.expires_at < now).select(
                table.id,
                limitby=(0, PortalAuthService.MFA_CHALLENGE_PRUNE_BATCH),
            )
        ]
        if expired_ids:
            db(table.id.belongs(expired_ids)).delete()
        live_ids = [
            r.id
            for r in db(table.portal_user_id == user_id).select(
                table.id, orderby=~table.id
            )
        ]
        evict = live_ids[PortalAuthService.MAX_LIVE_MFA_CHALLENGES - 1 :]
        if evict:
            db(table.id.belongs(evict)).delete()
        challenge_id = secrets.token_urlsafe(32)
        table.insert(
            challenge_hash=hash_challenge_id(challenge_id),
            portal_user_id=user_id,
            tenant_id=tenant_id,
            fingerprint_hash=_fingerprint_hash(fingerprint),
            attempts=0,
            expires_at=now
            + datetime.timedelta(seconds=PortalAuthService.MFA_CHALLENGE_TTL_SECONDS),
            created_at=now,
        )
        db.commit()
        return challenge_id

    @staticmethod
    def verify_mfa_challenge(
        challenge_id: Any, totp_code: str, fingerprint: str | None = None
    ) -> dict:
        """Complete the MFA step of a login using a server-issued challenge.

        The challenge -- not a caller-supplied user id -- selects the account,
        so nobody can reach a user's second factor (or its lockout counter)
        without first presenting that user's password. A challenge is
        consumed on success and dies after a fixed number of attempts.

        Returns:
            User dict on success; error dict otherwise (``challenge_invalid``
            when the challenge can no longer be used, ``status_code`` 429
            while the account's MFA step is locked).
        """
        invalid = {
            "error": "Invalid or expired MFA challenge",
            "status_code": 401,
            "challenge_invalid": True,
        }
        if not isinstance(challenge_id, str) or not challenge_id.strip():
            return invalid

        db = current_app.db
        table = db.portal_mfa_challenges
        now = datetime.datetime.now(datetime.UTC)
        row = (
            db(table.challenge_hash == hash_challenge_id(challenge_id.strip()))
            .select()
            .first()
        )
        if not row or row.consumed_at is not None:
            return invalid
        expires_at = _to_utc_datetime(row.expires_at)
        if expires_at is None or expires_at <= now:
            return invalid
        if row.fingerprint_hash and not hmac.compare_digest(
            row.fingerprint_hash, _fingerprint_hash(fingerprint) or ""
        ):
            logger.warning(
                "mfa_challenge_fingerprint_mismatch", user_id=row.portal_user_id
            )
            return invalid

        user = db.portal_users[row.portal_user_id]
        if not user or user.tenant_id != row.tenant_id or not user.is_active:
            return invalid

        # Atomically take one of the challenge's attempts before checking.
        reserved = db(
            (table.id == row.id)
            & (table.consumed_at == None)  # noqa: E711 - SQL NULL comparison
            & (table.attempts < PortalAuthService.MAX_MFA_CHALLENGE_ATTEMPTS)
        ).update(attempts=table.attempts.column + 1)
        db.commit()
        if reserved != 1:
            return invalid

        result = PortalAuthService.verify_mfa(row.portal_user_id, totp_code)
        if "error" in result:
            if (
                int(row.attempts or 0) + 1
                >= PortalAuthService.MAX_MFA_CHALLENGE_ATTEMPTS
            ):
                return {**result, "challenge_invalid": True}
            return result

        consumed = db(
            (table.id == row.id) & (table.consumed_at == None)  # noqa: E711
        ).update(consumed_at=now)
        db.commit()
        if consumed != 1:
            return invalid
        return result

    @staticmethod
    def issue_mfa_enrollment_token(user: dict) -> str:
        """Mint the enrolment-only token for a user whose MFA grace has expired.

        Signed with a key derived solely for this purpose, so no other token
        verifier in the application can accept it: it authorises MFA setup
        and logout, nothing else.
        """
        now = datetime.datetime.now(datetime.UTC)
        payload = {
            "sub": str(user["id"]),
            "tenant_id": user["tenant_id"],
            "type": MFA_ENROLLMENT_TOKEN_TYPE,
            "aud": MFA_ENROLLMENT_TOKEN_AUDIENCE,
            "scope": [],
            "iat": now,
            "exp": now
            + datetime.timedelta(
                minutes=PortalAuthService.MFA_ENROLLMENT_TOKEN_TTL_MINUTES
            ),
        }
        return jwt.encode(payload, _mfa_enrollment_key(), algorithm="HS256")

    @staticmethod
    def verify_mfa_enrollment_token(token: str) -> dict | None:
        """Return the claims of a valid enrolment-only token, else None.

        Rejects anything not signed with the dedicated enrolment key, which
        includes every ordinary access or refresh token.
        """
        try:
            payload = jwt.decode(
                token,
                _mfa_enrollment_key(),
                algorithms=["HS256"],
                audience=MFA_ENROLLMENT_TOKEN_AUDIENCE,
            )
        except jwt.InvalidTokenError:
            return None
        if payload.get("type") != MFA_ENROLLMENT_TOKEN_TYPE:
            return None
        return payload

    @staticmethod
    def _require_current_factor(user: Any, current_code: Any) -> dict | None:
        """Demand proof of the existing second factor before changing it.

        Returns None when ``current_code`` is a valid TOTP or backup code for
        the user's active MFA (it is consumed, and lockout applies), else an
        error dict. A session alone must never be enough to replace, remove
        or mint MFA credentials.
        """
        if current_code is None or not str(current_code).strip():
            return {
                "error": "A current MFA code is required for this action",
                "status_code": 403,
                "mfa_code_required": True,
            }
        result = PortalAuthService.verify_mfa(user.id, str(current_code))
        if "error" in result:
            return {
                "error": result["error"],
                "status_code": result.get("status_code", 403),
                "mfa_code_required": True,
            }
        return None

    @staticmethod
    def enable_mfa(user_id: int, current_code: Any = None) -> dict:
        """Start MFA enrollment: generate a PENDING TOTP secret and backup codes.

        Nothing takes effect until ``confirm_mfa`` proves the authenticator
        works; an abandoned setup expires. If MFA is already enrolled, a
        current valid TOTP or backup code is required first. The secret is
        stored encrypted and the backup codes hashed; the plaintext values
        are returned only in this setup response.

        Args:
            user_id: Portal user ID
            current_code: Current TOTP/backup code (required when re-enrolling)

        Returns:
            MFA setup info (secret, provisioning URI, backup codes), or an
            error dict with ``status_code``
        """
        user = current_app.db.portal_users[user_id]
        if not user:
            return {"error": "User not found"}

        if user.mfa_secret:
            denied = PortalAuthService._require_current_factor(user, current_code)
            if denied is not None:
                return denied

        # Generate new TOTP secret
        secret = pyotp.random_base32()

        # Generate backup codes (plaintext shown once, hashes persisted)
        backup_codes, hashed_backup_codes = _new_backup_codes()

        now = datetime.datetime.now(datetime.UTC)
        ttl = datetime.timedelta(minutes=PortalAuthService.MFA_PENDING_TTL_MINUTES)
        current_app.db(current_app.db.portal_users.id == user.id).update(
            mfa_pending_secret=_seal_totp_secret(user.id, secret),
            mfa_pending_backup_codes=hashed_backup_codes,
            mfa_pending_expires_at=now + ttl,
            updated_at=now,
        )
        current_app.db.commit()

        # Generate provisioning URI for authenticator apps
        totp = pyotp.TOTP(secret)
        provisioning_uri = totp.provisioning_uri(name=user.email, issuer_name="Elder")

        return {
            "secret": secret,
            "provisioning_uri": provisioning_uri,
            "backup_codes": backup_codes,
            "pending": True,
            "expires_in": int(ttl.total_seconds()),
        }

    @staticmethod
    def confirm_mfa(user_id: int, totp_code: str) -> dict:
        """Confirm MFA enrollment by validating a TOTP code against the user's secret.

        When an enrolment is pending, a valid code promotes the pending
        secret and backup codes to active (replacing any previous ones) and
        records the used time step so the same code cannot then be replayed
        at login. An expired pending enrolment is discarded.

        Args:
            user_id: Portal user ID
            totp_code: 6-digit TOTP code from Google Authenticator

        Returns:
            ``{"verified": True}`` on success, or an error dict with ``status_code``
        """
        db = current_app.db
        table = db.portal_users
        user = table[user_id]
        if not user:
            return {"error": "User not found", "status_code": 404}

        now = datetime.datetime.now(datetime.UTC)
        normalized_code = str(totp_code).strip()
        pending = getattr(user, "mfa_pending_secret", None)

        if pending:
            expires_at = _to_utc_datetime(getattr(user, "mfa_pending_expires_at", None))
            if expires_at is None or expires_at <= now:
                db(table.id == user.id).update(
                    mfa_pending_secret=None,
                    mfa_pending_backup_codes=None,
                    mfa_pending_expires_at=None,
                )
                db.commit()
                return {
                    "error": "MFA setup expired. Start the setup again.",
                    "status_code": 400,
                    "expired": True,
                }

            counter = _match_totp_counter(
                _open_totp_secret(user.id, pending), normalized_code, now
            )
            if counter is None:
                return {"error": "Invalid verification code", "status_code": 400}

            # Promote only if the pending secret is still the one we checked.
            promoted = db(
                (table.id == user.id) & (table.mfa_pending_secret == pending)
            ).update(
                mfa_secret=pending,
                mfa_backup_codes=_pending_backup_codes(user),
                mfa_pending_secret=None,
                mfa_pending_backup_codes=None,
                mfa_pending_expires_at=None,
                mfa_last_totp_counter=counter,
                mfa_failed_attempts=0,
                mfa_locked_until=None,
                updated_at=now,
            )
            db.commit()
            if promoted != 1:
                return {
                    "error": "MFA setup changed. Start the setup again.",
                    "status_code": 409,
                }
            logger.info("mfa_enrollment_confirmed", user_id=user.id)
            return {"verified": True, "enrolled": True}

        if not user.mfa_secret:
            return {
                "error": "MFA has not been initialized for this user",
                "status_code": 400,
            }

        # No pending enrolment: this checks the ACTIVE factor, so it goes
        # through the hardened path (attempt budget, lockout, replay
        # tracking) -- otherwise it would be an unthrottled code oracle.
        result = PortalAuthService.verify_mfa(user.id, normalized_code)
        if "error" in result:
            return {
                "error": result["error"],
                "status_code": result.get("status_code", 400),
            }

        return {"verified": True}

    @staticmethod
    def regenerate_backup_codes(user_id: int, current_code: Any = None) -> dict:
        """Generate a fresh set of 10 single-use TOTP bypass/backup codes.

        Replaces any previously issued backup codes for the user. Requires a
        current valid TOTP or backup code -- backup codes are MFA bypass
        credentials, so a session alone must not be able to mint them. Only
        hashes are stored; the plaintext codes exist in this response alone.

        Args:
            user_id: Portal user ID
            current_code: Current TOTP or backup code

        Returns:
            Dict with ``backup_codes`` and ``remaining``, or an error dict
            with ``status_code``.
        """
        user = current_app.db.portal_users[user_id]
        if not user:
            return {"error": "User not found", "status_code": 404}

        if not user.mfa_secret:
            return {
                "error": "MFA must be enabled before regenerating backup codes",
                "status_code": 400,
            }

        denied = PortalAuthService._require_current_factor(user, current_code)
        if denied is not None:
            return denied

        backup_codes, hashed_backup_codes = _new_backup_codes()
        current_app.db(current_app.db.portal_users.id == user.id).update(
            mfa_backup_codes=hashed_backup_codes,
            updated_at=datetime.datetime.now(datetime.UTC),
        )
        current_app.db.commit()

        return {
            "backup_codes": backup_codes,
            "remaining": len(backup_codes),
        }

    @staticmethod
    def disable_mfa(user_id: int, current_code: Any = None) -> dict:
        """Disable MFA for a user.

        Refuses with HTTP 403 if the user's tenant enforces MFA via its
        Enterprise security policy, and otherwise requires a current valid
        TOTP or backup code so a hijacked session cannot strip the factor.

        Args:
            user_id: Portal user ID
            current_code: Current TOTP or backup code

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

        if user.mfa_secret:
            denied = PortalAuthService._require_current_factor(user, current_code)
            if denied is not None:
                return denied

        current_app.db(current_app.db.portal_users.id == user.id).update(
            mfa_secret=None,
            mfa_backup_codes=None,
            mfa_pending_secret=None,
            mfa_pending_backup_codes=None,
            mfa_pending_expires_at=None,
            mfa_last_totp_counter=None,
            mfa_failed_attempts=0,
            mfa_locked_until=None,
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
