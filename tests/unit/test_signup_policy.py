"""Unit tests for configurable signup email-domain allowlist."""

from __future__ import annotations

import pytest

from apps.api.utils.signup_policy import (
    extract_email_domain,
    get_allowed_signup_domains,
    is_signup_email_allowed,
)


class TestGetAllowedSignupDomains:
    """Tests for parsing ALLOWED_SIGNUP_DOMAINS."""

    def test_empty_env_returns_empty_tuple(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALLOWED_SIGNUP_DOMAINS", raising=False)
        assert get_allowed_signup_domains() == ()

    def test_whitespace_only_returns_empty_tuple(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALLOWED_SIGNUP_DOMAINS", "   , ;  ")
        assert get_allowed_signup_domains() == ()

    def test_normalizes_case_leading_at_and_deduplicates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "ALLOWED_SIGNUP_DOMAINS",
            "@PenguinTech.IO, example.com; penguintech.io  sub.example.org",
        )
        assert get_allowed_signup_domains() == (
            "penguintech.io",
            "example.com",
            "sub.example.org",
        )


class TestExtractEmailDomain:
    """Tests for email domain extraction."""

    @pytest.mark.parametrize(
        ("email", "expected"),
        [
            ("alice@penguintech.io", "penguintech.io"),
            ("  ALICE@PenguinTech.IO  ", "penguintech.io"),
            ("not-an-email", ""),
            ("@penguintech.io", ""),
            ("alice@", ""),
            ("", ""),
        ],
    )
    def test_extract_email_domain(self, email: str, expected: str) -> None:
        assert extract_email_domain(email) == expected


class TestIsSignupEmailAllowed:
    """Tests for signup email allowlist enforcement."""

    def test_unconfigured_allows_any_domain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALLOWED_SIGNUP_DOMAINS", raising=False)
        assert is_signup_email_allowed("someone@anywhere.example") is True

    def test_configured_domain_allows_exact_match_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALLOWED_SIGNUP_DOMAINS", "penguintech.io")
        assert is_signup_email_allowed("alice@penguintech.io") is True
        assert is_signup_email_allowed("Bob@PenguinTech.IO") is True

    def test_configured_domain_rejects_other_domains_and_subdomains(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALLOWED_SIGNUP_DOMAINS", "penguintech.io")
        assert is_signup_email_allowed("attacker@evil.com") is False
        assert is_signup_email_allowed("attacker@evil.penguintech.io") is False
        assert is_signup_email_allowed("attacker@notpenguintech.io") is False
        assert is_signup_email_allowed("invalid-email") is False

    def test_multiple_domains_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALLOWED_SIGNUP_DOMAINS", "penguintech.io,partner.example")
        assert is_signup_email_allowed("a@penguintech.io") is True
        assert is_signup_email_allowed("b@partner.example") is True
        assert is_signup_email_allowed("c@other.example") is False
