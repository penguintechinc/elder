"""Unit tests for ALTCHA challenge generation and server-side verification."""

from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from quart import Quart

from apps.api.utils import altcha


def _solve_challenge(challenge: dict) -> str:
    """Solve an ALTCHA challenge and return the base64-encoded JSON payload."""
    salt = challenge["salt"]
    target = challenge["challenge"]
    maxnumber = challenge["maxnumber"]
    for number in range(maxnumber + 1):
        digest = hashlib.sha256(f"{salt}{number}".encode("utf-8")).hexdigest()
        if digest == target:
            payload = {
                "algorithm": challenge["algorithm"],
                "challenge": target,
                "number": number,
                "salt": salt,
                "signature": challenge["signature"],
            }
            return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    raise AssertionError("Failed to solve challenge within maxnumber")


@pytest.fixture(autouse=True)
def _clean_replay_cache():
    altcha.reset_used_challenges()
    yield
    altcha.reset_used_challenges()


def test_create_challenge_defaults_and_key_derivation(monkeypatch):
    monkeypatch.delenv("CAPTCHA_SECRET", raising=False)
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-altcha")
    monkeypatch.delenv("ALTCHA_MAX_NUMBER", raising=False)

    c = altcha.create_altcha_challenge(now=1_700_000_000)
    assert c["algorithm"] == "SHA-256"
    assert c["maxnumber"] == 100_000
    assert "?expires=1700000300" in c["salt"]
    assert len(c["challenge"]) == 64
    assert len(c["signature"]) == 64


def test_get_altcha_hmac_key_fails_loud_without_secret(monkeypatch):
    monkeypatch.delenv("CAPTCHA_SECRET", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="CAPTCHA_SECRET or SECRET_KEY"):
        altcha.get_altcha_hmac_key()


def test_verify_valid_and_reject_replay(monkeypatch):
    monkeypatch.setenv("CAPTCHA_SECRET", "dedicated-captcha-secret")
    now = 1_700_000_000
    challenge = altcha.create_altcha_challenge(max_number=500, ttl_seconds=300, now=now)
    token = _solve_challenge(challenge)

    ok, err = altcha.verify_altcha_payload(token, now=now + 10)
    assert ok is True
    assert err is None

    # Replay must fail on second verification
    ok2, err2 = altcha.verify_altcha_payload(token, now=now + 11)
    assert ok2 is False
    assert err2 == "CAPTCHA token has already been used"


def test_verify_rejects_expired_tampered_and_malformed(monkeypatch):
    monkeypatch.setenv("CAPTCHA_SECRET", "dedicated-captcha-secret")
    now = 1_700_000_000
    challenge = altcha.create_altcha_challenge(max_number=500, ttl_seconds=60, now=now)
    valid_token = _solve_challenge(challenge)
    decoded = json.loads(base64.b64decode(valid_token))

    # Expired
    ok, err = altcha.verify_altcha_payload(valid_token, now=now + 61)
    assert ok is False and "expired" in (err or "").lower()

    # Wrong proof-of-work number
    bad_num = dict(decoded, number=decoded["number"] + 1)
    bad_num_tok = base64.b64encode(json.dumps(bad_num).encode()).decode()
    ok, err = altcha.verify_altcha_payload(bad_num_tok, now=now + 5)
    assert ok is False and "proof-of-work" in (err or "").lower()

    # Tampered signature
    bad_sig = dict(decoded, signature="0" * 64)
    bad_sig_tok = base64.b64encode(json.dumps(bad_sig).encode()).decode()
    ok, err = altcha.verify_altcha_payload(bad_sig_tok, now=now + 5)
    assert ok is False and "signature" in (err or "").lower()

    # Missing expires in salt
    no_exp_salt = "abcdef123456"
    no_exp_ch = hashlib.sha256(f"{no_exp_salt}0".encode()).hexdigest()
    no_exp = dict(decoded, salt=no_exp_salt, number=0, challenge=no_exp_ch)
    no_exp_tok = base64.b64encode(json.dumps(no_exp).encode()).decode()
    ok, err = altcha.verify_altcha_payload(no_exp_tok, now=now + 5)
    assert ok is False and "expiry" in (err or "").lower()

    # Boolean number rejected
    bool_num = dict(decoded, number=True)
    bool_tok = base64.b64encode(json.dumps(bool_num).encode()).decode()
    ok, err = altcha.verify_altcha_payload(bool_tok, now=now + 5)
    assert ok is False

    # Malformed base64 / oversized
    assert altcha.verify_altcha_payload("not-valid-json")[0] is False
    assert altcha.verify_altcha_payload("A" * 5000)[0] is False
    assert altcha.verify_altcha_payload("")[0] is False


@pytest.mark.asyncio
async def test_portal_login_enforces_server_side_altcha(monkeypatch):
    from apps.api.api.v1 import auth as auth_routes
    from apps.api.api.v1 import portal_auth as portal_auth_routes

    monkeypatch.setenv("CAPTCHA_SECRET", "login-test-captcha-secret")
    app = Quart(__name__)
    app.config["SECRET_KEY"] = "login-test-secret"
    app.config["DEBUG"] = True
    app.register_blueprint(auth_routes.bp, url_prefix="/api/v1/auth")
    app.register_blueprint(portal_auth_routes.bp, url_prefix="/api/v1/portal-auth")

    # Minimal mock DB that resolves tenant_id=1
    tenant_row = SimpleNamespace(id=1, slug="system")
    mock_select = MagicMock()
    mock_select.first.return_value = tenant_row
    mock_query = MagicMock()
    mock_query.select.return_value = mock_select
    mock_db = MagicMock(return_value=mock_query)
    mock_db.tenants = SimpleNamespace(slug="slug")
    app.db = mock_db

    client = app.test_client()

    # 1. Challenge endpoint returns signed challenge with expiry
    res = await client.get("/api/v1/auth/captcha-challenge")
    assert res.status_code == 200
    body = await res.get_json()
    assert body["maxnumber"] == 100_000
    assert "?expires=" in body["salt"]

    # 2. Tampered captchaToken rejected even before threshold
    with (
        patch("apps.api.utils.altcha.requires_login_captcha", return_value=False),
        patch.object(portal_auth_routes.PortalAuthService, "authenticate") as mock_auth,
    ):
        res = await client.post(
            "/api/v1/portal-auth/login",
            json={
                "email": "admin@example.com",
                "password": "Password123!",
                "captchaToken": "bogus-token",
            },
        )
        assert res.status_code == 400
        data = await res.get_json()
        assert data["errorCode"] == "CAPTCHA_INVALID"
        mock_auth.assert_not_called()

    # 3. Once threshold reached (>= 3 failed attempts), missing captchaToken is rejected
    with (
        patch("apps.api.utils.altcha.requires_login_captcha", return_value=True),
        patch.object(portal_auth_routes.PortalAuthService, "authenticate") as mock_auth,
    ):
        res = await client.post(
            "/api/v1/portal-auth/login",
            json={"email": "admin@example.com", "password": "Password123!"},
        )
        assert res.status_code == 400
        data = await res.get_json()
        assert data["errorCode"] == "CAPTCHA_REQUIRED"
        assert data["captchaRequired"] is True
        mock_auth.assert_not_called()

    # 4. Valid solved captchaToken passes through to authenticate() and cannot be replayed
    small_challenge = altcha.create_altcha_challenge(max_number=500)
    solved_token = _solve_challenge(small_challenge)
    with (
        patch("apps.api.utils.altcha.requires_login_captcha", return_value=True),
        patch.object(
            portal_auth_routes.PortalAuthService,
            "authenticate",
            return_value={"error": "Invalid credentials"},
        ) as mock_auth,
    ):
        res = await client.post(
            "/api/v1/portal-auth/login",
            json={
                "email": "admin@example.com",
                "password": "WrongPassword!",
                "captchaToken": solved_token,
            },
        )
        assert res.status_code == 401
        mock_auth.assert_called_once()

        # Replaying the exact same token must be rejected with 400 CAPTCHA_INVALID
        res_replay = await client.post(
            "/api/v1/portal-auth/login",
            json={
                "email": "admin@example.com",
                "password": "WrongPassword!",
                "captchaToken": solved_token,
            },
        )
        assert res_replay.status_code == 400
        assert (await res_replay.get_json())["errorCode"] == "CAPTCHA_INVALID"

    # 5. Step-2 MFA submission (mfaCode present, no captchaToken) is NOT blocked by CAPTCHA
    with (
        patch("apps.api.utils.altcha.requires_login_captcha", return_value=True),
        patch.object(
            portal_auth_routes.PortalAuthService,
            "authenticate",
            return_value={"mfa_required": True, "user_id": 1, "tenant_id": 1},
        ),
        patch.object(
            portal_auth_routes.PortalAuthService,
            "verify_mfa",
            return_value={"error": "Invalid MFA code", "status_code": 401},
        ) as mock_verify,
    ):
        res = await client.post(
            "/api/v1/portal-auth/login",
            json={
                "email": "admin@example.com",
                "password": "Password123!",
                "mfaCode": "123456",
            },
        )
        assert res.status_code == 401
        mock_verify.assert_called_once_with(1, "123456")
