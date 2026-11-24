"""Unit tests for Enterprise MFA Enforcement (Google Authenticator / TOTP).

Covers:
- GET/PUT /api/v1/tenants/current/mfa-policy
- Enterprise license gating (403 on Free, 200 on Enterprise & PenguinTech bypass domains)
- Non-admin 403 enforcement
- grace_period_days validation (0..90) and preserving mfa_enforced_at on updates
- POST /api/v1/portal-auth/mfa/disable blocked (403) when tenant enforces MFA
- POST /api/v1/portal-auth/mfa/confirm TOTP validation (valid, invalid, uninitialized)
- Grace period calculation anchored to max(mfa_enforced_at, user.created_at)
- mfa_grace_days_remaining and mfa_locked_out transitions before vs after deadline
- mfa_enforced, mfa_setup_required, mfa_grace_expires_at, mfa_grace_days_remaining,
  and mfa_locked_out on /api/v1/portal-auth/me and /api/v1/portal-auth/login
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace
from typing import Any

import jwt
import penguin_dal
import pyotp
import pytest
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

try:
    from penguin_libs.pydantic import Name255, RequestModel, SlugStr  # noqa: F401
except ImportError:
    from pydantic import BaseModel

    _pl_pyd = ModuleType("penguin_libs.pydantic")
    _pl_pyd.RequestModel = BaseModel  # type: ignore[attr-defined]
    _pl_pyd.Name255 = str  # type: ignore[attr-defined]
    _pl_pyd.SlugStr = str  # type: ignore[attr-defined]
    sys.modules["penguin_libs.pydantic"] = _pl_pyd

from apps.api.api.v1.portal_auth import bp as portal_auth_bp
from apps.api.api.v1.tenants import bp as tenants_bp
from apps.api.services.portal_auth import (
    compute_user_mfa_status,
    user_has_mfa_enrolled,
)


class _FakeField:
    """Minimal PyDAL-like field supporting equality expressions."""

    def __init__(self, table_name: str, name: str) -> None:
        self.table_name = table_name
        self.name = name

    def __eq__(self, other: object) -> "_FakeCondition":  # type: ignore[override]
        return _FakeCondition(self.table_name, [(self.name, other)])


class _FakeCondition:
    """Composable equality condition for _FakeDB."""

    def __init__(self, table_name: str, pairs: list[tuple[str, Any]]) -> None:
        self.table_name = table_name
        self.pairs = pairs

    def __and__(self, other: "_FakeCondition") -> "_FakeCondition":
        return _FakeCondition(self.table_name, self.pairs + other.pairs)


class _FakeRow(SimpleNamespace):
    """Row object with attribute access."""


class _FakeTable:
    """In-memory table supporting bracket access and attribute-based queries."""

    def __init__(self, name: str, fields: list[str]) -> None:
        self._name = name
        self.rows: dict[int, _FakeRow] = {}
        self._next_id = 1
        for f in fields:
            setattr(self, f, _FakeField(name, f))

    def insert(self, **kwargs: Any) -> int:
        row_id = kwargs.pop("id", None) or self._next_id
        self._next_id = max(self._next_id, row_id + 1)
        row = _FakeRow(id=row_id, **kwargs)
        self.rows[row_id] = row
        return row_id

    def __getitem__(self, row_id: int) -> _FakeRow | None:
        return self.rows.get(row_id)


class _FakeQuerySet:
    """Result set supporting .select().first() and .update(...)."""

    def __init__(self, table: _FakeTable, matched: list[_FakeRow]) -> None:
        self._table = table
        self._matched = matched

    def select(self, **_kwargs: Any) -> "_FakeSelect":
        return _FakeSelect(self._matched)

    def update(self, **kwargs: Any) -> int:
        for row in self._matched:
            for k, v in kwargs.items():
                setattr(row, k, v)
        return len(self._matched)

    def count(self) -> int:
        return len(self._matched)


class _FakeSelect(list):
    """List subclass providing PyDAL's .first()."""

    def first(self) -> Any:
        return self[0] if self else None


class _FakeDB:
    """In-memory PyDAL substitute for unit-testing MFA enforcement routes."""

    def __init__(self) -> None:
        self.tenants = _FakeTable(
            "tenants",
            ["id", "name", "slug", "settings", "is_active", "updated_at"],
        )
        self.portal_users = _FakeTable(
            "portal_users",
            [
                "id",
                "tenant_id",
                "email",
                "password_hash",
                "full_name",
                "tenant_role",
                "global_role",
                "mfa_secret",
                "mfa_backup_codes",
                "is_active",
                "email_verified",
                "failed_login_attempts",
                "locked_until",
                "last_login_at",
                "created_at",
                "updated_at",
            ],
        )
        self.portal_user_org_assignments = _FakeTable(
            "portal_user_org_assignments",
            ["id", "portal_user_id", "organization_id", "role"],
        )
        self._tables = {
            "tenants": self.tenants,
            "portal_users": self.portal_users,
            "portal_user_org_assignments": self.portal_user_org_assignments,
        }

    def __call__(self, cond: _FakeCondition) -> _FakeQuerySet:
        table = self._tables[cond.table_name]
        matched = [
            row
            for row in table.rows.values()
            if all(getattr(row, field, None) == val for field, val in cond.pairs)
        ]
        return _FakeQuerySet(table, matched)

    def commit(self) -> None:
        pass


class _FakeLicenseClient:
    """Duck-typed license client returning a fixed tier."""

    def __init__(self, tier: str) -> None:
        self._tier = tier

    def validate(self) -> SimpleNamespace:
        return SimpleNamespace(tier=self._tier, limits={})


def _make_app(license_tier: str | None = "enterprise") -> tuple[Quart, _FakeDB]:
    app = Quart(__name__)
    app.config["JWT_SECRET_KEY"] = "test-mfa-secret-key-at-least-32-bytes"
    app.config["SECRET_KEY"] = "test-mfa-secret-key-at-least-32-bytes"
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=1)
    app.config["JWT_REFRESH_TOKEN_EXPIRES"] = timedelta(days=7)
    app.config["DEBUG"] = True

    db = _FakeDB()
    app.db = db  # type: ignore[attr-defined]
    if license_tier is not None:
        app.extensions["license_client"] = _FakeLicenseClient(license_tier)

    app.register_blueprint(tenants_bp, url_prefix="/api/v1/tenants")
    app.register_blueprint(portal_auth_bp, url_prefix="/api/v1/portal-auth")
    return app, db


def _make_token(
    app: Quart,
    *,
    user_id: int,
    tenant_id: int,
    email: str = "admin@example.com",
    tenant_role: str = "admin",
    global_role: str | None = None,
    scope: str = "",
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "email": email,
        "tenant_id": tenant_id,
        "tenant_role": tenant_role,
        "global_role": global_role,
        "scope": scope,
        "type": "portal_user",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    return jwt.encode(payload, app.config["JWT_SECRET_KEY"], algorithm="HS256")


def _seed_tenant_and_user(
    db: _FakeDB,
    *,
    settings: dict[str, Any] | None = None,
    mfa_secret: str | None = None,
    tenant_role: str = "admin",
    global_role: str | None = None,
    user_created_at: datetime | None = None,
    password: str = "Password123!",
) -> tuple[int, int]:
    tenant_id = db.tenants.insert(
        name="Acme Corp",
        slug="acme",
        settings=settings,
        is_active=True,
        updated_at=datetime.now(UTC),
    )
    user_id = db.portal_users.insert(
        tenant_id=tenant_id,
        email="user@acme.com",
        password_hash=generate_password_hash(password),
        full_name="Test User",
        tenant_role=tenant_role,
        global_role=global_role,
        mfa_secret=mfa_secret,
        mfa_backup_codes=None,
        is_active=True,
        email_verified=True,
        failed_login_attempts=0,
        locked_until=None,
        last_login_at=None,
        created_at=user_created_at or datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    return tenant_id, user_id


@pytest.mark.asyncio
class TestTenantMfaPolicyEndpoints:
    """GET and PUT /api/v1/tenants/current/mfa-policy."""

    async def test_get_policy_returns_defaults_on_enterprise(self) -> None:
        app, db = _make_app("enterprise")
        tenant_id, user_id = _seed_tenant_and_user(db)
        token = _make_token(app, user_id=user_id, tenant_id=tenant_id)

        client = app.test_client()
        resp = await client.get(
            "/api/v1/tenants/current/mfa-policy",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body == {
            "enforced": False,
            "provider": "google_authenticator",
            "grace_period_days": 7,
            "enforced_at": None,
            "mfa_enrolled": False,
            "license_tier": "enterprise",
            "can_manage": True,
        }

    async def test_put_policy_blocked_403_on_free_tier(self) -> None:
        app, db = _make_app("community")
        tenant_id, user_id = _seed_tenant_and_user(db, tenant_role="admin")
        token = _make_token(
            app, user_id=user_id, tenant_id=tenant_id, tenant_role="admin"
        )

        client = app.test_client()
        resp = await client.put(
            "/api/v1/tenants/current/mfa-policy",
            headers={"Authorization": f"Bearer {token}"},
            json={"enforced": True, "grace_period_days": 7},
        )
        assert resp.status_code == 403
        body = await resp.get_json()
        assert body["required_tier"] == "enterprise"
        assert body["upgrade"] is True

    async def test_put_policy_blocked_403_for_non_admin(self) -> None:
        app, db = _make_app("enterprise")
        tenant_id, user_id = _seed_tenant_and_user(db, tenant_role="reader")
        token = _make_token(
            app, user_id=user_id, tenant_id=tenant_id, tenant_role="reader"
        )

        client = app.test_client()
        resp = await client.put(
            "/api/v1/tenants/current/mfa-policy",
            headers={"Authorization": f"Bearer {token}"},
            json={"enforced": True},
        )
        assert resp.status_code == 403

    async def test_put_policy_succeeds_on_enterprise_and_preserves_enforced_at(
        self,
    ) -> None:
        app, db = _make_app("enterprise")
        tenant_id, user_id = _seed_tenant_and_user(db, tenant_role="admin")
        token = _make_token(
            app, user_id=user_id, tenant_id=tenant_id, tenant_role="admin"
        )

        client = app.test_client()
        resp1 = await client.put(
            "/api/v1/tenants/current/mfa-policy",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "enforced": True,
                "provider": "google_authenticator",
                "grace_period_days": 14,
            },
        )
        assert resp1.status_code == 200
        body1 = await resp1.get_json()
        assert body1["enforced"] is True
        assert body1["grace_period_days"] == 14
        assert body1["enforced_at"] is not None
        first_enforced_at = body1["enforced_at"]

        # Updating only grace_period_days while still enforced preserves enforced_at
        resp2 = await client.put(
            "/api/v1/tenants/current/mfa-policy",
            headers={"Authorization": f"Bearer {token}"},
            json={"enforced": True, "grace_period_days": 30},
        )
        assert resp2.status_code == 200
        body2 = await resp2.get_json()
        assert body2["grace_period_days"] == 30
        assert body2["enforced_at"] == first_enforced_at

        # Disabling clears enforced_at
        resp3 = await client.put(
            "/api/v1/tenants/current/mfa-policy",
            headers={"Authorization": f"Bearer {token}"},
            json={"enforced": False},
        )
        assert resp3.status_code == 200
        body3 = await resp3.get_json()
        assert body3["enforced"] is False
        assert body3["enforced_at"] is None

    async def test_put_policy_succeeds_on_bypass_domain_without_license_client(
        self,
    ) -> None:
        app, db = _make_app(license_tier=None)
        tenant_id, user_id = _seed_tenant_and_user(db, tenant_role="admin")
        token = _make_token(
            app, user_id=user_id, tenant_id=tenant_id, tenant_role="admin"
        )

        client = app.test_client()
        resp = await client.put(
            "/api/v1/tenants/current/mfa-policy",
            headers={
                "Authorization": f"Bearer {token}",
                "Host": "alpha.elderrms.app",
            },
            json={"enforced": True, "grace_period_days": 5},
        )
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["enforced"] is True
        assert body["license_tier"] == "enterprise"
        assert body["grace_period_days"] == 5

    async def test_put_policy_validates_grace_period_days_bounds(self) -> None:
        app, db = _make_app("enterprise")
        tenant_id, user_id = _seed_tenant_and_user(db, tenant_role="admin")
        token = _make_token(
            app, user_id=user_id, tenant_id=tenant_id, tenant_role="admin"
        )
        client = app.test_client()

        for invalid in (-1, 91):
            resp = await client.put(
                "/api/v1/tenants/current/mfa-policy",
                headers={"Authorization": f"Bearer {token}"},
                json={"enforced": True, "grace_period_days": invalid},
            )
            assert resp.status_code == 400

        for valid in (0, 90):
            resp = await client.put(
                "/api/v1/tenants/current/mfa-policy",
                headers={"Authorization": f"Bearer {token}"},
                json={"enforced": True, "grace_period_days": valid},
            )
            assert resp.status_code == 200
            body = await resp.get_json()
            assert body["grace_period_days"] == valid


@pytest.mark.asyncio
class TestMfaDisableAndConfirm:
    """disable_mfa enforcement and /mfa/confirm TOTP verification."""

    async def test_disable_mfa_blocked_403_when_enforced(self) -> None:
        app, db = _make_app("enterprise")
        secret = pyotp.random_base32()
        tenant_id, user_id = _seed_tenant_and_user(
            db,
            settings={"mfa_enforced": True, "mfa_grace_period_days": 7},
            mfa_secret=secret,
        )
        token = _make_token(app, user_id=user_id, tenant_id=tenant_id)

        client = app.test_client()
        resp = await client.post(
            "/api/v1/portal-auth/mfa/disable",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        body = await resp.get_json()
        assert "Enterprise security policy" in body["error"]
        assert db.portal_users[user_id].mfa_secret == secret

    async def test_disable_mfa_allowed_when_not_enforced(self) -> None:
        app, db = _make_app("enterprise")
        secret = pyotp.random_base32()
        tenant_id, user_id = _seed_tenant_and_user(
            db,
            settings={"mfa_enforced": False},
            mfa_secret=secret,
        )
        token = _make_token(app, user_id=user_id, tenant_id=tenant_id)

        client = app.test_client()
        resp = await client.post(
            "/api/v1/portal-auth/mfa/disable",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert db.portal_users[user_id].mfa_secret is None

    async def test_confirm_mfa_valid_and_invalid_totp(self) -> None:
        app, db = _make_app("enterprise")
        secret = pyotp.random_base32()
        tenant_id, user_id = _seed_tenant_and_user(db, mfa_secret=secret)
        token = _make_token(app, user_id=user_id, tenant_id=tenant_id)
        client = app.test_client()

        valid_code = pyotp.TOTP(secret).now()
        resp_ok = await client.post(
            "/api/v1/portal-auth/mfa/confirm",
            headers={"Authorization": f"Bearer {token}"},
            json={"totp_code": valid_code},
        )
        assert resp_ok.status_code == 200
        assert (await resp_ok.get_json()) == {"verified": True}

        resp_bad = await client.post(
            "/api/v1/portal-auth/mfa/confirm",
            headers={"Authorization": f"Bearer {token}"},
            json={"totp_code": "000000"},
        )
        assert resp_bad.status_code == 400
        bad_body = await resp_bad.get_json()
        assert bad_body["verified"] is False


class TestGracePeriodCalculation:
    """Per-user grace deadline anchored to max(mfa_enforced_at, user.created_at)."""

    def test_existing_user_anchored_to_enforced_at(self) -> None:
        db = _FakeDB()
        enforced_at = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
        user_created_at = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
        tenant_id, _ = _seed_tenant_and_user(
            db,
            settings={
                "mfa_enforced": True,
                "mfa_enforced_at": enforced_at.isoformat(),
                "mfa_grace_period_days": 7,
            },
            user_created_at=user_created_at,
        )

        # 2 days after enforcement -> 5 days remaining, not locked out
        before_deadline = enforced_at + timedelta(days=2)
        status = compute_user_mfa_status(
            tenant_id,
            mfa_enabled=False,
            user_created_at=user_created_at,
            db=db,
            now=before_deadline,
        )
        assert status["mfa_enforced"] is True
        assert status["mfa_setup_required"] is True
        assert (
            status["mfa_grace_expires_at"]
            == (enforced_at + timedelta(days=7)).isoformat()
        )
        assert status["mfa_grace_days_remaining"] == 5
        assert status["mfa_locked_out"] is False

        # 7 days and 1 second after enforcement -> locked out, 0 days remaining
        after_deadline = enforced_at + timedelta(days=7, seconds=1)
        expired_status = compute_user_mfa_status(
            tenant_id,
            mfa_enabled=False,
            user_created_at=user_created_at,
            db=db,
            now=after_deadline,
        )
        assert expired_status["mfa_grace_days_remaining"] == 0
        assert expired_status["mfa_locked_out"] is True

    def test_new_user_registered_after_enforcement_anchored_to_user_created_at(
        self,
    ) -> None:
        db = _FakeDB()
        enforced_at = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)
        user_created_at = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
        tenant_id, _ = _seed_tenant_and_user(
            db,
            settings={
                "mfa_enforced": True,
                "mfa_enforced_at": enforced_at.isoformat(),
                "mfa_grace_period_days": 7,
            },
            user_created_at=user_created_at,
        )

        # 9 days after enforcement (past original grace) but only 4 days after user signup
        now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
        status = compute_user_mfa_status(
            tenant_id,
            mfa_enabled=False,
            user_created_at=user_created_at,
            db=db,
            now=now,
        )
        assert (
            status["mfa_grace_expires_at"]
            == (user_created_at + timedelta(days=7)).isoformat()
        )
        assert status["mfa_grace_days_remaining"] == 3
        assert status["mfa_locked_out"] is False

    def test_zero_day_grace_period_locks_out_immediately(self) -> None:
        db = _FakeDB()
        enforced_at = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
        tenant_id, _ = _seed_tenant_and_user(
            db,
            settings={
                "mfa_enforced": True,
                "mfa_enforced_at": enforced_at.isoformat(),
                "mfa_grace_period_days": 0,
            },
        )
        status = compute_user_mfa_status(
            tenant_id,
            mfa_enabled=False,
            user_created_at=enforced_at - timedelta(days=1),
            db=db,
            now=enforced_at,
        )
        assert status["mfa_setup_required"] is True
        assert status["mfa_grace_days_remaining"] == 0
        assert status["mfa_locked_out"] is True

    def test_enrolled_user_clears_setup_required_and_lockout(self) -> None:
        db = _FakeDB()
        enforced_at = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
        tenant_id, _ = _seed_tenant_and_user(
            db,
            settings={
                "mfa_enforced": True,
                "mfa_enforced_at": enforced_at.isoformat(),
                "mfa_grace_period_days": 0,
            },
        )
        status = compute_user_mfa_status(
            tenant_id,
            mfa_enabled=True,
            user_created_at=enforced_at,
            db=db,
            now=enforced_at + timedelta(days=30),
        )
        assert status == {
            "mfa_enforced": True,
            "mfa_setup_required": False,
            "mfa_grace_expires_at": None,
            "mfa_grace_days_remaining": None,
            "mfa_locked_out": False,
        }

    def test_passkey_credential_satisfies_mfa_enrollment(self) -> None:
        db = _FakeDB()
        passkeys_table = _FakeTable("passkeys", ["id", "portal_user_id", "is_active"])
        db.passkeys = passkeys_table  # type: ignore[attr-defined]
        db._tables["passkeys"] = passkeys_table

        _, user_id = _seed_tenant_and_user(db, mfa_secret=None)
        user = db.portal_users[user_id]
        assert user_has_mfa_enrolled(user, db=db) is False

        # Inactive passkey does not count
        passkeys_table.insert(portal_user_id=user_id, is_active=False)
        assert user_has_mfa_enrolled(user, db=db) is False

        # Active passkey satisfies MFA enrollment
        passkeys_table.insert(portal_user_id=user_id, is_active=True)
        assert user_has_mfa_enrolled(user, db=db) is True


@pytest.mark.asyncio
class TestPortalMeAndLoginMfaFlags:
    """mfa_enforced, mfa_setup_required, grace & lockout fields on /me and /login."""

    async def test_me_and_login_expose_mfa_grace_and_lockout_fields(self) -> None:
        app, db = _make_app("enterprise")
        enforced_at = datetime.now(UTC) - timedelta(days=10)
        tenant_id, user_id = _seed_tenant_and_user(
            db,
            settings={
                "mfa_enforced": True,
                "mfa_enforced_at": enforced_at.isoformat(),
                "mfa_grace_period_days": 7,
            },
            user_created_at=enforced_at - timedelta(days=5),
            mfa_secret=None,
            password="Password123!",
        )
        token = _make_token(app, user_id=user_id, tenant_id=tenant_id)
        client = app.test_client()

        me_resp = await client.get(
            "/api/v1/portal-auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200
        me_body = await me_resp.get_json()
        assert me_body["mfa_enforced"] is True
        assert me_body["mfa_setup_required"] is True
        assert me_body["mfa_grace_days_remaining"] == 0
        assert me_body["mfa_locked_out"] is True
        assert me_body["mfa_grace_expires_at"] is not None

        login_resp = await client.post(
            "/api/v1/portal-auth/login",
            json={
                "tenant": "acme",
                "email": "user@acme.com",
                "password": "Password123!",
            },
        )
        assert login_resp.status_code == 200
        login_body = await login_resp.get_json()
        assert login_body["mfa_enforced"] is True
        assert login_body["mfa_setup_required"] is True
        assert login_body["mfa_grace_days_remaining"] == 0
        assert login_body["mfa_locked_out"] is True
