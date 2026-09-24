"""JWT hardening tests: iss/aud claims + no secret/PII in logs.

Regression coverage for security review finding #4:
- generate_token() must stamp iss/aud so a forged/wrong-service token can
  be detected.
- verify_token() must reject a token whose iss/aud is PRESENT but doesn't
  match this service's configured values (issuer/audience mismatch).
- verify_token()/get_token_from_header() must never log the signing
  secret, token contents, or PII (email), at any level.
"""

from dataclasses import dataclass

import jwt
import pytest
from quart import current_app

from apps.api.auth.jwt_handler import generate_token, verify_token


@dataclass(slots=True)
class _FakeIdentity:
    id: int = 1
    username: str = "alice"
    portal_role: str = "observer"
    tenant_id: int = 1


class TestGenerateTokenStampsIssuerAudience:
    @pytest.mark.asyncio
    async def test_access_token_has_iss_and_aud(self, app):
        async with app.app_context():
            token = generate_token(_FakeIdentity(), "access")
            payload = jwt.decode(
                token,
                current_app.config["JWT_SECRET_KEY"]
                or current_app.config["SECRET_KEY"],
                algorithms=[current_app.config["JWT_ALGORITHM"]],
                options={"verify_aud": False},
            )
            assert payload["iss"] == current_app.config.get("JWT_ISSUER", "elder-api")
            assert payload["aud"] == current_app.config.get("JWT_AUDIENCE", "elder-api")


class TestVerifyTokenIssuerAudienceValidation:
    @pytest.mark.asyncio
    async def test_valid_token_round_trips(self, app):
        async with app.app_context():
            token = generate_token(_FakeIdentity(), "access")
            payload = verify_token(token)
            assert payload is not None
            assert payload["sub"] == "1"

    @pytest.mark.asyncio
    async def test_rejects_wrong_issuer(self, app):
        async with app.app_context():
            token = generate_token(_FakeIdentity(), "access")
            secret = (
                current_app.config["JWT_SECRET_KEY"] or current_app.config["SECRET_KEY"]
            )
            algorithm = current_app.config["JWT_ALGORITHM"]
            tampered_payload = jwt.decode(
                token, secret, algorithms=[algorithm], options={"verify_aud": False}
            )
            tampered_payload["iss"] = "some-other-service"
            forged = jwt.encode(tampered_payload, secret, algorithm=algorithm)
            assert verify_token(forged) is None

    @pytest.mark.asyncio
    async def test_rejects_wrong_audience(self, app):
        async with app.app_context():
            token = generate_token(_FakeIdentity(), "access")
            secret = (
                current_app.config["JWT_SECRET_KEY"] or current_app.config["SECRET_KEY"]
            )
            algorithm = current_app.config["JWT_ALGORITHM"]
            tampered_payload = jwt.decode(
                token, secret, algorithms=[algorithm], options={"verify_aud": False}
            )
            tampered_payload["aud"] = "some-other-audience"
            forged = jwt.encode(tampered_payload, secret, algorithm=algorithm)
            assert verify_token(forged) is None

    @pytest.mark.asyncio
    async def test_allows_legacy_token_missing_iss_aud(self, app):
        """Other in-flight token issuers (portal_auth, gRPC servicer, and
        several stream-module test fixtures owned elsewhere) don't stamp
        iss/aud yet -- verify_token() must not hard-reject their tokens
        outright (that would break portal login today). Only a PRESENT,
        WRONG value is rejected -- see verify_token()'s docstring."""
        async with app.app_context():
            secret = (
                current_app.config["JWT_SECRET_KEY"] or current_app.config["SECRET_KEY"]
            )
            algorithm = current_app.config["JWT_ALGORITHM"]
            legacy_payload = {"sub": "1", "type": "access", "tenant": "1"}
            legacy_token = jwt.encode(legacy_payload, secret, algorithm=algorithm)
            assert verify_token(legacy_token) is not None


class TestNoSecretOrPiiInLogs:
    @pytest.mark.asyncio
    async def test_verify_token_never_logs_secret_or_payload(self, app, caplog):
        async with app.app_context():
            token = generate_token(_FakeIdentity(username="alice"), "access")
            secret = (
                current_app.config["JWT_SECRET_KEY"] or current_app.config["SECRET_KEY"]
            )
            with caplog.at_level("DEBUG"):
                verify_token(token)
            log_text = caplog.text
            assert secret not in log_text
            assert token not in log_text
            assert "alice" not in log_text

    @pytest.mark.asyncio
    async def test_invalid_auth_header_never_logs_header_value(self, app, caplog):
        from apps.api.auth.jwt_handler import get_token_from_header

        async with app.test_request_context(
            "/", headers={"Authorization": "NotBearer some-secret-value-xyz"}
        ):
            with caplog.at_level("DEBUG"):
                result = get_token_from_header()
            assert result is None
            assert "some-secret-value-xyz" not in caplog.text
