"""Unit tests for the rejection and identity-resolution branches of jwt_handler.

Complements test_jwt_handler_hardening / test_service_worker_auth: machine
token claim validation (service_worker, coderun, service_researcher), remote
worker ``ewk_`` token lookup against the real DB (incl. tenant binding),
portal-user and identity resolution, and minting-argument validation. Tokens
are signed at runtime with the test app's own key; nothing is a literal.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import jwt
import pytest
from quart import g
from werkzeug.security import generate_password_hash

from apps.api.auth import jwt_handler as jh
from apps.api.modules.runs.permissions import ALL_GRANTABLE_SCOPES

ISS = "elder-api"
AUD = "elder-api"
WORKER_SUB = "svc-scanner"
WORKER_SCOPE = "discovery:read"
GRANTABLE = sorted(ALL_GRANTABLE_SCOPES)[0]


def _sfx():
    """Unique suffix for DB rows."""
    return uuid4().hex[:10]


def _now():
    """Current epoch seconds."""
    return int(datetime.now(UTC).timestamp())


def _sign(app, payload):
    """Sign a payload with the app's JWT key."""
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    return jwt.encode(payload, secret, algorithm=app.config["JWT_ALGORITHM"])


def _worker_claims(**over):
    """Valid service_worker claims, with overrides (None removes a claim)."""
    now = _now()
    base = {
        "sub": WORKER_SUB,
        "type": "service_worker",
        "iss": ISS,
        "aud": AUD,
        "iat": now,
        "exp": now + 300,
        "tenant": "system",
        "scope": [WORKER_SCOPE],
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def _coderun_claims(**over):
    """Valid coderun claims, with overrides (None removes a claim)."""
    now = _now()
    base = {
        "sub": "coderun:1:2:x",
        "type": "coderun",
        "iss": ISS,
        "aud": AUD,
        "iat": now,
        "exp": now + 120,
        "tenant": "5",
        "function_id": 2,
        "execution_id": "exec-1",
        "scope": [GRANTABLE],
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def _researcher_claims(**over):
    """Valid service_researcher claims, with overrides (None removes a claim)."""
    now = _now()
    base = {
        "sub": "service-researcher:5:9",
        "type": "service_researcher",
        "iss": ISS,
        "aud": AUD,
        "iat": now,
        "exp": now + 120,
        "tenant": "5",
        "scope": ["entities:read"],
        "delegated_by": "9",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


class TestServiceWorkerClaims:
    """_validate_service_worker_claims accepts only tightly scoped machine tokens."""

    def test_valid_claims_are_normalized(self):
        """A valid token gets normalized tenant, roles and de-duplicated scopes."""
        out = jh._validate_service_worker_claims(
            _worker_claims(tenant=" 007 ", scope=f"{WORKER_SCOPE} {WORKER_SCOPE}"),
            ISS,
            AUD,
        )
        assert out["tenant"] == "7"
        assert out["roles"] == ["service_worker"]
        assert out["scope"] == [WORKER_SCOPE]

    @pytest.mark.parametrize(
        "over",
        [
            {"iss": "other-issuer"},
            {"aud": "other-aud"},
            {"iss": None},
            {"exp": None},
            {"iat": "soon"},
            {"exp": True},
            {"iat": _now() + 10_000, "exp": _now() + 10_100},
            {"iat": _now(), "exp": _now() + 601},
            {"iat": _now() + 100, "exp": _now() + 50},
            {"sub": "svc-unknown"},
            {"sub": 7},
            {"scope": None},
            {"scope": {"a": 1}},
            {"scope": [WORKER_SCOPE, 5]},
            {"scope": ["secrets:admin"]},
            {"scope": ["  "]},
            {"scope": []},
            {"tenant": None},
            {"tenant": True},
            {"tenant": 5},
            {"tenant": "0"},
            {"tenant": "-3"},
            {"tenant": "abc"},
        ],
    )
    def test_rejections(self, over):
        """Every malformed/over-privileged service_worker claim set is rejected."""
        assert (
            jh._validate_service_worker_claims(_worker_claims(**over), ISS, AUD) is None
        )


class TestCoderunClaims:
    """_validate_coderun_claims enforces tenant lock, TTL and grantable scopes."""

    def test_valid_string_scope(self):
        """A space-separated scope string is accepted and normalized to a list."""
        out = jh._validate_coderun_claims(
            _coderun_claims(scope=f"{GRANTABLE} {GRANTABLE}"), ISS, AUD
        )
        assert out is not None
        assert out["scope"] == [GRANTABLE]
        assert str(out["tenant"]) == "5"

    @pytest.mark.parametrize(
        "over",
        [
            {"iss": "evil"},
            {"aud": None},
            {"exp": "later"},
            {"iat": None},
            {"iat": _now(), "exp": _now() + 361},
            {"iat": _now() + 5000, "exp": _now() + 5100},
            {"iat": _now() + 10, "exp": _now() + 5},
            {"tenant": None},
            {"tenant": True},
            {"tenant": "0"},
            {"tenant": "x1"},
            {"function_id": 0},
            {"function_id": True},
            {"function_id": "2"},
            {"execution_id": "   "},
            {"execution_id": 12},
            {"scope": None},
            {"scope": 5},
            {"scope": [GRANTABLE, None]},
            {"scope": ["not:grantable:scope"]},
            {"scope": []},
        ],
    )
    def test_rejections(self, over):
        """Every malformed/over-privileged coderun claim set is rejected."""
        assert jh._validate_coderun_claims(_coderun_claims(**over), ISS, AUD) is None


class TestVerifyTokenMachineTypes:
    """verify_token end to end for signed machine tokens."""

    @pytest.mark.asyncio
    async def test_valid_researcher_token_is_normalized(self, app):
        """A valid read-only researcher token verifies with viewer role."""
        async with app.app_context():
            out = jh.verify_token(
                _sign(app, _researcher_claims(scope="entities:read", tenant=" 5 "))
            )
        assert out["roles"] == ["viewer"]
        assert out["scope"] == ["entities:read"]
        assert out["tenant"] == "5"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "over",
        [
            {"iss": None},
            {"aud": None},
            {"iss": "someone-else"},
            {"aud": "someone-else"},
            {"exp": _now() + 3000, "iat": None},
            {"iat": "yesterday"},
            {"iat": _now() - 3000},
            {"tenant": None},
            {"tenant": True},
            {"tenant": "abc"},
            {"tenant": "0"},
            {"scope": None},
            {"scope": 4},
            {"scope": []},
            {"scope": ["entities:write"]},
            {"scope": ["*:read"]},
            {"scope": ["noseparator"]},
            {"scope": [7]},
            {"delegated_by": None},
            {"delegated_by": "  "},
        ],
    )
    async def test_researcher_rejections(self, app, over):
        """Researcher tokens that are over-long, unscoped or not read-only are refused."""
        async with app.app_context():
            assert jh.verify_token(_sign(app, _researcher_claims(**over))) is None

    @pytest.mark.asyncio
    async def test_researcher_without_exp_rejected(self, app):
        """A researcher token with no expiry is never accepted."""
        claims = _researcher_claims()
        del claims["exp"]
        async with app.app_context():
            assert jh.verify_token(_sign(app, claims)) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "claims, ok",
        [
            (_worker_claims(), True),
            (_worker_claims(scope=["secrets:admin"]), False),
            (_coderun_claims(), True),
            (_coderun_claims(function_id=-1), False),
        ],
    )
    async def test_worker_and_coderun_dispatch(self, app, claims, ok):
        """verify_token routes worker/coderun tokens through their validators."""
        async with app.app_context():
            out = jh.verify_token(_sign(app, claims))
        assert (out is not None) is ok

    @pytest.mark.asyncio
    async def test_expired_and_tampered_tokens(self, app):
        """Expired and wrong-key tokens both verify to None."""
        now = _now()
        expired = {"sub": "1", "iat": now - 100, "exp": now - 10}
        forged = jwt.encode(
            {"sub": "1", "exp": now + 60},
            "-".join(["not", "the", "key", _sfx()]) * 3,
            algorithm="HS256",
        )
        async with app.app_context():
            assert jh.verify_token(_sign(app, expired)) is None
            assert jh.verify_token(forged) is None
            assert jh.verify_token("definitely.not.ajwt") is None


class TestRemoteWorkerToken:
    """resolve_remote_worker_token against the real remote_workers table."""

    def _insert_worker(self, db, token, **over):
        """Insert a remote worker bound to ``token``; return its row id."""
        sfx = _sfx()
        now = datetime.now(UTC)
        fields = {
            "worker_id": f"cov-w-{sfx}",
            "worker_type": "scanner",
            "name": f"cov worker {sfx}",
            "token_hash": jh.hash_worker_token(token),
            "is_enabled": True,
            "created_at": now,
            "updated_at": now,
        }
        fields.update(over)
        rid = db.remote_workers.insert(**fields)
        db.commit()
        return rid

    def _token(self):
        """Build a runtime worker token with the provisioned prefix."""
        return jh.WORKER_TOKEN_PREFIX + uuid4().hex

    @pytest.mark.asyncio
    async def test_system_worker_resolves(self, app):
        """An enabled tenant-less worker resolves to system-scoped claims."""
        token = self._token()
        async with app.app_context():
            rid = self._insert_worker(app.db, token, cluster="c1")
            claims = jh.verify_token(token)
        assert claims["sub"] == "svc-scanner"
        assert claims["tenant"] == "system"
        assert claims["remote_worker_db_id"] == rid
        assert claims["remote_worker_cluster"] == "c1"
        assert set(claims["scope"]) == set(
            jh.SERVICE_WORKER_ALLOWED_SCOPES["svc-scanner"]
        )

    @pytest.mark.asyncio
    async def test_tenant_bound_worker_carries_own_tenant_only(self, app):
        """Two tenants' workers each resolve to their own tenant, never the other's."""
        db = app.db
        tok_a, tok_b = self._token(), self._token()
        async with app.app_context():
            tenants = []
            for key in ("a", "b"):
                sfx = _sfx()
                tenants.append(
                    db.tenants.insert(
                        name=f"cov-jwt-{key}-{sfx}",
                        slug=f"cov-jwt-{key}-{sfx}",
                        is_active=True,
                    )
                )
            db.commit()
            self._insert_worker(db, tok_a, tenant_id=tenants[0])
            self._insert_worker(db, tok_b, tenant_id=tenants[1])
            claims_a = jh.resolve_remote_worker_token(tok_a)
            claims_b = jh.resolve_remote_worker_token(tok_b)
        assert claims_a["tenant"] == str(tenants[0])
        assert claims_b["tenant"] == str(tenants[1])
        assert claims_a["tenant"] != claims_b["tenant"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "over",
        [
            {"is_enabled": False},
            {"revoked_at": datetime.now(UTC)},
            {"worker_type": "mystery"},
        ],
    )
    async def test_disabled_revoked_or_unknown_type_rejected(self, app, over):
        """Disabled, revoked or unsupported-type workers cannot authenticate."""
        token = self._token()
        async with app.app_context():
            self._insert_worker(app.db, token, **over)
            assert jh.resolve_remote_worker_token(token) is None

    @pytest.mark.asyncio
    async def test_unknown_and_malformed_tokens(self, app):
        """Unknown tokens, wrong prefixes and non-strings resolve to None."""
        async with app.app_context():
            assert jh.resolve_remote_worker_token(self._token()) is None
            assert jh.resolve_remote_worker_token("bearer-" + uuid4().hex) is None
            assert jh.resolve_remote_worker_token(None) is None

    @pytest.mark.asyncio
    async def test_missing_table_and_lookup_failure(self, app):
        """No remote_workers table or a failing query both deny (fail closed)."""
        token = self._token()
        async with app.app_context():
            with patch.object(jh, "current_app", SimpleNamespace(db=None)):
                assert jh.resolve_remote_worker_token(token) is None
            broken = MagicMock(side_effect=RuntimeError("db down"))
            broken.remote_workers = MagicMock()
            broken.rollback.side_effect = RuntimeError("no rollback")
            with patch.object(jh, "current_app", SimpleNamespace(db=broken)):
                assert jh.resolve_remote_worker_token(token) is None
            broken.rollback.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_type_without_scope_allowlist_rejected(self, app):
        """A worker type mapped to a sub with no scope allowlist is denied."""
        token = self._token()
        async with app.app_context():
            self._insert_worker(app.db, token)
            with patch.dict(jh.WORKER_TYPE_TO_SERVICE_SUB, {"scanner": "svc-nobody"}):
                assert jh.resolve_remote_worker_token(token) is None


class TestGetCurrentUserMachine:
    """get_current_user builds synthetic identities for machine tokens."""

    async def _resolve(self, app, payload):
        """Run get_current_user with verify_token returning ``payload``."""
        async with app.test_request_context(
            "/x", headers={"Authorization": "Bearer placeholder"}
        ):
            with patch.object(jh, "verify_token", return_value=payload):
                user = jh.get_current_user()
            cached = getattr(g, "current_user", None)
        return user, cached

    @pytest.mark.asyncio
    async def test_researcher_identity(self, app):
        """A researcher token yields a read-only, tenant-bound synthetic identity."""
        user, cached = await self._resolve(
            app,
            _researcher_claims(
                scope=["entities:read", "entities:write"], act={"sub": "9"}
            ),
        )
        assert user is cached
        assert user.tenant_id == 5
        assert user.scopes == ["entities:read"]
        assert user.get("delegated_by") == "9"
        assert user.get("missing", "dflt") == "dflt"
        assert user.is_superuser is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "over",
        [
            {"tenant": "0"},
            {"scope": ["entities:write"]},
            {"delegated_by": ""},
            {"delegated_by": 9},
            {"tenant": "not-int"},
        ],
    )
    async def test_researcher_invalid_payload_denied(self, app, over):
        """Researcher payloads lacking tenant, read scopes or delegator are denied."""
        user, cached = await self._resolve(app, _researcher_claims(**over))
        assert user is None and cached is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tenant, expected", [("system", None), ("12", 12)])
    async def test_worker_identity(self, app, tenant, expected):
        """Worker tokens map to a ServiceWorkerIdentity with filtered scopes."""
        user, _ = await self._resolve(
            app,
            _worker_claims(
                tenant=tenant,
                scope=[WORKER_SCOPE, "secrets:admin"],
                remote_worker_id="w1",
            ),
        )
        assert isinstance(user, jh.ServiceWorkerIdentity)
        assert user.tenant_id == expected
        assert user.scopes == [WORKER_SCOPE]
        assert user.get("remote_worker_id") == "w1"
        assert user.get("nope", 3) == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "over",
        [
            {"sub": "svc-unknown"},
            {"tenant": "0"},
            {"tenant": 12},
            {"tenant": "x"},
            {"scope": ["secrets:admin"]},
            {"scope": None},
        ],
    )
    async def test_worker_invalid_payload_denied(self, app, over):
        """Worker payloads with unknown sub, bad tenant or no allowed scope are denied."""
        user, _ = await self._resolve(app, _worker_claims(**over))
        assert user is None

    @pytest.mark.asyncio
    async def test_coderun_identity(self, app):
        """Coderun tokens map to a CodeRunIdentity locked to tenant and function."""
        user, _ = await self._resolve(
            app, _coderun_claims(scope=[GRANTABLE, "bogus:scope"], execution_id=" e9 ")
        )
        assert isinstance(user, jh.CodeRunIdentity)
        assert user.tenant_id == 5
        assert user.scopes == [GRANTABLE]
        assert user.get("execution_id") == "e9"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "over",
        [
            {"tenant": "0"},
            {"tenant": 5},
            {"function_id": 0},
            {"function_id": True},
            {"execution_id": ""},
            {"execution_id": None},
            {"scope": ["bogus:scope"]},
        ],
    )
    async def test_coderun_invalid_payload_denied(self, app, over):
        """Coderun payloads with bad tenant/function/execution/scope are denied."""
        user, _ = await self._resolve(app, _coderun_claims(**over))
        assert user is None

    @pytest.mark.asyncio
    async def test_is_system_service_worker(self, app):
        """Only system-tenant worker claims plus a tenant-less worker identity qualify."""
        system_claims = {"type": "service_worker", "tenant": "system"}
        async with app.test_request_context("/x"):
            g.claims = system_claims
            g.current_user = jh.ServiceWorkerIdentity(WORKER_SUB, None, [WORKER_SCOPE])
            assert jh.is_system_service_worker() is True
            g.current_user = jh.ServiceWorkerIdentity(WORKER_SUB, 4, [WORKER_SCOPE])
            assert jh.is_system_service_worker() is False
            g.current_user = jh.ServiceWorkerIdentity(WORKER_SUB, None, [WORKER_SCOPE])
            g.claims = {"type": "service_worker", "tenant": "4"}
            assert jh.is_system_service_worker() is False
            g.claims = {"type": "access", "tenant": "system"}
            assert jh.is_system_service_worker() is False


class TestGetCurrentUserDb:
    """get_current_user for portal users and identities stored in the real DB."""

    def _tenant(self, db):
        """Insert a tenant and return its id."""
        sfx = _sfx()
        tid = db.tenants.insert(
            name=f"cov-gcu-{sfx}", slug=f"cov-gcu-{sfx}", is_active=True
        )
        db.commit()
        return tid

    def _portal_user(self, db, tid, **over):
        """Insert a portal user and return its id."""
        fields = {
            "tenant_id": tid,
            "email": f"cov-pu-{_sfx()}@example.com",
            "is_active": True,
            "email_verified": True,
            "global_role": "admin",
            "full_name": None,
        }
        fields.update(over)
        pid = db.portal_users.insert(**fields)
        db.commit()
        return pid

    async def _resolve(self, app, payload):
        """Resolve the current user for a signed token carrying ``payload``."""
        now = _now()
        token = _sign(
            app, {"iat": now, "exp": now + 60, "iss": ISS, "aud": AUD, **payload}
        )
        async with app.test_request_context(
            "/x", headers={"Authorization": f"Bearer {token}"}
        ):
            return jh.get_current_user()

    @pytest.mark.asyncio
    async def test_portal_user_identity(self, app):
        """An active portal user becomes a synthetic identity in its own tenant."""
        db = app.db
        async with app.app_context():
            tid = self._tenant(db)
            pid = self._portal_user(db, tid)
        user = await self._resolve(app, {"sub": str(pid), "type": "portal_user"})
        assert user.id == pid
        assert user.tenant_id == tid
        assert user.is_superuser is True
        assert user.display_name == user.username == user.email.split("@")[0]
        assert user.get("portal_role") == "admin"

    @pytest.mark.asyncio
    async def test_portal_user_inactive_or_missing_denied(self, app):
        """Inactive or non-existent portal users are not authenticated."""
        db = app.db
        async with app.app_context():
            tid = self._tenant(db)
            pid = self._portal_user(db, tid, is_active=False, global_role=None)
        assert (
            await self._resolve(app, {"sub": str(pid), "type": "portal_user"}) is None
        )
        assert (
            await self._resolve(app, {"sub": "2000000100", "type": "portal_user"})
            is None
        )

    @pytest.mark.asyncio
    async def test_identity_missing_inactive_and_bad_sub_denied(self, app):
        """Unknown, deactivated or non-numeric subjects are not authenticated."""
        db = app.db
        now = datetime.now(UTC)
        async with app.app_context():
            tid = self._tenant(db)
            sfx = _sfx()
            iid = db.identities.insert(
                identity_type="human",
                username=f"cov-gcu-{sfx}",
                email=f"cov-gcu-{sfx}@example.com",
                tenant_id=tid,
                auth_provider="local",
                is_active=False,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="observer",
                created_at=now,
                updated_at=now,
            )
            db.commit()
        assert await self._resolve(app, {"sub": str(iid)}) is None
        assert await self._resolve(app, {"sub": "2000000101"}) is None
        assert await self._resolve(app, {"sub": "not-a-number"}) is None

    @pytest.mark.asyncio
    async def test_lookup_error_with_failing_rollback_denied(self, app):
        """A DB error during lookup (and a failing rollback) still denies cleanly."""
        broken = MagicMock()
        broken.identities.__getitem__.side_effect = RuntimeError("db down")
        broken.rollback.side_effect = RuntimeError("rollback failed")
        async with app.test_request_context(
            "/x", headers={"Authorization": "Bearer placeholder"}
        ):
            with (
                patch.object(jh, "verify_token", return_value={"sub": "1"}),
                patch.object(jh, "current_app", SimpleNamespace(db=broken)),
            ):
                assert jh.get_current_user() is None
        broken.rollback.assert_called_once_with()


class TestMintingValidation:
    """Token minting helpers reject invalid arguments before signing."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kwargs, message",
        [
            ({"scopes": None}, "non-empty sequence"),
            ({"scopes": GRANTABLE}, "non-empty sequence"),
            ({"scopes": [GRANTABLE, "  "]}, "Invalid coderun scope"),
            ({"scopes": [5]}, "Invalid coderun scope"),
            ({"scopes": ["bogus:scope"]}, "not grantable"),
            ({"scopes": []}, "At least one grantable scope"),
            ({"tenant_id": 0}, "tenant_id"),
            ({"function_id": True}, "function_id"),
            ({"execution_id": " "}, "execution_id"),
            ({"ttl_seconds": 361}, "TTL"),
        ],
    )
    async def test_generate_coderun_token_rejects(self, app, kwargs, message):
        """Invalid coderun minting arguments raise ValueError."""
        args = {
            "tenant_id": 1,
            "function_id": 2,
            "execution_id": "e1",
            "scopes": [GRANTABLE],
            **kwargs,
        }
        async with app.app_context():
            with pytest.raises(ValueError, match=message):
                jh.generate_coderun_token(**args)

    @pytest.mark.asyncio
    async def test_generate_coderun_token_roundtrip(self, app):
        """A minted coderun token verifies and de-duplicates scopes."""
        async with app.app_context():
            token = jh.generate_coderun_token(3, 4, " e7 ", [GRANTABLE, GRANTABLE], 60)
            claims = jh.verify_token(token)
        assert claims["sub"] == "coderun:3:4:e7"
        assert claims["scope"] == [GRANTABLE]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kwargs, message",
        [
            ({"tenant_id": -1}, "tenant_id"),
            ({"tenant_id": True}, "tenant_id"),
            ({"ttl_seconds": 0}, "TTL"),
            ({"caller_sub": None}, "caller_sub"),
            ({"caller_sub": True}, "caller_sub"),
            ({"caller_sub": "  "}, "caller_sub"),
            ({"scopes": None}, "non-empty sequence"),
            ({"scopes": "entities:read"}, "non-empty sequence"),
            ({"scopes": ["entities:write"]}, "only allow"),
            ({"scopes": [3]}, "only allow"),
            ({"scopes": ["plain"]}, "only allow"),
            ({"scopes": []}, "At least one read-only scope"),
        ],
    )
    async def test_generate_researcher_token_rejects(self, app, kwargs, message):
        """Invalid researcher minting arguments raise ValueError."""
        args = {
            "tenant_id": 1,
            "scopes": ["entities:read"],
            "caller_sub": "9",
            **kwargs,
        }
        async with app.app_context():
            with pytest.raises(ValueError, match=message):
                jh.generate_service_researcher_token(**args)

    @pytest.mark.asyncio
    async def test_generate_token_refresh_has_no_scopes(self, app):
        """Refresh tokens use the refresh lifetime and carry no scopes."""
        identity = SimpleNamespace(
            id=77, username="cov-user", portal_role=None, tenant_id=4
        )
        async with app.app_context():
            refresh = jh.verify_token(jh.generate_token(identity, "refresh"))
            access = jh.verify_token(jh.generate_token(identity))
            delta = app.config["JWT_REFRESH_TOKEN_EXPIRES"].total_seconds()
        assert refresh["type"] == "refresh"
        assert refresh["scope"] == []
        assert refresh["exp"] - refresh["iat"] == pytest.approx(delta, abs=2)
        assert access["scope"] and access["roles"] == ["observer"]
        assert access["tenant"] == "4"

    def test_verify_password(self):
        """Passwords check against the stored hash; no hash never authenticates."""
        secret = "-".join(["pw", _sfx()])
        with_hash = SimpleNamespace(password_hash=generate_password_hash(secret))
        assert jh.verify_password(with_hash, secret) is True
        assert jh.verify_password(with_hash, secret + "x") is False
        assert jh.verify_password(SimpleNamespace(password_hash=None), secret) is False

    def test_read_scope_predicate(self):
        """Only concrete ``<resource>:read`` strings are read scopes."""
        assert jh._is_valid_read_scope(" entities:read ") is True
        for bad in (None, 5, "entities", "*:read", ":read", "entities:write"):
            assert jh._is_valid_read_scope(bad) is False
