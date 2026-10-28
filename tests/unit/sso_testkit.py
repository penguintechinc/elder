"""Shared real-path fixtures and external-IdP fakes for the SSO hardening tests.

Everything except the *external* OIDC identity provider (its discovery, JWKS,
authorization, token and userinfo endpoints) runs for real: the Quart app, the
routes, Postgres (``DATABASE_URL``) and Redis (``REDIS_URL``). The fake provider
mimics a real one closely enough to enforce what the code under test must send
it (client credentials, PKCE verifier, nonce echoed into the ID token).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from quart import current_app

CALLBACK_REDIRECT_URI = "https://elder.example.test/api/v1/sso/oidc/callback"
BINDING_COOKIE = "elder_oidc_binding"
AUTHORIZE = "/api/v1/sso/oidc/authorize"
CALLBACK = "/api/v1/sso/oidc/callback"


@dataclass(slots=True)
class Begun:
    """What the browser holds after hitting ``/oidc/authorize``."""

    status: int
    body: dict[str, Any]
    cookie: str | None

    @property
    def authorization_url(self) -> str:
        """The URL the browser would be redirected to at the IdP."""
        return str(self.body["authorization_url"])

    @property
    def state(self) -> str:
        """The ``state`` Elder issued for this login."""
        return str(self.body["state"])


def cookie_from(resp: Any) -> str | None:
    """Pull the ``elder_oidc_binding`` value out of a response's Set-Cookie headers."""
    for header in resp.headers.get_all("Set-Cookie"):
        if header.startswith(f"{BINDING_COOKIE}="):
            return str(header.split(";", 1)[0].split("=", 1)[1])
    return None


async def begin(
    client: Any, idp_id: int, *, tenant_id: int | None = None, **extra: Any
) -> Begun:
    """Hit ``/oidc/authorize`` like the SPA does and capture the binding cookie."""
    query: dict[str, Any] = {"redirect_uri": CALLBACK_REDIRECT_URI, **extra}
    if tenant_id is not None:
        query["tenant_id"] = tenant_id
    resp = await client.get(f"{AUTHORIZE}/{idp_id}", query_string=query)
    body = await resp.get_json()
    return Begun(resp.status_code, body, cookie_from(resp))


async def finish(
    client: Any,
    b: Begun,
    code: str,
    *,
    idp_id: int | None = None,
    cookie: str | None = "from-begin",
    state: str | None = "from-begin",
    redirect_uri: str | None = CALLBACK_REDIRECT_URI,
    extra: dict[str, Any] | None = None,
) -> Any:
    """Deliver the IdP's redirect to ``/oidc/callback`` (optionally tampered)."""
    query: dict[str, Any] = {"code": code, **(extra or {})}
    st = b.state if state == "from-begin" else state
    if st is not None:
        query["state"] = st
    if idp_id is not None:
        query["idp_id"] = idp_id
    if redirect_uri is not None:
        query["redirect_uri"] = redirect_uri
    ck = b.cookie if cookie == "from-begin" else cookie
    headers = {"Cookie": f"{BINDING_COOKIE}={ck}"} if ck else {}
    return await client.get(CALLBACK, query_string=query, headers=headers)


async def login(
    client: Any,
    provider: "FakeOidcProvider",
    idp_id: int,
    email: str,
    *,
    tenant_id: int | None = None,
) -> Any:
    """Run the whole browser flow: authorize -> IdP login -> callback."""
    b = await begin(client, idp_id, tenant_id=tenant_id)
    assert b.status == 200, b.body
    code = provider.authenticate(b.authorization_url, email)
    return await finish(client, b, code, idp_id=idp_id)


@dataclass(slots=True)
class World:
    """Ids, tokens and secrets for two tenants, a platform scope and their SSO rows."""

    prefix: str
    tenant_a: int
    tenant_b: int
    tenant_sys: int
    a_user_email: str
    a_global_admin_email: str
    b_admin_email: str
    b_member_email: str
    a_user_id: int
    a_global_admin_id: int
    b_admin_id: int
    b_member_id: int
    scim_token_a: str
    scim_token_b: str
    a_oidc: int
    b_oidc: int
    g_oidc: int
    tokens: dict[str, str] = field(default_factory=dict)

    def auth(self, who: str) -> dict[str, str]:
        """Bearer header for the named portal caller."""
        return {"Authorization": f"Bearer {self.tokens[who]}"}


def allow_redirect_uris(monkeypatch: Any, app: Any, *uris: str) -> None:
    """Set the operator's ``OIDC_ALLOWED_REDIRECT_URIS`` for one test.

    Defaults to the testkit's callback URL; pass others to widen the list.
    """
    monkeypatch.setitem(
        app.config,
        "OIDC_ALLOWED_REDIRECT_URIS",
        ",".join(uris or (CALLBACK_REDIRECT_URI,)),
    )


def mint_token(
    app: Any,
    *,
    user_id: int,
    tenant_id: int | None,
    tenant_role: str | None,
    global_role: str | None = None,
) -> str:
    """Mint a portal-user JWT exactly like portal-auth does (HS256)."""
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "email": f"user{user_id}@example.test",
        "tenant_id": tenant_id,
        "tenant_role": tenant_role,
        "global_role": global_role,
        "type": "portal_user",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def insert_idp(db: Any, tenant_id: int | None, name: str, **extra: Any) -> int:
    """Insert an active OIDC IdP config row directly (fixture data)."""
    config_id = db.idp_configurations.insert(
        tenant_id=tenant_id,
        idp_type="oidc",
        name=name,
        attribute_mappings={},
        jit_provisioning_enabled=extra.pop("jit_provisioning_enabled", True),
        default_role="reader",
        is_active=True,
        **extra,
    )
    db.commit()
    return config_id


def insert_user(db: Any, tenant_id: int, email: str, **extra: Any) -> int:
    """Insert a portal user row directly (fixture data)."""
    user_id = db.portal_users.insert(
        tenant_id=tenant_id,
        email=email,
        tenant_role=extra.pop("tenant_role", "reader"),
        is_active=extra.pop("is_active", True),
        email_verified=True,
        **extra,
    )
    db.commit()
    return user_id


def build_world(app: Any, *, issuer_a: str, issuer_b: str, issuer_g: str) -> World:
    """Create the two-tenant fixture world inside the current app context."""
    prefix = f"sso-hard-{secrets.token_hex(4)}"
    db = current_app.db

    def tenant(label: str) -> int:
        tid = db.tenants.insert(
            name=f"{prefix}-{label}",
            slug=f"{prefix}-{label}",
            is_active=True,
            settings={},
        )
        db.commit()
        return tid

    tenant_a, tenant_b, tenant_sys = tenant("a"), tenant("b"), tenant("sys")

    a_user_email = f"a-user@{prefix}.test"
    a_global_admin_email = f"global-admin@{prefix}.test"
    b_admin_email = f"b-admin@{prefix}.test"
    b_member_email = f"b-member@{prefix}.test"
    a_user_id = insert_user(db, tenant_a, a_user_email)
    # The platform admin's own row lives in the attacker's tenant (A): this is
    # the account a tenant-A admin's private IdP must never be able to assert.
    a_global_admin_id = insert_user(
        db,
        tenant_a,
        a_global_admin_email,
        tenant_role="admin",
        global_role="admin",
    )
    b_admin_id = insert_user(db, tenant_b, b_admin_email, tenant_role="admin")
    b_member_id = insert_user(db, tenant_b, b_member_email)

    scim_token_a = f"scim_a_{secrets.token_urlsafe(24)}"
    scim_token_b = f"scim_b_{secrets.token_urlsafe(24)}"
    for tid, token in ((tenant_a, scim_token_a), (tenant_b, scim_token_b)):
        db.scim_configurations.insert(
            tenant_id=tid,
            endpoint_url=f"/api/v1/sso/scim/{tid}",
            bearer_token=token,
            sync_groups=True,
            is_active=True,
        )
    db.commit()

    a_oidc = insert_idp(
        db,
        tenant_a,
        f"{prefix}-a-oidc",
        oidc_client_id="client-a",
        oidc_client_secret="SECRET-A-oidc",
        oidc_issuer_url=issuer_a,
    )
    b_oidc = insert_idp(
        db,
        tenant_b,
        f"{prefix}-b-oidc",
        oidc_client_id="client-b",
        oidc_client_secret="SECRET-B-oidc",
        oidc_issuer_url=issuer_b,
    )
    g_oidc = insert_idp(
        db,
        None,
        f"{prefix}-g-oidc",
        oidc_client_id="client-g",
        oidc_client_secret="SECRET-G-oidc",
        oidc_issuer_url=issuer_g,
    )

    world = World(
        prefix=prefix,
        tenant_a=tenant_a,
        tenant_b=tenant_b,
        tenant_sys=tenant_sys,
        a_user_email=a_user_email,
        a_global_admin_email=a_global_admin_email,
        b_admin_email=b_admin_email,
        b_member_email=b_member_email,
        a_user_id=a_user_id,
        a_global_admin_id=a_global_admin_id,
        b_admin_id=b_admin_id,
        b_member_id=b_member_id,
        scim_token_a=scim_token_a,
        scim_token_b=scim_token_b,
        a_oidc=a_oidc,
        b_oidc=b_oidc,
        g_oidc=g_oidc,
    )
    world.tokens = {
        "a_admin": mint_token(
            app, user_id=101, tenant_id=tenant_a, tenant_role="admin"
        ),
        "b_admin": mint_token(
            app, user_id=102, tenant_id=tenant_b, tenant_role="admin"
        ),
        "a_reader": mint_token(
            app, user_id=103, tenant_id=tenant_a, tenant_role="reader"
        ),
        "global_admin": mint_token(
            app,
            user_id=104,
            tenant_id=tenant_sys,
            tenant_role="admin",
            global_role="admin",
        ),
        "support_tenant_admin": mint_token(
            app,
            user_id=105,
            tenant_id=tenant_a,
            tenant_role="admin",
            global_role="support",
        ),
        "no_tenant_admin": mint_token(
            app, user_id=106, tenant_id=None, tenant_role="admin"
        ),
    }
    return world


def teardown_world(world: World) -> None:
    """Delete every row the world (and the tests on top of it) created."""
    db = current_app.db
    tenant_ids = (world.tenant_a, world.tenant_b, world.tenant_sys)
    db(db.idp_configurations.name.startswith(world.prefix)).delete()
    for tid in tenant_ids:
        db(db.idp_configurations.tenant_id == tid).delete()
        db(db.scim_configurations.tenant_id == tid).delete()
        db(db.portal_users.tenant_id == tid).delete()
        db(db.tenants.id == tid).delete()
    db.commit()


def set_tenant_settings(tenant_id: int, settings: dict[str, Any]) -> None:
    """Replace a tenant's ``settings`` JSON (call inside an app context)."""
    db = current_app.db
    db(db.tenants.id == tenant_id).update(settings=settings)
    db.commit()


def b64url(data: bytes) -> str:
    """URL-safe base64 without padding (PKCE/JWS encoding)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@dataclass(slots=True)
class FakeOidcProvider:
    """Stand-in for an *external* OIDC provider.

    Enforces what the Elder side must do correctly: the right client
    credentials at the token endpoint, the PKCE verifier matching the
    ``code_challenge`` it saw at authorize time, and the ``nonce`` it was given
    echoed into the ID token (unless ``nonce_override`` forces a bad one).
    """

    issuer: str
    client_id: str
    client_secret: str
    kid: str
    key: Any = field(default_factory=lambda: rsa.generate_private_key(65537, 2048))
    grants: dict[str, dict[str, Any]] = field(default_factory=dict)
    refresh_tokens: set[str] = field(default_factory=set)
    token_calls: list[str] = field(default_factory=list)
    userinfo_calls: list[str] = field(default_factory=list)
    discovery_calls: int = 0
    nonce_override: str | None = None
    omit_nonce: bool = False
    userinfo_claims: dict[str, Any] | None = None
    omit_endpoints: set[str] = field(default_factory=set)
    claim_overrides: dict[str, Any] = field(default_factory=dict)
    signing_key: Any = None
    jwks_kid: str | None = None
    omit_id_token: bool = False

    @property
    def authorization_endpoint(self) -> str:
        """Authorization endpoint URL advertised in discovery."""
        return f"{self.issuer}/authorize"

    @property
    def token_endpoint(self) -> str:
        """Token endpoint URL advertised in discovery."""
        return f"{self.issuer}/token"

    @property
    def jwks_uri(self) -> str:
        """JWKS URL advertised in discovery."""
        return f"{self.issuer}/jwks"

    @property
    def userinfo_endpoint(self) -> str:
        """Userinfo endpoint URL advertised in discovery."""
        return f"{self.issuer}/userinfo"

    def discovery(self) -> dict[str, str]:
        """OIDC discovery document."""
        self.discovery_calls += 1
        document = {
            "issuer": self.issuer,
            "authorization_endpoint": self.authorization_endpoint,
            "token_endpoint": self.token_endpoint,
            "jwks_uri": self.jwks_uri,
            "userinfo_endpoint": self.userinfo_endpoint,
            "end_session_endpoint": f"{self.issuer}/logout",
        }
        return {k: v for k, v in document.items() if k not in self.omit_endpoints}

    def jwks(self) -> dict[str, Any]:
        """JWKS with this provider's single signing key."""
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update({"kid": self.jwks_kid or self.kid, "alg": "RS256", "use": "sig"})
        return {"keys": [jwk]}

    def authenticate(self, authorization_url: str, email: str) -> str:
        """Simulate the user logging in at the IdP; return the issued code.

        Reads ``nonce``/``code_challenge`` off the URL Elder built, exactly like
        a real provider binds them to the authorization code.
        """
        query = parse_qs(urlparse(authorization_url).query)
        code = f"code-{secrets.token_hex(8)}"
        self.grants[code] = {
            "email": email,
            "nonce": query.get("nonce", [None])[0],
            "code_challenge": query.get("code_challenge", [None])[0],
            "code_challenge_method": query.get("code_challenge_method", [None])[0],
        }
        return code

    def issue_id_token(self, code: str, code_verifier: str | None) -> str:
        """Exchange ``code`` for an ID token (raises like a real IdP on misuse)."""
        grant = self.grants.pop(code, None)
        if grant is None:
            raise ValueError("invalid_grant")
        if grant["code_challenge"]:
            if grant["code_challenge_method"] != "S256" or not code_verifier:
                raise ValueError("invalid_grant: pkce")
            digest = b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())
            if digest != grant["code_challenge"]:
                raise ValueError("invalid_grant: pkce mismatch")
        now = datetime.now(UTC)
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.client_id,
            "sub": f"sub-{grant['email']}",
            "email": grant["email"],
            "email_verified": True,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        }
        if not self.omit_nonce:
            nonce = self.nonce_override or grant["nonce"]
            if nonce:
                claims["nonce"] = nonce
        for name, value in self.claim_overrides.items():
            if value is None:
                claims.pop(name, None)
            else:
                claims[name] = value
        return jwt.encode(
            claims,
            self.signing_key or self.key,
            algorithm="RS256",
            headers={"kid": self.kid},
        )


class _Resp:
    """Minimal ``requests.Response`` stand-in."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        """Always 200."""

    def json(self) -> dict[str, Any]:
        """Return the canned JSON document."""
        return self._payload


def install_fake_providers(monkeypatch: Any, providers: list[FakeOidcProvider]) -> None:
    """Route OIDCService's outbound HTTP/OAuth calls to the fake providers."""
    documents: dict[str, Any] = {}
    by_token_endpoint: dict[str, FakeOidcProvider] = {}
    by_userinfo: dict[str, FakeOidcProvider] = {}
    for p in providers:
        documents[f"{p.issuer}/.well-known/openid-configuration"] = p.discovery
        documents[p.jwks_uri] = p.jwks
        by_token_endpoint[p.token_endpoint] = p
        by_userinfo[p.userinfo_endpoint] = p

    def fake_get(url: str, **kwargs: Any) -> _Resp:
        if url in by_userinfo:
            provider = by_userinfo[url]
            provider.userinfo_calls.append(
                kwargs.get("headers", {}).get("Authorization", "")
            )
            return _Resp(provider.userinfo_claims or {})
        if url not in documents:
            raise requests.ConnectionError(f"unreachable: {url}")
        return _Resp(documents[url]())

    class FakeSession:
        def __init__(
            self,
            client_id: str,
            client_secret: str,
            token_endpoint_auth_method: str | None = None,
        ) -> None:
            self.client_id = client_id
            self.client_secret = client_secret

        def _provider(self, url: str) -> FakeOidcProvider:
            provider = by_token_endpoint[url]
            provider.token_calls.append(f"{self.client_id}:{url}")
            if (self.client_id, self.client_secret) != (
                provider.client_id,
                provider.client_secret,
            ):
                raise ValueError("invalid_client")
            return provider

        def fetch_token(
            self,
            url: str,
            grant_type: str,
            code: str,
            redirect_uri: str,
            **kwargs: Any,
        ) -> dict[str, Any]:
            provider = self._provider(url)
            token = {
                "id_token": provider.issue_id_token(code, kwargs.get("code_verifier")),
                "access_token": "idp-access-token",
                "token_type": "Bearer",
            }
            if provider.omit_id_token:
                del token["id_token"]
            return token

        def refresh_token(self, url: str, refresh_token: str) -> dict[str, Any]:
            provider = self._provider(url)
            if refresh_token not in provider.refresh_tokens:
                raise ValueError("invalid_grant")
            return {"access_token": "refreshed-access", "token_type": "Bearer"}

    monkeypatch.setattr("apps.api.services.sso.oidc_service.requests.get", fake_get)
    monkeypatch.setattr("apps.api.services.sso.oidc_service.OAuth2Session", FakeSession)
