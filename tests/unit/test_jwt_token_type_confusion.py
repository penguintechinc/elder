"""Token-type confusion hardening tests for the JWT verifiers.

Every verification context must accept only its own token ``type`` values and
resolve the subject only in the principal table matching that type. A token of
another type (refresh, portal, service, coderun, or a missing/unknown type)
must be rejected, never treated as an identity access token.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest

from apps.api.auth.jwt_handler import (
    SERVICE_WORKER_ALLOWED_SCOPES,
    CodeRunIdentity,
    ServiceWorkerIdentity,
    get_current_user,
    verify_token,
)
from apps.api.auth.web_auth import COOKIE_NAME, get_web_user

TOKEN_TYPES = [
    "access",
    "refresh",
    "portal_user",
    "portal_refresh",
    "service_worker",
    "coderun",
    "service_researcher",
    "missing",
    "unknown",
]

# verifier name -> token types it must accept (everything else is rejected)
VERIFIER_ACCEPTS: dict[str, frozenset[str]] = {
    "api_bearer": frozenset(
        {"access", "portal_user", "service_worker", "coderun", "service_researcher"}
    ),
    "web_session": frozenset({"access"}),
    "identity_refresh": frozenset({"refresh"}),
    "service_worker": frozenset({"service_worker"}),
    "coderun": frozenset({"coderun"}),
    "grpc_access": frozenset({"access"}),
    "grpc_refresh": frozenset({"refresh"}),
    "portal_access": frozenset({"portal_user"}),
    "portal_refresh": frozenset({"portal_refresh"}),
}

MATRIX = [(t, v) for v in VERIFIER_ACCEPTS for t in TOKEN_TYPES]


def _secret(app) -> str:
    """Return the signing secret the app uses for its own JWTs."""
    return app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")


def _algorithm(app) -> str:
    """Return the signing algorithm the app uses for its own JWTs."""
    return app.config.get("JWT_ALGORITHM", "HS256")


def _mint(app, token_type: str, sub: str = "1") -> str:
    """Sign a well-formed test token of ``token_type`` (valid for its own context)."""
    from apps.api.modules.runs.permissions import ALL_GRANTABLE_SCOPES

    now = datetime.now(UTC)
    payload: dict = {
        "sub": sub,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=300)).timestamp()),
        "iss": app.config.get("JWT_ISSUER", "elder-api"),
        "aud": app.config.get("JWT_AUDIENCE", "elder-api"),
        "tenant": "1",
        "scope": [],
    }
    if token_type == "service_worker":
        payload.update(
            sub="svc-scanner",
            tenant="system",
            scope=sorted(SERVICE_WORKER_ALLOWED_SCOPES["svc-scanner"]),
        )
    elif token_type == "service_researcher":
        payload.update(
            sub="service-researcher:1",
            scope=["entities:read"],
            delegated_by="1",
        )
    elif token_type == "coderun":
        payload.update(
            sub="coderun:1:1:exec-1",
            function_id=1,
            execution_id="exec-1",
            roles=["coderun"],
            scope=[sorted(ALL_GRANTABLE_SCOPES)[0]],
        )
    if token_type == "unknown":
        payload["type"] = "mfa_challenge"
    elif token_type != "missing":
        payload["type"] = token_type
    return jwt.encode(payload, _secret(app), algorithm=_algorithm(app))


async def _run_verifier(app, verifier: str, token: str):
    """Run ``token`` through the named verification context; None == rejected."""
    from apps.api.auth.jwt_handler import (
        CODERUN_TOKEN_TYPES,
        IDENTITY_REFRESH_TOKEN_TYPES,
        SERVICE_WORKER_TOKEN_TYPES,
    )
    from apps.api.grpc.servicers.elder_servicer import ElderServicer

    grpc_self = SimpleNamespace(jwt_secret=_secret(app), jwt_algorithm=_algorithm(app))
    async with app.app_context():
        if verifier == "api_bearer":
            return verify_token(token)
        if verifier == "identity_refresh":
            return verify_token(token, IDENTITY_REFRESH_TOKEN_TYPES)
        if verifier == "service_worker":
            return verify_token(token, SERVICE_WORKER_TOKEN_TYPES)
        if verifier == "coderun":
            return verify_token(token, CODERUN_TOKEN_TYPES)
        if verifier in ("portal_access", "portal_refresh"):
            from apps.api.auth import jwt_handler

            allowed = (
                jwt_handler.PORTAL_ACCESS_TOKEN_TYPES
                if verifier == "portal_access"
                else jwt_handler.PORTAL_REFRESH_TOKEN_TYPES
            )
            return verify_token(token, allowed)
        if verifier == "grpc_access":
            return ElderServicer._verify_jwt_token(grpc_self, token)
        if verifier == "grpc_refresh":
            return ElderServicer._verify_jwt_token(grpc_self, token, "refresh")
    if verifier == "web_session":
        async with app.test_request_context(
            "/", headers={"Cookie": f"{COOKIE_NAME}={token}"}
        ):
            return get_web_user()
    raise AssertionError(f"unknown verifier {verifier}")


class TestTokenTypeVerifierMatrix:
    """Every (token type, verifier) pair is accepted iff the type belongs there."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("token_type", "verifier"), MATRIX)
    async def test_matrix(self, app, token_type, verifier):
        result = await _run_verifier(app, verifier, _mint(app, token_type))
        if token_type in VERIFIER_ACCEPTS[verifier]:
            assert result is not None
        else:
            assert result is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "minter", ["access", "refresh", "portal", "portal_refresh"]
    )
    @pytest.mark.parametrize(
        "verifier", [v for v in VERIFIER_ACCEPTS if v != "web_session"]
    )
    async def test_real_minters_with_auth_time(self, app, minter, verifier):
        """Tokens from the production minters (stamped with auth_time) land
        only in their own verification context."""
        from apps.api.api.v1.portal_auth import generate_tokens
        from apps.api.auth.jwt_handler import generate_token, interactive_auth_time

        identity = SimpleNamespace(
            id=42, username="u", tenant_id=7, portal_role="observer", is_active=True
        )
        portal_user = {
            "id": 42,
            "email": "p@example.invalid",
            "tenant_id": 7,
            "tenant_role": "member",
            "global_role": None,
        }
        async with app.app_context():
            at = interactive_auth_time()
            if minter in ("access", "refresh"):
                token = generate_token(identity, minter, auth_time=at)
                token_type = minter
            else:
                pair = generate_tokens(portal_user, auth_time=at)
                token = pair[
                    "refresh_token" if minter == "portal_refresh" else "access_token"
                ]
                token_type = (
                    "portal_refresh" if minter == "portal_refresh" else "portal_user"
                )
        result = await _run_verifier(app, verifier, token)
        if token_type in VERIFIER_ACCEPTS[verifier]:
            assert result is not None and result["auth_time"] == at
        else:
            assert result is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verifier", list(VERIFIER_ACCEPTS))
    async def test_expired_and_malformed_rejected(self, app, verifier):
        now = datetime.now(UTC)
        expired = jwt.encode(
            {"sub": "1", "type": "access", "exp": now - timedelta(seconds=5)},
            _secret(app),
            algorithm=_algorithm(app),
        )
        assert await _run_verifier(app, verifier, expired) is None
        assert await _run_verifier(app, verifier, "not.a.jwt") is None

    @pytest.mark.asyncio
    async def test_mismatched_principal_kind_claim_rejected(self, app):
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": "1",
                "type": "access",
                "principal_kind": "portal_user",
                "exp": now + timedelta(seconds=300),
            },
            _secret(app),
            algorithm=_algorithm(app),
        )
        async with app.app_context():
            assert verify_token(token) is None


def _make_tenant(db) -> int:
    """Create a fresh, uniquely-slugged tenant."""
    now = datetime.now(UTC)
    tid = db.tenants.insert(
        name=f"TTC {uuid4().hex[:6]}",
        slug=f"ttc-{uuid4().hex[:8]}",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return tid


def _make_identity(db, tenant_id: int, row_id: int | None = None) -> int:
    """Create an active identity, optionally at a specific primary key."""
    now = datetime.now(UTC)
    fields = {
        "tenant_id": tenant_id,
        "username": f"ttc-{uuid4().hex[:8]}",
        "identity_type": "human",
        "auth_provider": "local",
        "is_active": True,
        "is_superuser": False,
        "mfa_enabled": False,
        "must_change_password": False,
        "portal_role": "observer",
        "created_at": now,
        "updated_at": now,
    }
    if row_id is not None:
        fields["id"] = row_id
    iid = db.identities.insert(**fields)
    db.commit()
    return iid


def _make_portal_user(db, tenant_id: int, row_id: int | None = None) -> int:
    """Create an active portal user, optionally at a specific primary key."""
    now = datetime.now(UTC)
    fields = {
        "tenant_id": tenant_id,
        "email": f"ttc-{uuid4().hex[:8]}@example.com",
        "is_active": True,
        "email_verified": True,
        "global_role": "user",
        "created_at": now,
        "updated_at": now,
    }
    if row_id is not None:
        fields["id"] = row_id
    pid = db.portal_users.insert(**fields)
    db.commit()
    return pid


async def _current_user_for(app, token: str):
    """Resolve the request principal for ``token`` sent as a bearer header."""
    async with app.test_request_context(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    ):
        return get_current_user()


class TestPrincipalKindBinding:
    """A token's subject resolves only in the table matching its type."""

    @pytest.fixture
    def shared_id_principals(self, app):
        """An identity and a portal user that share the same numeric id."""
        db = app.db
        tid = _make_tenant(db)
        iid = _make_identity(db, tid)
        # A high, random primary key free in both tables.
        nid = max(iid, 10_000_000) + int(uuid4().int % 1_000_000)
        iid2 = _make_identity(db, tid, row_id=nid)
        pid = _make_portal_user(db, tid, row_id=nid)
        assert iid2 == pid == nid
        return nid

    @pytest.mark.asyncio
    async def test_portal_refresh_never_authenticates_identity(
        self, app, shared_id_principals
    ):
        token = _mint(app, "portal_refresh", sub=str(shared_id_principals))
        assert await _current_user_for(app, token) is None

    @pytest.mark.asyncio
    async def test_identity_refresh_never_authenticates(
        self, app, shared_id_principals
    ):
        token = _mint(app, "refresh", sub=str(shared_id_principals))
        assert await _current_user_for(app, token) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("token_type", ["missing", "unknown"])
    async def test_untyped_token_never_authenticates(
        self, app, shared_id_principals, token_type
    ):
        token = _mint(app, token_type, sub=str(shared_id_principals))
        assert await _current_user_for(app, token) is None

    @pytest.mark.asyncio
    async def test_portal_access_resolves_portal_user_only(
        self, app, shared_id_principals
    ):
        token = _mint(app, "portal_user", sub=str(shared_id_principals))
        user = await _current_user_for(app, token)
        assert user is not None
        assert hasattr(user, "_portal_user")

    @pytest.mark.asyncio
    async def test_identity_access_resolves_identity_only(
        self, app, shared_id_principals
    ):
        token = _mint(app, "access", sub=str(shared_id_principals))
        user = await _current_user_for(app, token)
        assert user is not None
        assert not hasattr(user, "_portal_user")
        assert user.username.startswith("ttc-")

    @pytest.mark.asyncio
    async def test_service_tokens_never_resolve_stored_principal(self, app):
        worker = await _current_user_for(app, _mint(app, "service_worker"))
        assert isinstance(worker, ServiceWorkerIdentity)
        coderun = await _current_user_for(app, _mint(app, "coderun"))
        assert isinstance(coderun, CodeRunIdentity)


class TestRefreshTokenNotABearer:
    """Refresh tokens work only at their refresh endpoint."""

    @pytest.mark.asyncio
    async def test_identity_refresh_rejected_as_bearer_on_normal_route(self, app):
        db = app.db
        iid = _make_identity(db, _make_tenant(db))
        token = _mint(app, "refresh", sub=str(iid))
        async with app.test_client() as client:
            resp = await client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_identity_access_accepted_on_normal_route(self, app):
        db = app.db
        iid = _make_identity(db, _make_tenant(db))
        token = _mint(app, "access", sub=str(iid))
        async with app.test_client() as client:
            resp = await client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_coderun_and_worker_rejected_on_identity_route(self, app):
        for token_type in ("coderun", "service_worker"):
            async with app.test_client() as client:
                resp = await client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {_mint(app, token_type)}"},
                )
            assert resp.status_code in (401, 403, 404), token_type

    @pytest.mark.asyncio
    async def test_identity_refresh_still_works_at_refresh_endpoint(self, app):
        db = app.db
        iid = _make_identity(db, _make_tenant(db))
        token = _mint(app, "refresh", sub=str(iid))
        async with app.test_client() as client:
            resp = await client.post(
                "/api/v1/auth/refresh", json={"refresh_token": token}
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_portal_refresh_rejected_at_identity_refresh_endpoint(self, app):
        db = app.db
        tid = _make_tenant(db)
        pid = _make_portal_user(db, tid)
        token = _mint(app, "portal_refresh", sub=str(pid))
        async with app.test_client() as client:
            resp = await client.post(
                "/api/v1/auth/refresh", json={"refresh_token": token}
            )
        assert resp.status_code == 401


class TestPortalEndpointsRejectForeignTokens:
    """Portal routes accept only portal tokens, verified with iss/aud checks."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("token_type", ["refresh", "access", "missing"])
    async def test_portal_refresh_endpoint_rejects_non_portal_refresh(
        self, app, token_type
    ):
        async with app.test_client() as client:
            resp = await client.post(
                "/api/v1/portal-auth/refresh",
                json={"refresh_token": _mint(app, token_type)},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_portal_route_rejects_foreign_issuer(self, app):
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": "1",
                "type": "portal_user",
                "iss": "someone-else",
                "exp": now + timedelta(seconds=300),
            },
            _secret(app),
            algorithm=_algorithm(app),
        )
        async with app.test_client() as client:
            resp = await client.get(
                "/api/v1/portal-auth/me", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code == 401
