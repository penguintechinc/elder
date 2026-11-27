"""Unit tests for one-time onboarding tokens (email_verify and admin_invite)."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from apps.api.utils import onboarding_tokens
from apps.api.utils.onboarding_tokens import (
    ADMIN_INVITE_TTL_SECONDS,
    EMAIL_VERIFY_TTL_SECONDS,
    PURPOSE_ADMIN_INVITE,
    PURPOSE_EMAIL_VERIFY,
    consume_onboarding_token,
    issue_onboarding_token,
    peek_onboarding_token,
    reset_onboarding_tokens,
)


class _FakeRedis:
    """Minimal in-memory Redis double supporting set/get/getdel."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int | None]] = {}

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = (value, ex)
        return True

    def get(self, key: str) -> str | None:
        entry = self.store.get(key)
        return entry[0] if entry is not None else None

    def getdel(self, key: str) -> str | None:
        entry = self.store.pop(key, None)
        return entry[0] if entry is not None else None


@pytest.fixture(autouse=True)
def _clean_store() -> None:
    reset_onboarding_tokens()
    yield
    reset_onboarding_tokens()


class TestOnboardingTokenLifecycle:
    """Tests for issuing, peeking, and consuming single-use onboarding tokens."""

    def test_issue_peek_and_single_use_consume_email_verify(self) -> None:
        token = issue_onboarding_token(
            PURPOSE_EMAIL_VERIFY,
            {"user_id": 42, "tenant_id": 1},
            now=1_000,
        )
        assert isinstance(token, str) and len(token) >= 32

        # Multiple peeks succeed without consuming the token.
        assert peek_onboarding_token(PURPOSE_EMAIL_VERIFY, token, now=1_100) == {
            "user_id": 42,
            "tenant_id": 1,
        }
        assert peek_onboarding_token(PURPOSE_EMAIL_VERIFY, token, now=1_200) == {
            "user_id": 42,
            "tenant_id": 1,
        }

        # First consume succeeds; second consume and subsequent peek fail.
        consumed = consume_onboarding_token(PURPOSE_EMAIL_VERIFY, token, now=1_300)
        assert consumed == {"user_id": 42, "tenant_id": 1}
        assert consume_onboarding_token(PURPOSE_EMAIL_VERIFY, token, now=1_301) is None
        assert peek_onboarding_token(PURPOSE_EMAIL_VERIFY, token, now=1_301) is None

    def test_strict_purpose_domain_separation(self) -> None:
        verify_token = issue_onboarding_token(
            PURPOSE_EMAIL_VERIFY, {"user_id": 7, "tenant_id": 1}, now=1_000
        )
        invite_token = issue_onboarding_token(
            PURPOSE_ADMIN_INVITE, {"user_id": 8, "tenant_id": 1}, now=1_000
        )

        # Cross-purpose peek and consume must fail without destroying the token.
        assert (
            peek_onboarding_token(PURPOSE_ADMIN_INVITE, verify_token, now=1_001) is None
        )
        assert (
            consume_onboarding_token(PURPOSE_ADMIN_INVITE, verify_token, now=1_001)
            is None
        )
        assert (
            peek_onboarding_token(PURPOSE_EMAIL_VERIFY, invite_token, now=1_001) is None
        )
        assert (
            consume_onboarding_token(PURPOSE_EMAIL_VERIFY, invite_token, now=1_001)
            is None
        )

        # Original purposes still work.
        assert consume_onboarding_token(
            PURPOSE_EMAIL_VERIFY, verify_token, now=1_002
        ) == {
            "user_id": 7,
            "tenant_id": 1,
        }
        assert consume_onboarding_token(
            PURPOSE_ADMIN_INVITE, invite_token, now=1_002
        ) == {
            "user_id": 8,
            "tenant_id": 1,
        }

    def test_expiry_rejects_peek_and_consume(self) -> None:
        token = issue_onboarding_token(
            PURPOSE_EMAIL_VERIFY,
            {"user_id": 99, "tenant_id": 2},
            ttl_seconds=60,
            now=5_000,
        )
        assert peek_onboarding_token(PURPOSE_EMAIL_VERIFY, token, now=5_059) is not None
        assert peek_onboarding_token(PURPOSE_EMAIL_VERIFY, token, now=5_060) is None
        assert consume_onboarding_token(PURPOSE_EMAIL_VERIFY, token, now=5_060) is None

    def test_default_ttls_and_validation(self) -> None:
        assert EMAIL_VERIFY_TTL_SECONDS == 24 * 3600
        assert ADMIN_INVITE_TTL_SECONDS == 72 * 3600

        with pytest.raises(ValueError, match="Unsupported onboarding token purpose"):
            issue_onboarding_token("password_reset", {"user_id": 1})

        with pytest.raises(ValueError, match="Unsupported onboarding token purpose"):
            peek_onboarding_token("unknown", "abc")

        with pytest.raises(ValueError, match="Unsupported onboarding token purpose"):
            consume_onboarding_token("unknown", "abc")

        with pytest.raises(ValueError, match="ttl_seconds must be positive"):
            issue_onboarding_token(PURPOSE_EMAIL_VERIFY, {"user_id": 1}, ttl_seconds=0)

        assert peek_onboarding_token(PURPOSE_EMAIL_VERIFY, "") is None
        assert consume_onboarding_token(PURPOSE_ADMIN_INVITE, "   ") is None

    def test_stores_only_sha256_digest_in_redis_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_redis = _FakeRedis()
        monkeypatch.setattr(onboarding_tokens, "_get_redis_client", lambda: fake_redis)

        token = issue_onboarding_token(
            PURPOSE_ADMIN_INVITE,
            {"user_id": 12, "tenant_id": 3},
        )
        expected_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expected_key = f"elder:onboarding:admin_invite:{expected_digest}"

        assert list(fake_redis.store.keys()) == [expected_key]
        assert token not in expected_key
        assert fake_redis.store[expected_key][1] == ADMIN_INVITE_TTL_SECONDS

        # Peek reads without removing
        assert peek_onboarding_token(PURPOSE_ADMIN_INVITE, token) == {
            "user_id": 12,
            "tenant_id": 3,
        }
        assert expected_key in fake_redis.store

        # Consume atomically removes
        payload: dict[str, Any] | None = consume_onboarding_token(
            PURPOSE_ADMIN_INVITE, token
        )
        assert payload == {"user_id": 12, "tenant_id": 3}
        assert expected_key not in fake_redis.store
        assert consume_onboarding_token(PURPOSE_ADMIN_INVITE, token) is None
