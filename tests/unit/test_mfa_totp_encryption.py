"""Unit tests for MFA at-rest hardening (sealed TOTP secrets, hashed backup codes).

Covers:
- the envelope format (version prefix, key id, round trip, AAD row binding)
- loud failure on a missing/wrong key and on tampered or transplanted ciphertext
- key rotation via MFA_ENCRYPTION_OLD_KEYS and lazy re-seal
- service accessors against a REAL penguin-dal database (Postgres when
  DATABASE_URL is set, a throwaway SQLite file otherwise): encrypt on write,
  decrypt on read, legacy plaintext rows transparently re-sealed
- TOTP replay rejection (last accepted time step) and second-factor lockout
- backup codes stored hashed and single-use
- the secret never appearing in API responses after setup, logs, or repr
- Alembic revision 056 upgrade / idempotent re-run / downgrade
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import jwt
import penguin_dal
import pyotp
import pytest
import pytest_asyncio
import sqlalchemy as sa
import structlog
from quart import Quart
from werkzeug.security import generate_password_hash

if not hasattr(penguin_dal, "DAL") and hasattr(penguin_dal, "DB"):
    penguin_dal.DAL = penguin_dal.DB  # type: ignore[attr-defined]

try:
    import shared.observability  # noqa: F401
except ImportError:
    from unittest.mock import MagicMock

    _obs_stub = ModuleType("shared.observability")
    _obs_stub.get_meter = lambda *_a, **_kw: MagicMock()  # type: ignore[attr-defined]
    _obs_stub.get_tracer = lambda *_a, **_kw: MagicMock()  # type: ignore[attr-defined]
    sys.modules["shared.observability"] = _obs_stub

from apps.api.api.v1.portal_auth import bp as portal_auth_bp
from apps.api.models.base import Base
from apps.api.models.tenant import (
    PortalMfaChallenge,
    PortalUser,
    PortalUserOrgAssignment,
    Tenant,
)
from apps.api.services.portal_auth import PortalAuthService
from apps.api.services.portal_auth import service as portal_service
from apps.api.utils import mfa_crypto
from apps.api.utils.mfa_crypto import (
    ENVELOPE_PREFIX,
    MfaCryptoError,
    MfaDecryptionError,
    MfaKeyUnavailableError,
    build_keyring,
    decrypt_totp_secret,
    encrypt_totp_secret,
    hash_backup_code,
    is_encrypted,
    is_hashed_backup_code,
    match_backup_code,
    needs_reseal,
    normalize_backup_code,
    open_totp_secret,
)

APP_SECRET = "unit-test-app-secret-key-at-least-32-bytes"
OTHER_SECRET = "a-completely-different-master-secret-value"
PASSWORD = "correct horse battery staple"
MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "056_mfa_secret_at_rest_hardening.py"
)


def _ring(secret: str = APP_SECRET, **extra: str) -> mfa_crypto.MfaKeyring:
    """Build a keyring from a bare SECRET_KEY (plus optional overrides)."""
    return build_keyring({"SECRET_KEY": secret, **extra})


class TestEnvelope:
    """Envelope format, authentication and key selection."""

    def test_envelope_is_versioned_and_not_plaintext(self) -> None:
        """A sealed secret carries the v1 prefix + kid and hides the plaintext."""
        secret = pyotp.random_base32()
        ring = _ring()
        sealed = encrypt_totp_secret(secret, ring, aad="portal_users:1")

        assert sealed.startswith(ENVELOPE_PREFIX)
        version, kid, nonce, ciphertext = sealed.split(":")
        assert version == "v1"
        assert kid == ring.current_kid
        assert nonce and ciphertext
        assert secret not in sealed
        assert len(sealed) <= 255  # fits portal_users.mfa_secret String(255)
        assert is_encrypted(sealed)
        assert not is_encrypted(secret)

    def test_round_trip_and_fresh_nonce(self) -> None:
        """Decrypt returns the secret; two seals of one secret differ."""
        secret = pyotp.random_base32()
        ring = _ring()
        first = encrypt_totp_secret(secret, ring, aad="portal_users:7")
        second = encrypt_totp_secret(secret, ring, aad="portal_users:7")

        assert first != second
        assert decrypt_totp_secret(first, ring, aad="portal_users:7") == secret
        assert open_totp_secret(second, ring, aad="portal_users:7") == secret

    def test_wrong_key_fails_loudly(self) -> None:
        """A keyring without the row's kid raises instead of returning garbage."""
        sealed = encrypt_totp_secret("JBSWY3DPEHPK3PXP", _ring(), aad="portal_users:1")
        with pytest.raises(MfaKeyUnavailableError):
            decrypt_totp_secret(sealed, _ring(OTHER_SECRET), aad="portal_users:1")

    def test_wrong_key_with_forged_kid_fails_authentication(self) -> None:
        """Relabelling an envelope with another key's kid fails the GCM tag."""
        sealed = encrypt_totp_secret("JBSWY3DPEHPK3PXP", _ring(), aad="portal_users:1")
        other = _ring(OTHER_SECRET)
        _, _, nonce, ciphertext = sealed.split(":")
        forged = f"v1:{other.current_kid}:{nonce}:{ciphertext}"
        with pytest.raises(MfaDecryptionError):
            decrypt_totp_secret(forged, other, aad="portal_users:1")

    def test_aad_binds_envelope_to_row(self) -> None:
        """Ciphertext copied to another row id does not decrypt."""
        ring = _ring()
        sealed = encrypt_totp_secret("JBSWY3DPEHPK3PXP", ring, aad="portal_users:1")
        with pytest.raises(MfaDecryptionError):
            decrypt_totp_secret(sealed, ring, aad="portal_users:2")

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda s: s[:-2] + ("AA" if not s.endswith("AA") else "BB"),
            lambda s: s.rsplit(":", 1)[0],
            lambda s: s.replace("v1:", "v1:x", 1),
            lambda s: s.rsplit(":", 1)[0] + ":!!!not-base64!!!",
        ],
    )
    def test_tampered_or_malformed_envelope_raises(self, mutate: Any) -> None:
        """Any corruption surfaces as an MfaCryptoError, never as a value."""
        ring = _ring()
        sealed = encrypt_totp_secret("JBSWY3DPEHPK3PXP", ring, aad="portal_users:1")
        with pytest.raises(MfaCryptoError):
            decrypt_totp_secret(mutate(sealed), ring, aad="portal_users:1")

    @pytest.mark.parametrize("config", [{}, {"SECRET_KEY": ""}, {"SECRET_KEY": "  "}])
    def test_missing_key_raises(self, config: dict[str, str]) -> None:
        """No configured master secret is a hard error."""
        with pytest.raises(MfaKeyUnavailableError):
            build_keyring(config)

    def test_empty_secret_is_refused(self) -> None:
        """Sealing an empty secret is refused rather than stored."""
        with pytest.raises(MfaCryptoError):
            encrypt_totp_secret("", _ring(), aad="portal_users:1")

    def test_dedicated_key_takes_precedence_and_is_derived(self) -> None:
        """MFA_ENCRYPTION_KEY wins over SECRET_KEY; the AES key is HKDF output."""
        dedicated = build_keyring(
            {"SECRET_KEY": APP_SECRET, "MFA_ENCRYPTION_KEY": OTHER_SECRET}
        )
        assert dedicated.current_kid == _ring(OTHER_SECRET).current_kid
        assert dedicated.current_kid != _ring().current_kid
        key = _ring().current_key()
        assert len(key) == 32
        assert key != APP_SECRET.encode()[:32]

    def test_rotation_old_key_still_decrypts_and_flags_reseal(self) -> None:
        """After rotation the old key opens old rows, which are due a re-seal."""
        old_ring = _ring()
        sealed = encrypt_totp_secret("JBSWY3DPEHPK3PXP", old_ring, aad="portal_users:1")
        rotated = build_keyring(
            {
                "MFA_ENCRYPTION_KEY": OTHER_SECRET,
                "MFA_ENCRYPTION_OLD_KEYS": f" {APP_SECRET} ,",
            }
        )

        assert decrypt_totp_secret(sealed, rotated, aad="portal_users:1") == (
            "JBSWY3DPEHPK3PXP"
        )
        assert needs_reseal(sealed, rotated)
        assert not needs_reseal(sealed, old_ring)
        assert needs_reseal("JBSWY3DPEHPK3PXP", old_ring)

    def test_keyring_repr_hides_key_material(self) -> None:
        """repr() of a keyring exposes the kid only."""
        ring = _ring()
        text = repr(ring)
        assert ring.current_kid in text
        assert repr(ring.current_key()) not in text
        assert ring.current_key().hex() not in text
        assert APP_SECRET not in text


class TestBackupCodeHashing:
    """Backup codes are hashed, shape-gated and matched safely."""

    def test_hash_is_salted_and_not_plaintext(self) -> None:
        """Two hashes of one code differ and neither contains the code."""
        first = hash_backup_code("deadbeef")
        second = hash_backup_code("deadbeef")
        assert first != second
        assert is_hashed_backup_code(first)
        assert "deadbeef" not in first
        assert not is_hashed_backup_code("deadbeef")

    def test_match_hashed_and_legacy_entries(self) -> None:
        """Hashed and legacy plaintext entries both match; others do not."""
        stored = [
            hash_backup_code("aaaaaaaa"),
            "bbbbbbbb",
            hash_backup_code("cccccccc"),
        ]
        assert match_backup_code(stored, "aaaaaaaa") == 0
        assert match_backup_code(stored, "bbbbbbbb") == 1
        assert match_backup_code(stored, "cccccccc") == 2
        assert match_backup_code(stored, "dddddddd") is None
        assert match_backup_code([], "aaaaaaaa") is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("DEADBEEF", "deadbeef"),
            ("  deadbeef ", "deadbeef"),
            ("123456", None),
            ("deadbee", None),
            ("deadbeefs", None),
            ("zzzzzzzz", None),
        ],
    )
    def test_normalize_backup_code(self, raw: str, expected: str | None) -> None:
        """Only 8-hex-character input is treated as a backup code."""
        assert normalize_backup_code(raw) == expected


@pytest.fixture(scope="module")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Return a real database URL with the tenant/portal-user tables present.

    Uses DATABASE_URL (Postgres) when set, else a throwaway SQLite file, so
    the accessor layer is always exercised against a real penguin-dal DB.
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        url = f"sqlite:///{tmp_path_factory.mktemp('mfa') / 'mfa.db'}"
    engine = sa.create_engine(url)
    Base.metadata.create_all(
        engine,
        tables=[
            Tenant.__table__,
            PortalUser.__table__,
            PortalUserOrgAssignment.__table__,
            PortalMfaChallenge.__table__,
        ],
    )
    engine.dispose()
    return url


@pytest.fixture
def mfa_app(database_url: str) -> Iterator[Quart]:
    """Build a minimal Quart app with the portal-auth blueprint and a real DAL."""
    app = Quart(__name__)
    app.config["JWT_SECRET_KEY"] = APP_SECRET
    app.config["SECRET_KEY"] = APP_SECRET
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=1)
    app.config["JWT_REFRESH_TOKEN_EXPIRES"] = timedelta(days=7)
    app.config["DEBUG"] = True
    app.db = penguin_dal.DAL(database_url, pool_size=2, migrate=False)  # type: ignore[attr-defined]
    app.register_blueprint(portal_auth_bp, url_prefix="/api/v1/portal-auth")
    yield app
    app.db.close()  # type: ignore[attr-defined]


@pytest_asyncio.fixture
async def ctx(mfa_app: Quart) -> AsyncIterator[Quart]:
    """Enter the application context so ``current_app.db`` resolves."""
    async with mfa_app.app_context():
        yield mfa_app


def _seed_user(app: Quart, **overrides: Any) -> Any:
    """Insert a tenant + portal user and return the fresh user row."""
    db = app.db  # type: ignore[attr-defined]
    tag = uuid.uuid4().hex[:12]
    tenant_id = db.tenants.insert(name=f"T {tag}", slug=f"t-{tag}", is_active=True)
    fields: dict[str, Any] = {
        "tenant_id": tenant_id,
        "email": f"user-{tag}@example.com",
        "password_hash": generate_password_hash(PASSWORD),
        "tenant_role": "admin",
        "is_active": True,
        "email_verified": True,
        "failed_login_attempts": 0,
        "updated_at": datetime.now(UTC),
    }
    fields.update(overrides)
    user_id = db.portal_users.insert(**fields)
    db.commit()
    return db.portal_users[user_id]


def _row(app: Quart, user_id: int) -> Any:
    """Re-read a portal user row."""
    return app.db.portal_users[user_id]  # type: ignore[attr-defined]


def _codes(row: Any) -> list[str]:
    """Return a row's stored backup-code entries as a list."""
    raw = row.mfa_backup_codes
    return json.loads(raw) if isinstance(raw, str) else list(raw or [])


def _code(secret: str, offset_steps: int = 0) -> str:
    """Return the TOTP code for now (+/- whole 30s steps)."""
    return pyotp.TOTP(secret).at(
        datetime.now(UTC) + timedelta(seconds=30 * offset_steps)
    )


def _enroll(app: Quart, user_id: int) -> dict[str, Any]:
    """Enrol a user in MFA (enable + confirm) and return the setup material.

    The last-accepted time step recorded by the confirmation is cleared so
    each test can start from a code for the current step; the replay rule
    for the confirmation code itself has its own test.
    """
    setup = PortalAuthService.enable_mfa(user_id)
    confirmed = PortalAuthService.confirm_mfa(user_id, _code(setup["secret"]))
    assert confirmed == {"verified": True, "enrolled": True}
    app.db(app.db.portal_users.id == user_id).update(  # type: ignore[attr-defined]
        mfa_last_totp_counter=None
    )
    return setup


def _token(app: Quart, user: Any) -> str:
    """Mint a portal access token for ``user``."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "tenant_id": user.tenant_id,
        "tenant_role": "admin",
        "global_role": None,
        "scope": "",
        "type": "portal_user",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    return jwt.encode(payload, app.config["JWT_SECRET_KEY"], algorithm="HS256")


@pytest.mark.asyncio
class TestServiceAccessors:
    """Encrypt-on-write / decrypt-on-read through the service layer."""

    async def test_confirm_on_active_factor_is_attempt_capped(self, ctx: Quart) -> None:
        """confirm_mfa without a pending enrolment cannot brute-force the code."""
        user = _seed_user(ctx)
        secret = _enroll(ctx, user.id)["secret"]
        good = _code(secret)
        wrong = "000000" if good != "000000" else "111111"

        for _ in range(PortalAuthService.MAX_MFA_ATTEMPTS):
            assert "error" in PortalAuthService.confirm_mfa(user.id, wrong)

        locked = PortalAuthService.confirm_mfa(user.id, good)
        assert locked["status_code"] == 429
        assert "verified" not in locked

    async def test_round_trip_verify_and_confirm(self, ctx: Quart) -> None:
        """A code from the setup secret verifies against the sealed row."""
        user = _seed_user(ctx)
        secret = _enroll(ctx, user.id)["secret"]

        assert PortalAuthService.confirm_mfa(user.id, _code(secret)) == {
            "verified": True
        }
        assert PortalAuthService.confirm_mfa(user.id, "000000")["status_code"] == 400
        # Confirming against the active factor consumes the time step too.
        assert "error" in PortalAuthService.verify_mfa(user.id, _code(secret))
        ctx.db(ctx.db.portal_users.id == user.id).update(  # type: ignore[attr-defined]
            mfa_last_totp_counter=None
        )

        result = PortalAuthService.verify_mfa(user.id, _code(secret))
        assert result["id"] == user.id
        assert set(result) == {
            "id",
            "email",
            "tenant_id",
            "tenant_role",
            "global_role",
            "full_name",
        }
        assert _row(ctx, user.id).mfa_last_totp_counter is not None

    async def test_replayed_code_is_rejected(self, ctx: Quart) -> None:
        """A code is accepted once; reuse and older steps fail, newer pass."""
        user = _seed_user(ctx)
        secret = _enroll(ctx, user.id)["secret"]
        code = _code(secret)

        assert "error" not in PortalAuthService.verify_mfa(user.id, code)
        assert PortalAuthService.verify_mfa(user.id, code) == {
            "error": "Invalid MFA code"
        }
        # The next time step is strictly newer, so it is accepted...
        assert "error" not in PortalAuthService.verify_mfa(user.id, _code(secret, 1))
        # ...after which the current step (older counter) is still refused.
        assert "error" in PortalAuthService.verify_mfa(user.id, _code(secret))

    async def test_lockout_after_max_failures(self, ctx: Quart) -> None:
        """N wrong codes lock verification; even a correct code is then refused."""
        user = _seed_user(ctx)
        secret = _enroll(ctx, user.id)["secret"]

        for _ in range(PortalAuthService.MAX_MFA_ATTEMPTS):
            assert PortalAuthService.verify_mfa(user.id, "000000") == {
                "error": "Invalid MFA code"
            }

        row = _row(ctx, user.id)
        assert row.mfa_failed_attempts == PortalAuthService.MAX_MFA_ATTEMPTS
        assert row.mfa_locked_until is not None

        locked = PortalAuthService.verify_mfa(user.id, _code(secret))
        assert locked["status_code"] == 429
        assert locked["locked"] is True
        assert _row(ctx, user.id).mfa_last_totp_counter is None

        # Once the lock expires a fresh budget is granted.
        ctx.db(ctx.db.portal_users.id == user.id).update(  # type: ignore[attr-defined]
            mfa_locked_until=datetime.now(UTC) - timedelta(seconds=1)
        )
        assert "error" not in PortalAuthService.verify_mfa(user.id, _code(secret))
        row = _row(ctx, user.id)
        assert row.mfa_failed_attempts == 0
        assert row.mfa_locked_until is None

    async def test_exhausted_budget_without_lock_timestamp_still_locks(
        self, ctx: Quart
    ) -> None:
        """A spent budget with no lock recorded yet is refused and then locked."""
        user = _seed_user(ctx)
        secret = _enroll(ctx, user.id)["secret"]
        ctx.db(ctx.db.portal_users.id == user.id).update(  # type: ignore[attr-defined]
            mfa_failed_attempts=PortalAuthService.MAX_MFA_ATTEMPTS
        )

        assert (
            PortalAuthService.verify_mfa(user.id, _code(secret))["status_code"] == 429
        )
        assert _row(ctx, user.id).mfa_locked_until is not None

    async def test_password_login_does_not_reset_mfa_budget(self, ctx: Quart) -> None:
        """A correct password must not hand out more second-factor guesses."""
        user = _seed_user(ctx)
        _enroll(ctx, user.id)
        for _ in range(3):
            PortalAuthService.verify_mfa(user.id, "000000")

        login = PortalAuthService.authenticate(user.tenant_id, user.email, PASSWORD)
        assert login["mfa_required"] is True
        assert _row(ctx, user.id).mfa_failed_attempts == 3

    async def test_success_resets_failure_budget(self, ctx: Quart) -> None:
        """A valid code clears earlier failures."""
        user = _seed_user(ctx)
        secret = _enroll(ctx, user.id)["secret"]
        PortalAuthService.verify_mfa(user.id, "000000")
        assert _row(ctx, user.id).mfa_failed_attempts == 1
        assert "error" not in PortalAuthService.verify_mfa(user.id, _code(secret))
        assert _row(ctx, user.id).mfa_failed_attempts == 0

    async def test_legacy_plaintext_row_is_transparently_resealed(
        self, ctx: Quart
    ) -> None:
        """A pre-encryption row still verifies and is sealed on first success."""
        secret = pyotp.random_base32()
        user = _seed_user(ctx, mfa_secret=secret, mfa_backup_codes=["0a1b2c3d"])
        assert _row(ctx, user.id).mfa_secret == secret

        assert "error" not in PortalAuthService.verify_mfa(user.id, _code(secret))

        row = _row(ctx, user.id)
        assert row.mfa_secret.startswith(ENVELOPE_PREFIX)
        assert secret not in row.mfa_secret
        assert (
            decrypt_totp_secret(
                row.mfa_secret, build_keyring(ctx.config), aad=f"portal_users:{user.id}"
            )
            == secret
        )
        # Sealed row keeps working on the next time step.
        assert "error" not in PortalAuthService.verify_mfa(user.id, _code(secret, 1))

    async def test_rotated_key_row_is_resealed_under_current_key(
        self, ctx: Quart
    ) -> None:
        """After rotation an old-key row verifies and moves to the new kid."""
        user = _seed_user(ctx)
        secret = _enroll(ctx, user.id)["secret"]
        old_kid = _row(ctx, user.id).mfa_secret.split(":")[1]

        ctx.config["MFA_ENCRYPTION_KEY"] = OTHER_SECRET
        ctx.config["MFA_ENCRYPTION_OLD_KEYS"] = APP_SECRET
        assert "error" not in PortalAuthService.verify_mfa(user.id, _code(secret))

        new_kid = _row(ctx, user.id).mfa_secret.split(":")[1]
        assert new_kid != old_kid
        assert new_kid == build_keyring(ctx.config).current_kid

    async def test_wrong_key_is_loud_not_a_bypass(self, ctx: Quart) -> None:
        """With the wrong key verification raises; login still demands MFA."""
        user = _seed_user(ctx)
        secret = _enroll(ctx, user.id)["secret"]
        ctx.config["SECRET_KEY"] = OTHER_SECRET

        with pytest.raises(MfaKeyUnavailableError):
            PortalAuthService.verify_mfa(user.id, _code(secret))
        with pytest.raises(MfaKeyUnavailableError):
            PortalAuthService.confirm_mfa(user.id, _code(secret))

        login = PortalAuthService.authenticate(user.tenant_id, user.email, PASSWORD)
        assert login == {
            "mfa_required": True,
            "user_id": user.id,
            "tenant_id": user.tenant_id,
        }

    async def test_missing_key_is_loud(self, ctx: Quart) -> None:
        """Enrollment refuses to store anything when no key is configured."""
        user = _seed_user(ctx)
        ctx.config["SECRET_KEY"] = ""
        with pytest.raises(MfaKeyUnavailableError):
            PortalAuthService.enable_mfa(user.id)
        assert _row(ctx, user.id).mfa_secret is None

    async def test_transplanted_ciphertext_is_rejected(self, ctx: Quart) -> None:
        """Another user's envelope copied onto a row fails authentication."""
        attacker = _seed_user(ctx)
        victim = _seed_user(ctx)
        attacker_secret = _enroll(ctx, attacker.id)["secret"]
        _enroll(ctx, victim.id)
        ctx.db(ctx.db.portal_users.id == victim.id).update(  # type: ignore[attr-defined]
            mfa_secret=_row(ctx, attacker.id).mfa_secret
        )

        with pytest.raises(MfaDecryptionError):
            PortalAuthService.verify_mfa(victim.id, _code(attacker_secret))

    async def test_backup_code_is_single_use(self, ctx: Quart) -> None:
        """A backup code signs in once and is then gone."""
        user = _seed_user(ctx)
        setup = _enroll(ctx, user.id)
        code = setup["backup_codes"][3]

        assert PortalAuthService.verify_mfa(user.id, code.upper())["id"] == user.id
        assert len(_codes(_row(ctx, user.id))) == 9
        assert PortalAuthService.verify_mfa(user.id, code) == {
            "error": "Invalid MFA code"
        }
        assert len(_codes(_row(ctx, user.id))) == 9

    async def test_backup_code_with_null_updated_at_and_legacy_entry(
        self, ctx: Quart
    ) -> None:
        """Legacy plaintext entries on a row with no updated_at are consumable."""
        secret = pyotp.random_base32()
        user = _seed_user(
            ctx,
            mfa_secret=secret,
            mfa_backup_codes=["0a1b2c3d", "11112222"],
            updated_at=None,
        )
        assert "error" not in PortalAuthService.verify_mfa(user.id, "0a1b2c3d")
        assert _codes(_row(ctx, user.id)) == ["11112222"]

    async def test_regenerate_stores_hashes_and_invalidates_old(
        self, ctx: Quart
    ) -> None:
        """Regenerated codes are hashed; the previous set stops working."""
        user = _seed_user(ctx)
        setup = _enroll(ctx, user.id)
        old_codes = setup["backup_codes"]
        result = PortalAuthService.regenerate_backup_codes(
            user.id, _code(setup["secret"])
        )

        stored = _codes(_row(ctx, user.id))
        assert result["remaining"] == 10
        assert all(is_hashed_backup_code(c) for c in stored)
        assert not set(stored) & set(result["backup_codes"])
        assert "error" in PortalAuthService.verify_mfa(user.id, old_codes[0])
        assert "error" not in PortalAuthService.verify_mfa(
            user.id, result["backup_codes"][0]
        )

    async def test_disable_clears_all_mfa_state(self, ctx: Quart) -> None:
        """Disabling MFA removes the envelope, codes and counters."""
        user = _seed_user(ctx)
        secret = _enroll(ctx, user.id)["secret"]
        PortalAuthService.verify_mfa(user.id, _code(secret))

        assert PortalAuthService.disable_mfa(user.id, _code(secret, 1)) == {
            "success": True
        }
        row = _row(ctx, user.id)
        assert row.mfa_secret is None
        assert row.mfa_backup_codes is None
        assert row.mfa_last_totp_counter is None
        assert row.mfa_locked_until is None

    async def test_unknown_user_and_unenrolled_user(self, ctx: Quart) -> None:
        """Verification of a missing or unenrolled user is a plain error."""
        user = _seed_user(ctx)
        assert PortalAuthService.verify_mfa(2_000_000_000, "123456") == {
            "error": "User not found"
        }
        assert PortalAuthService.verify_mfa(user.id, "123456") == {
            "error": "MFA not enabled for this user"
        }

    async def test_secret_never_logged(
        self, ctx: Quart, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No log event carries the secret, provisioning URI or backup codes."""
        user = _seed_user(ctx)
        with structlog.testing.capture_logs() as events, caplog.at_level("DEBUG"):
            setup = _enroll(ctx, user.id)
            secret = setup["secret"]
            PortalAuthService.verify_mfa(user.id, "000000")
            code = _code(secret)
            PortalAuthService.verify_mfa(user.id, code)
            PortalAuthService.verify_mfa(user.id, code)  # replay
            PortalAuthService.verify_mfa(user.id, setup["backup_codes"][0])
            for _ in range(PortalAuthService.MAX_MFA_ATTEMPTS + 1):
                PortalAuthService.verify_mfa(user.id, "000000")
        sealed = _row(ctx, user.id).mfa_secret

        haystack = repr(events) + caplog.text
        assert {e["event"] for e in events} >= {
            "mfa_totp_replay_rejected",
            "mfa_backup_code_consumed",
            "mfa_verify_lockout_started",
            "mfa_verify_locked",
        }
        for needle in (secret, setup["provisioning_uri"], sealed):
            assert needle not in haystack
        for backup in setup["backup_codes"]:
            assert backup not in haystack


@pytest.mark.asyncio
class TestPendingEnrollment:
    """Enrolment stays pending until confirmed; changes need the current factor."""

    async def test_enable_is_pending_and_stores_only_envelope_and_hashes(
        self, ctx: Quart
    ) -> None:
        """Setup returns plaintext once; nothing is active until confirmed."""
        user = _seed_user(ctx)
        setup = PortalAuthService.enable_mfa(user.id)
        row = _row(ctx, user.id)

        assert setup["pending"] is True
        assert row.mfa_secret is None
        assert row.mfa_backup_codes is None
        assert row.mfa_pending_secret.startswith(ENVELOPE_PREFIX)
        assert setup["secret"] not in row.mfa_pending_secret
        assert (
            row.mfa_pending_secret.split(":")[1]
            == build_keyring(ctx.config).current_kid
        )
        assert setup["secret"] in setup["provisioning_uri"]
        raw_pending = row.mfa_pending_backup_codes
        pending_codes = (
            json.loads(raw_pending)
            if isinstance(raw_pending, str)
            else list(raw_pending)
        )
        assert len(pending_codes) == len(setup["backup_codes"]) == 10
        assert all(is_hashed_backup_code(c) for c in pending_codes)
        assert not set(pending_codes) & set(setup["backup_codes"])
        assert setup["secret"] not in repr(row)
        assert setup["secret"] not in str(row)

        # Not enrolled yet: the password alone still signs in, MFA not demanded.
        login = PortalAuthService.authenticate(user.tenant_id, user.email, PASSWORD)
        assert "mfa_required" not in login
        assert PortalAuthService.verify_mfa(user.id, _code(setup["secret"])) == {
            "error": "MFA not enabled for this user"
        }

    async def test_confirm_activates_and_blocks_replay_of_confirm_code(
        self, ctx: Quart
    ) -> None:
        """A valid code promotes the pending secret; that code cannot log in."""
        user = _seed_user(ctx)
        setup = PortalAuthService.enable_mfa(user.id)
        code = _code(setup["secret"])

        assert PortalAuthService.confirm_mfa(user.id, "000000")["status_code"] == 400
        assert _row(ctx, user.id).mfa_secret is None
        assert PortalAuthService.confirm_mfa(user.id, code) == {
            "verified": True,
            "enrolled": True,
        }

        row = _row(ctx, user.id)
        assert row.mfa_secret.startswith(ENVELOPE_PREFIX)
        assert row.mfa_pending_secret is None
        assert row.mfa_pending_backup_codes is None
        assert row.mfa_pending_expires_at is None
        assert len(_codes(row)) == 10
        assert all(is_hashed_backup_code(c) for c in _codes(row))
        assert PortalAuthService.authenticate(user.tenant_id, user.email, PASSWORD)[
            "mfa_required"
        ]
        assert PortalAuthService.verify_mfa(user.id, code) == {
            "error": "Invalid MFA code"
        }
        assert "error" not in PortalAuthService.verify_mfa(
            user.id, _code(setup["secret"], 1)
        )

    async def test_abandoned_pending_enrollment_expires(self, ctx: Quart) -> None:
        """An unconfirmed setup is discarded once its window has passed."""
        user = _seed_user(ctx)
        setup = PortalAuthService.enable_mfa(user.id)
        ctx.db(ctx.db.portal_users.id == user.id).update(  # type: ignore[attr-defined]
            mfa_pending_expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )

        result = PortalAuthService.confirm_mfa(user.id, _code(setup["secret"]))
        assert result["status_code"] == 400
        assert result["expired"] is True
        row = _row(ctx, user.id)
        assert row.mfa_secret is None
        assert row.mfa_pending_secret is None

    async def test_reenroll_requires_current_factor(self, ctx: Quart) -> None:
        """A session alone cannot replace an enrolled factor."""
        user = _seed_user(ctx)
        old = _enroll(ctx, user.id)
        sealed_before = _row(ctx, user.id).mfa_secret

        for bad in (None, "", "000000"):
            denied = PortalAuthService.enable_mfa(user.id, bad)
            assert denied["status_code"] == 403
            assert denied["mfa_code_required"] is True
        row = _row(ctx, user.id)
        assert row.mfa_secret == sealed_before
        assert row.mfa_pending_secret is None

        new = PortalAuthService.enable_mfa(user.id, _code(old["secret"]))
        assert new["pending"] is True
        # Still the old factor until the new one is confirmed.
        assert _row(ctx, user.id).mfa_secret == sealed_before
        assert "error" not in PortalAuthService.verify_mfa(
            user.id, _code(old["secret"], 1)
        )

        assert PortalAuthService.confirm_mfa(user.id, _code(new["secret"]))["enrolled"]
        ctx.db(ctx.db.portal_users.id == user.id).update(  # type: ignore[attr-defined]
            mfa_last_totp_counter=None
        )
        assert "error" in PortalAuthService.verify_mfa(user.id, _code(old["secret"]))
        assert "error" not in PortalAuthService.verify_mfa(
            user.id, _code(new["secret"])
        )
        assert "error" in PortalAuthService.verify_mfa(user.id, old["backup_codes"][0])

    async def test_disable_and_regenerate_require_current_factor(
        self, ctx: Quart
    ) -> None:
        """Removing MFA or minting backup codes needs a valid current code."""
        user = _seed_user(ctx)
        setup = _enroll(ctx, user.id)
        before = _codes(_row(ctx, user.id))

        assert PortalAuthService.regenerate_backup_codes(user.id)["status_code"] == 403
        assert (
            PortalAuthService.regenerate_backup_codes(user.id, "000000")["status_code"]
            == 403
        )
        assert _codes(_row(ctx, user.id)) == before
        assert PortalAuthService.disable_mfa(user.id)["status_code"] == 403
        assert PortalAuthService.disable_mfa(user.id, "000000")["status_code"] == 403
        assert _row(ctx, user.id).mfa_secret is not None

        assert PortalAuthService.disable_mfa(user.id, setup["backup_codes"][0]) == {
            "success": True
        }
        assert _row(ctx, user.id).mfa_secret is None


@pytest.mark.asyncio
class TestMfaChallenge:
    """The MFA login step is bound to the password step by a server challenge."""

    async def test_challenge_stored_hashed_and_single_use(self, ctx: Quart) -> None:
        """Only a digest is stored; a challenge works exactly once."""
        user = _seed_user(ctx)
        secret = _enroll(ctx, user.id)["secret"]
        challenge = PortalAuthService.create_mfa_challenge(
            user.id, user.tenant_id, "ua"
        )

        rows = ctx.db(  # type: ignore[attr-defined]
            ctx.db.portal_mfa_challenges.portal_user_id == user.id  # type: ignore[attr-defined]
        ).select()
        assert len(rows) == 1
        assert rows[0].challenge_hash != challenge
        assert challenge not in repr(rows[0])
        assert rows[0].fingerprint_hash != "ua"

        ok = PortalAuthService.verify_mfa_challenge(challenge, _code(secret), "ua")
        assert ok["id"] == user.id
        again = PortalAuthService.verify_mfa_challenge(
            challenge, _code(secret, 1), "ua"
        )
        assert again["challenge_invalid"] is True
        assert again["status_code"] == 401

    @pytest.mark.parametrize("bogus", [None, "", "   ", 123, "not-a-real-challenge"])
    async def test_unknown_challenge_is_rejected(self, ctx: Quart, bogus: Any) -> None:
        """Anything that is not a live challenge id is refused."""
        result = PortalAuthService.verify_mfa_challenge(bogus, "123456", "ua")
        assert result["challenge_invalid"] is True

    async def test_expired_and_foreign_fingerprint_challenges_rejected(
        self, ctx: Quart
    ) -> None:
        """Expiry and a different client fingerprint both kill a challenge."""
        user = _seed_user(ctx)
        secret = _enroll(ctx, user.id)["secret"]
        table = ctx.db.portal_mfa_challenges  # type: ignore[attr-defined]

        other_client = PortalAuthService.create_mfa_challenge(
            user.id, user.tenant_id, "browser-a"
        )
        result = PortalAuthService.verify_mfa_challenge(
            other_client, _code(secret), "browser-b"
        )
        assert result["challenge_invalid"] is True

        expired = PortalAuthService.create_mfa_challenge(user.id, user.tenant_id, "ua")
        ctx.db(table.portal_user_id == user.id).update(  # type: ignore[attr-defined]
            expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )
        result = PortalAuthService.verify_mfa_challenge(expired, _code(secret), "ua")
        assert result["challenge_invalid"] is True
        # Neither rejected attempt touched the account's own budget.
        assert not _row(ctx, user.id).mfa_failed_attempts
        assert _row(ctx, user.id).mfa_last_totp_counter is None

        # Expired rows are pruned when the next challenge is created.
        PortalAuthService.create_mfa_challenge(user.id, user.tenant_id, "ua")
        assert ctx.db(table.portal_user_id == user.id).count() == 1  # type: ignore[attr-defined]

    async def test_challenge_dies_after_max_attempts(self, ctx: Quart) -> None:
        """A challenge is invalidated after N failures, even for a right code."""
        user = _seed_user(ctx)
        secret = _enroll(ctx, user.id)["secret"]
        # Give the account a large budget so only the challenge cap is in play.
        ctx.db(ctx.db.portal_users.id == user.id).update(  # type: ignore[attr-defined]
            mfa_failed_attempts=-100
        )
        challenge = PortalAuthService.create_mfa_challenge(
            user.id, user.tenant_id, "ua"
        )

        for attempt in range(PortalAuthService.MAX_MFA_CHALLENGE_ATTEMPTS):
            result = PortalAuthService.verify_mfa_challenge(challenge, "000000", "ua")
            assert result["error"] == "Invalid MFA code"
            last = attempt == PortalAuthService.MAX_MFA_CHALLENGE_ATTEMPTS - 1
            assert bool(result.get("challenge_invalid")) is last

        dead = PortalAuthService.verify_mfa_challenge(challenge, _code(secret), "ua")
        assert dead["challenge_invalid"] is True
        assert _row(ctx, user.id).mfa_last_totp_counter is None

    async def test_user_lock_applies_across_challenges(self, ctx: Quart) -> None:
        """Fresh challenges do not buy extra guesses once the account is locked."""
        user = _seed_user(ctx)
        secret = _enroll(ctx, user.id)["secret"]
        first = PortalAuthService.create_mfa_challenge(user.id, user.tenant_id, "ua")
        for _ in range(PortalAuthService.MAX_MFA_ATTEMPTS):
            PortalAuthService.verify_mfa_challenge(first, "000000", "ua")

        second = PortalAuthService.create_mfa_challenge(user.id, user.tenant_id, "ua")
        locked = PortalAuthService.verify_mfa_challenge(second, _code(secret), "ua")
        assert locked["status_code"] == 429
        assert locked["locked"] is True

    async def test_expired_challenges_pruned_table_wide(self, ctx: Quart) -> None:
        """Creating a challenge clears other users' expired rows too."""
        alice = _seed_user(ctx)
        bob = _seed_user(ctx)
        table = ctx.db.portal_mfa_challenges  # type: ignore[attr-defined]
        PortalAuthService.create_mfa_challenge(alice.id, alice.tenant_id, "ua")
        ctx.db(table.portal_user_id == alice.id).update(  # type: ignore[attr-defined]
            expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )

        PortalAuthService.create_mfa_challenge(bob.id, bob.tenant_id, "ua")

        assert ctx.db(table.portal_user_id == alice.id).count() == 0  # type: ignore[attr-defined]
        assert ctx.db(table.portal_user_id == bob.id).count() == 1  # type: ignore[attr-defined]

    async def test_live_challenges_capped_per_user(self, ctx: Quart) -> None:
        """Only the newest N challenges survive; the oldest is evicted."""
        user = _seed_user(ctx)
        secret = _enroll(ctx, user.id)["secret"]
        cap = PortalAuthService.MAX_LIVE_MFA_CHALLENGES
        table = ctx.db.portal_mfa_challenges  # type: ignore[attr-defined]
        issued = [
            PortalAuthService.create_mfa_challenge(user.id, user.tenant_id, "ua")
            for _ in range(cap + 2)
        ]

        assert ctx.db(table.portal_user_id == user.id).count() == cap  # type: ignore[attr-defined]
        oldest = PortalAuthService.verify_mfa_challenge(issued[0], _code(secret), "ua")
        assert oldest["challenge_invalid"] is True
        newest = PortalAuthService.verify_mfa_challenge(issued[-1], _code(secret), "ua")
        assert newest["id"] == user.id


def _enforce_mfa(app: Quart, tenant_id: int, *, grace_days: int, days_ago: int) -> None:
    """Turn on tenant MFA enforcement, back-dated by ``days_ago`` days."""
    app.db(app.db.tenants.id == tenant_id).update(  # type: ignore[attr-defined]
        settings={
            "mfa_enforced": True,
            "mfa_enforced_at": (
                datetime.now(UTC) - timedelta(days=days_ago)
            ).isoformat(),
            "mfa_grace_period_days": grace_days,
        }
    )


async def _login(client: Any, user: Any, **extra: Any) -> Any:
    """POST the password step for ``user`` (plus any extra body fields)."""
    return await client.post(
        "/api/v1/portal-auth/login",
        json={
            "tenant_id": user.tenant_id,
            "email": user.email,
            "password": PASSWORD,
            **extra,
        },
        headers={"User-Agent": "pytest-client"},
    )


def _set_cookies(resp: Any) -> str:
    """Return every Set-Cookie header of a response joined into one string."""
    return " | ".join(resp.headers.getlist("Set-Cookie"))


@pytest.mark.asyncio
class TestRoutes:
    """HTTP surface: challenge flow, cache headers, no secret after setup."""

    async def test_setup_response_is_not_cacheable(self, mfa_app: Quart) -> None:
        """The enrollment and regenerate responses carry Cache-Control: no-store."""
        user = _seed_user(mfa_app)
        headers = {"Authorization": f"Bearer {_token(mfa_app, user)}"}
        client = mfa_app.test_client()

        resp = await client.post("/api/v1/portal-auth/mfa/enable", headers=headers)
        assert resp.status_code == 200
        assert resp.headers["Cache-Control"] == "no-store"
        assert resp.headers["Pragma"] == "no-cache"
        body = await resp.get_json()
        assert set(body) == {
            "secret",
            "provisioning_uri",
            "backup_codes",
            "pending",
            "expires_in",
        }
        confirm = await client.post(
            "/api/v1/portal-auth/mfa/confirm",
            headers=headers,
            json={"totp_code": _code(body["secret"])},
        )
        assert await confirm.get_json() == {"verified": True}

        regen = await client.post(
            "/api/v1/portal-auth/mfa/backup-codes/regenerate",
            headers=headers,
            json={"current_code": body["backup_codes"][0]},
        )
        assert regen.status_code == 200
        assert regen.headers["Cache-Control"] == "no-store"

    async def test_mfa_change_routes_demand_current_code(self, mfa_app: Quart) -> None:
        """Re-enrol, regenerate and disable answer 403 without a current code."""
        user = _seed_user(mfa_app)
        async with mfa_app.app_context():
            setup = _enroll(mfa_app, user.id)
        headers = {"Authorization": f"Bearer {_token(mfa_app, user)}"}
        client = mfa_app.test_client()

        for path in ("mfa/enable", "mfa/backup-codes/regenerate", "mfa/disable"):
            resp = await client.post(f"/api/v1/portal-auth/{path}", headers=headers)
            assert resp.status_code == 403, path
            assert (await resp.get_json())["mfa_code_required"] is True

        disabled = await client.post(
            "/api/v1/portal-auth/mfa/disable",
            headers=headers,
            json={"current_code": _code(setup["secret"])},
        )
        assert disabled.status_code == 200
        assert _row(mfa_app, user.id).mfa_secret is None

    async def test_login_issues_challenge_not_user_id(self, mfa_app: Quart) -> None:
        """The password step yields an opaque challenge and no account id/token."""
        user = _seed_user(mfa_app)
        async with mfa_app.app_context():
            secret = _enroll(mfa_app, user.id)["secret"]
        client = mfa_app.test_client()

        login = await _login(client, user)
        body = await login.get_json()
        assert login.status_code == 200
        assert login.headers["Cache-Control"] == "no-store"
        assert body["success"] is False
        assert body["mfaRequired"] is True
        assert set(body) == {
            "success",
            "mfaRequired",
            "mfaChallenge",
            "mfa_challenge",
            "mfaChallengeExpiresIn",
        }
        assert "elder_access_token" not in _set_cookies(login)
        challenge = body["mfaChallenge"]
        assert len(challenge) >= 40 and str(user.id) != challenge

        verify = await client.post(
            "/api/v1/portal-auth/mfa/verify",
            json={"mfa_challenge": challenge, "totp_code": _code(secret)},
            headers={"User-Agent": "pytest-client"},
        )
        assert verify.status_code == 200
        verified = await verify.get_json()
        assert verified["success"] is True and verified["token"]
        assert "elder_access_token=" in _set_cookies(verify)

        replay = await client.post(
            "/api/v1/portal-auth/mfa/verify",
            json={"mfa_challenge": challenge, "totp_code": _code(secret, 1)},
            headers={"User-Agent": "pytest-client"},
        )
        assert replay.status_code == 401
        assert (await replay.get_json())["errorCode"] == "MFA_CHALLENGE_INVALID"

    async def test_bare_user_id_cannot_reach_the_second_factor(
        self, mfa_app: Quart
    ) -> None:
        """user_id + a correct code yields nothing, and cannot lock the account."""
        user = _seed_user(mfa_app)
        async with mfa_app.app_context():
            setup = _enroll(mfa_app, user.id)
        client = mfa_app.test_client()

        for code in (_code(setup["secret"]), setup["backup_codes"][0]):
            resp = await client.post(
                "/api/v1/portal-auth/mfa/verify",
                json={"user_id": user.id, "totp_code": code},
            )
            assert resp.status_code == 400
            assert "token" not in await resp.get_json()
            assert "elder_access_token" not in _set_cookies(resp)

        for _ in range(PortalAuthService.MAX_MFA_ATTEMPTS * 3):
            resp = await client.post(
                "/api/v1/portal-auth/mfa/verify",
                json={
                    "user_id": user.id,
                    "mfa_challenge": "guessed-challenge-id",
                    "totp_code": "000000",
                },
            )
            assert resp.status_code == 401

        row = _row(mfa_app, user.id)
        assert not row.mfa_failed_attempts
        assert row.mfa_locked_until is None
        assert row.mfa_last_totp_counter is None
        assert len(_codes(row)) == 10

    async def test_login_with_password_and_code_in_one_request(
        self, mfa_app: Quart
    ) -> None:
        """react-libs style: credentials + mfaCode together sign in."""
        user = _seed_user(mfa_app)
        async with mfa_app.app_context():
            secret = _enroll(mfa_app, user.id)["secret"]
        client = mfa_app.test_client()

        wrong = await _login(client, user, mfaCode="000000")
        assert wrong.status_code == 401
        assert (await wrong.get_json())["errorCode"] == "INVALID_MFA_CODE"

        bad_password = await client.post(
            "/api/v1/portal-auth/login",
            json={
                "tenant_id": user.tenant_id,
                "email": user.email,
                "password": "not-the-password",
                "mfaCode": _code(secret),
            },
        )
        assert bad_password.status_code == 401
        assert (await bad_password.get_json())["errorCode"] == "INVALID_CREDENTIALS"
        assert _row(mfa_app, user.id).mfa_last_totp_counter is None

        ok = await _login(client, user, mfaCode=_code(secret))
        assert ok.status_code == 200
        assert (await ok.get_json())["success"] is True
        assert "elder_access_token=" in _set_cookies(ok)

    async def test_secret_absent_from_responses_after_setup(
        self, mfa_app: Quart
    ) -> None:
        """Login, MFA verify, confirm and /me never echo the secret or envelope."""
        user = _seed_user(mfa_app)
        headers = {"Authorization": f"Bearer {_token(mfa_app, user)}"}
        client = mfa_app.test_client()
        setup = await (
            await client.post("/api/v1/portal-auth/mfa/enable", headers=headers)
        ).get_json()
        secret = setup["secret"]
        confirm = await client.post(
            "/api/v1/portal-auth/mfa/confirm",
            headers=headers,
            json={"totp_code": _code(secret)},
        )
        sealed = _row(mfa_app, user.id).mfa_secret
        stored_codes = _codes(_row(mfa_app, user.id))

        login = await _login(client, user)
        verify = await client.post(
            "/api/v1/portal-auth/mfa/verify",
            json={
                "mfa_challenge": (await login.get_json())["mfaChallenge"],
                "totp_code": _code(secret, 1),
            },
            headers={"User-Agent": "pytest-client"},
        )
        me = await client.get("/api/v1/portal-auth/me", headers=headers)

        assert confirm.status_code == 200
        assert verify.status_code == 200
        assert me.status_code == 200
        me_body = await me.get_json()
        assert me_body["mfa_enabled"] is True
        assert me_body["mfa_backup_codes_remaining"] == 10

        for resp in (login, confirm, verify, me):
            text = await resp.get_data(as_text=True)
            assert secret not in text
            assert sealed not in text
            assert "mfa_secret" not in text
            assert "provisioning_uri" not in text
            for entry in (*setup["backup_codes"], *stored_codes):
                assert entry not in text

    async def test_verify_route_lockout_and_input_validation(
        self, mfa_app: Quart
    ) -> None:
        """401 per bad code, 429 once the account is locked, 400 on junk."""
        user = _seed_user(mfa_app)
        client = mfa_app.test_client()
        async with mfa_app.app_context():
            secret = _enroll(mfa_app, user.id)["secret"]

        challenge = (await (await _login(client, user)).get_json())["mfaChallenge"]
        for _ in range(PortalAuthService.MAX_MFA_ATTEMPTS):
            resp = await client.post(
                "/api/v1/portal-auth/mfa/verify",
                json={"mfa_challenge": challenge, "totp_code": "000000"},
                headers={"User-Agent": "pytest-client"},
            )
            assert resp.status_code == 401

        fresh = (await (await _login(client, user)).get_json())["mfaChallenge"]
        locked = await client.post(
            "/api/v1/portal-auth/mfa/verify",
            json={"mfa_challenge": fresh, "totp_code": _code(secret)},
            headers={"User-Agent": "pytest-client"},
        )
        assert locked.status_code == 429
        body = await locked.get_json()
        assert body["errorCode"] == "MFA_LOCKED"
        assert "token" not in body

        locked_login = await _login(client, user, mfaCode=_code(secret))
        assert locked_login.status_code == 429

        for junk in (
            {"mfa_challenge": ["x"], "totp_code": "123456"},
            {"mfa_challenge": fresh, "totp_code": ["123456"]},
            {"mfa_challenge": fresh},
            {"totp_code": "123456"},
        ):
            bad = await client.post("/api/v1/portal-auth/mfa/verify", json=junk)
            assert bad.status_code == 400


NORMAL_ROUTES = (
    ("GET", "/api/v1/portal-auth/me"),
    ("POST", "/api/v1/portal-auth/mfa/disable"),
    ("POST", "/api/v1/portal-auth/mfa/backup-codes/regenerate"),
    ("POST", "/api/v1/portal-auth/password/change"),
    ("GET", "/api/v1/tenants/current/mfa-policy"),
    ("PUT", "/api/v1/tenants/current/mfa-policy"),
)


@pytest.mark.asyncio
class TestGraceExpiryEnforcement:
    """Expired MFA grace: no session, only an enrolment-scoped token."""

    async def test_expired_grace_login_gets_enrollment_token_only(
        self, mfa_app: Quart
    ) -> None:
        """The token reaches MFA setup and logout, and no other route."""
        from apps.api.api.v1.tenants import bp as tenants_bp
        from apps.api.auth import jwt_handler

        mfa_app.register_blueprint(tenants_bp, url_prefix="/api/v1/tenants")
        mfa_app.config["JWT_ALGORITHM"] = "HS256"
        user = _seed_user(mfa_app)
        _enforce_mfa(mfa_app, user.tenant_id, grace_days=0, days_ago=1)
        client = mfa_app.test_client()

        login = await _login(client, user)
        body = await login.get_json()
        assert login.status_code == 200
        assert body["mfaEnrollmentRequired"] is True
        assert body["mfa_locked_out"] is True
        for absent in ("token", "access_token", "refreshToken", "refresh_token"):
            assert absent not in body
        cookies = _set_cookies(login)
        # No refresh cookie is issued (any stale one is deleted, i.e. emptied).
        assert re.search(r"elder_refresh_token=[^;\s]", cookies) is None
        token = body["enrollment_token"]
        claims = jwt.decode(token, options={"verify_signature": False})
        assert claims["type"] == "portal_mfa_enrollment"
        assert claims["scope"] == []
        assert claims["exp"] - claims["iat"] <= 15 * 60
        with pytest.raises(jwt.InvalidTokenError):
            jwt.decode(token, mfa_app.config["JWT_SECRET_KEY"], algorithms=["HS256"])

        for transport in ("header", "cookie"):
            for method, path in NORMAL_ROUTES:
                headers = (
                    {"Authorization": f"Bearer {token}"}
                    if transport == "header"
                    else {"Cookie": f"elder_access_token={token}"}
                )
                resp = await client.open(path, method=method, headers=headers, json={})
                assert resp.status_code == 401, (transport, method, path)

        async with mfa_app.app_context():
            assert jwt_handler.verify_token(token) is None
            assert PortalAuthService.verify_mfa_enrollment_token(token)["sub"] == str(
                user.id
            )
            assert (
                PortalAuthService.verify_mfa_enrollment_token(_token(mfa_app, user))
                is None
            )

        headers = {"Authorization": f"Bearer {token}"}
        enable = await client.post("/api/v1/portal-auth/mfa/enable", headers=headers)
        assert enable.status_code == 200
        secret = (await enable.get_json())["secret"]
        confirm = await client.post(
            "/api/v1/portal-auth/mfa/confirm",
            headers=headers,
            json={"totp_code": _code(secret)},
        )
        assert await confirm.get_json() == {"verified": True, "reauthenticate": True}
        logout = await client.post("/api/v1/portal-auth/logout", headers=headers)
        assert logout.status_code == 200

        # Enrolled now: the next login is the ordinary password + MFA flow.
        relogin = await _login(client, user)
        assert (await relogin.get_json())["mfaRequired"] is True
        full = await _login(client, user, mfaCode=_code(secret, 1))
        full_body = await full.get_json()
        assert full_body["success"] is True and full_body["token"]
        assert full_body["mfa_locked_out"] is False

    async def test_within_grace_login_still_gets_a_session(
        self, mfa_app: Quart
    ) -> None:
        """Before the deadline the user signs in normally, with the warning flags."""
        user = _seed_user(mfa_app)
        _enforce_mfa(mfa_app, user.tenant_id, grace_days=7, days_ago=1)
        body = await (await _login(mfa_app.test_client(), user)).get_json()
        assert body["token"]
        assert body["mfa_setup_required"] is True
        assert body["mfa_locked_out"] is False
        assert "mfaEnrollmentRequired" not in body

    async def test_refresh_refused_once_grace_expired(self, mfa_app: Quart) -> None:
        """A session begun inside the grace period cannot be refreshed past it."""
        user = _seed_user(mfa_app)
        _enforce_mfa(mfa_app, user.tenant_id, grace_days=7, days_ago=1)
        client = mfa_app.test_client()
        refresh_token = (await (await _login(client, user)).get_json())["refresh_token"]

        ok = await client.post(
            "/api/v1/portal-auth/refresh", json={"refresh_token": refresh_token}
        )
        assert ok.status_code == 200

        _enforce_mfa(mfa_app, user.tenant_id, grace_days=0, days_ago=1)
        refused = await client.post(
            "/api/v1/portal-auth/refresh", json={"refresh_token": refresh_token}
        )
        assert refused.status_code == 401
        assert "access_token" not in (await refused.get_data(as_text=True))

    async def test_forged_enrollment_token_with_session_key_is_rejected(
        self, mfa_app: Quart
    ) -> None:
        """An enrolment-typed token signed with the ordinary JWT key is refused."""
        user = _seed_user(mfa_app)
        now = datetime.now(UTC)
        forged = jwt.encode(
            {
                "sub": str(user.id),
                "tenant_id": user.tenant_id,
                "type": "portal_mfa_enrollment",
                "aud": "elder-mfa-enrollment",
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            mfa_app.config["JWT_SECRET_KEY"],
            algorithm="HS256",
        )
        client = mfa_app.test_client()
        for path in ("mfa/enable", "mfa/confirm"):
            resp = await client.post(
                f"/api/v1/portal-auth/{path}",
                headers={"Authorization": f"Bearer {forged}"},
                json={"totp_code": "123456"},
            )
            assert resp.status_code == 401, path


def _load_migration() -> ModuleType:
    """Import Alembic revision 056 from its file (the name starts with a digit)."""
    spec = importlib.util.spec_from_file_location("mfa_migration_056", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine: sa.Engine, fn_name: str) -> None:
    """Run the migration's upgrade()/downgrade() inside an Alembic context."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = _load_migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            getattr(module, fn_name)()


@pytest.fixture
def legacy_engine(tmp_path: Path) -> Iterator[sa.Engine]:
    """Create a pre-056 schema (no MFA state columns) in a throwaway SQLite DB."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE portal_users (id INTEGER PRIMARY KEY, email TEXT, "
                "mfa_secret VARCHAR(255), mfa_backup_codes JSON)"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TABLE identities (id INTEGER PRIMARY KEY, mfa_secret VARCHAR(255))"
            )
        )
    yield engine
    engine.dispose()


def _fetch(engine: sa.Engine, table: str, row_id: int, column: str) -> Any:
    """Read one column of one row."""
    with engine.connect() as conn:
        return conn.execute(
            sa.text(f"SELECT {column} FROM {table} WHERE id = :i"), {"i": row_id}
        ).scalar_one()


class TestMigration056:
    """Alembic revision 056: seal, hash, idempotency, downgrade, key handling."""

    def test_revision_chain(self) -> None:
        """056 sits on top of both release heads, 053 and 054."""
        module = _load_migration()
        assert module.revision == "056"
        assert module.down_revision == ("053", "054")

    def test_upgrade_seals_and_hashes_then_is_idempotent(
        self, legacy_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plaintext rows are converted once; a second run changes nothing."""
        monkeypatch.setenv("SECRET_KEY", APP_SECRET)
        monkeypatch.delenv("MFA_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("MFA_ENCRYPTION_OLD_KEYS", raising=False)
        secret_a, secret_b = pyotp.random_base32(), pyotp.random_base32()
        with legacy_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO portal_users (id, email, mfa_secret, mfa_backup_codes) "
                    "VALUES (1, 'a', :s, :c), (2, 'b', NULL, NULL), (3, 'c', '', '[]')"
                ),
                {"s": secret_a, "c": json.dumps(["0a1b2c3d", "feedface"])},
            )
            conn.execute(
                sa.text("INSERT INTO identities (id, mfa_secret) VALUES (9, :s)"),
                {"s": secret_b},
            )

        _run(legacy_engine, "upgrade")

        ring = _ring()
        sealed_a = _fetch(legacy_engine, "portal_users", 1, "mfa_secret")
        sealed_b = _fetch(legacy_engine, "identities", 9, "mfa_secret")
        assert sealed_a.startswith(ENVELOPE_PREFIX) and secret_a not in sealed_a
        assert decrypt_totp_secret(sealed_a, ring, aad="portal_users:1") == secret_a
        assert decrypt_totp_secret(sealed_b, ring, aad="identities:9") == secret_b
        assert _fetch(legacy_engine, "portal_users", 2, "mfa_secret") is None
        assert _fetch(legacy_engine, "portal_users", 3, "mfa_secret") == ""
        codes = json.loads(_fetch(legacy_engine, "portal_users", 1, "mfa_backup_codes"))
        assert len(codes) == 2 and all(is_hashed_backup_code(c) for c in codes)
        assert match_backup_code(codes, "feedface") == 1
        columns = {
            c["name"] for c in sa.inspect(legacy_engine).get_columns("portal_users")
        }
        assert {
            "mfa_last_totp_counter",
            "mfa_failed_attempts",
            "mfa_locked_until",
            "mfa_pending_secret",
            "mfa_pending_backup_codes",
            "mfa_pending_expires_at",
        } <= columns

        assert "portal_mfa_challenges" in sa.inspect(legacy_engine).get_table_names()

        _run(legacy_engine, "upgrade")
        assert _fetch(legacy_engine, "portal_users", 1, "mfa_secret") == sealed_a
        assert _fetch(legacy_engine, "identities", 9, "mfa_secret") == sealed_b
        assert (
            json.loads(_fetch(legacy_engine, "portal_users", 1, "mfa_backup_codes"))
            == codes
        )

    def test_downgrade_restores_secret_and_clears_hashed_codes(
        self, legacy_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Downgrade decrypts secrets, empties hashed code lists, drops columns."""
        monkeypatch.setenv("SECRET_KEY", APP_SECRET)
        monkeypatch.delenv("MFA_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("MFA_ENCRYPTION_OLD_KEYS", raising=False)
        secret = pyotp.random_base32()
        with legacy_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO portal_users (id, email, mfa_secret, mfa_backup_codes) "
                    "VALUES (1, 'a', :s, :c)"
                ),
                {"s": secret, "c": json.dumps(["0a1b2c3d"])},
            )
        _run(legacy_engine, "upgrade")
        _run(legacy_engine, "downgrade")

        assert _fetch(legacy_engine, "portal_users", 1, "mfa_secret") == secret
        assert (
            json.loads(_fetch(legacy_engine, "portal_users", 1, "mfa_backup_codes"))
            == []
        )
        columns = {
            c["name"] for c in sa.inspect(legacy_engine).get_columns("portal_users")
        }
        assert (
            not {
                "mfa_last_totp_counter",
                "mfa_failed_attempts",
                "mfa_locked_until",
                "mfa_pending_secret",
                "mfa_pending_backup_codes",
                "mfa_pending_expires_at",
            }
            & columns
        )

        assert (
            "portal_mfa_challenges" not in sa.inspect(legacy_engine).get_table_names()
        )

        _run(legacy_engine, "upgrade")
        assert _fetch(legacy_engine, "portal_users", 1, "mfa_secret").startswith(
            ENVELOPE_PREFIX
        )

    def test_upgrade_reseals_rows_from_a_rotated_key(
        self, legacy_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-running after a rotation moves rows to the current key id."""
        monkeypatch.setenv("SECRET_KEY", APP_SECRET)
        monkeypatch.delenv("MFA_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("MFA_ENCRYPTION_OLD_KEYS", raising=False)
        secret = pyotp.random_base32()
        with legacy_engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO portal_users (id, mfa_secret) VALUES (1, :s)"),
                {"s": secret},
            )
        _run(legacy_engine, "upgrade")

        monkeypatch.setenv("MFA_ENCRYPTION_KEY", OTHER_SECRET)
        monkeypatch.setenv("MFA_ENCRYPTION_OLD_KEYS", APP_SECRET)
        _run(legacy_engine, "upgrade")

        resealed = _fetch(legacy_engine, "portal_users", 1, "mfa_secret")
        assert resealed.split(":")[1] == _ring(OTHER_SECRET).current_kid
        assert (
            decrypt_totp_secret(resealed, _ring(OTHER_SECRET), aad="portal_users:1")
            == secret
        )

    def test_upgrade_without_key_fails_only_when_rows_need_it(
        self, legacy_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No key + nothing to convert passes; no key + a secret row aborts."""
        for name in ("SECRET_KEY", "MFA_ENCRYPTION_KEY", "MFA_ENCRYPTION_OLD_KEYS"):
            monkeypatch.delenv(name, raising=False)

        _run(legacy_engine, "upgrade")  # empty tables: no key required

        secret = pyotp.random_base32()
        with legacy_engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO portal_users (id, mfa_secret) VALUES (1, :s)"),
                {"s": secret},
            )
        with pytest.raises(MfaKeyUnavailableError):
            _run(legacy_engine, "upgrade")
        assert _fetch(legacy_engine, "portal_users", 1, "mfa_secret") == secret

    def test_downgrade_with_wrong_key_fails_loudly(
        self, legacy_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Downgrade never writes garbage when the key does not match."""
        monkeypatch.setenv("SECRET_KEY", APP_SECRET)
        monkeypatch.delenv("MFA_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("MFA_ENCRYPTION_OLD_KEYS", raising=False)
        with legacy_engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO portal_users (id, mfa_secret) VALUES (1, :s)"),
                {"s": pyotp.random_base32()},
            )
        _run(legacy_engine, "upgrade")
        sealed = _fetch(legacy_engine, "portal_users", 1, "mfa_secret")

        monkeypatch.setenv("SECRET_KEY", OTHER_SECRET)
        with pytest.raises(MfaKeyUnavailableError):
            _run(legacy_engine, "downgrade")
        assert _fetch(legacy_engine, "portal_users", 1, "mfa_secret") == sealed


def test_service_module_exposes_no_plaintext_helpers() -> None:
    """The service keeps exactly one read and one write accessor for the column."""
    source = Path(portal_service.__file__).read_text()
    assert source.count("mfa_pending_secret=_seal_totp_secret(") == 1
    assert source.count("mfa_secret=_seal_totp_secret(") == 0
    assert source.count('updates["mfa_secret"] = _seal_totp_secret(') == 1
    assert "pyotp.TOTP(user.mfa_secret)" not in source
    assert "mfa_secret=secret" not in source
