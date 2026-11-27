"""Unit tests for per-tenant and environment-default signup policy."""

from __future__ import annotations

import pytest

from apps.api.utils.signup_policy import (
    extract_email_domain,
    get_allowed_signup_domains,
    is_email_verification_required,
    is_signup_email_allowed,
    normalize_signup_domains,
    resolve_allowed_signup_domains,
    resolve_tenant_signup_policy,
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

    def test_normalize_accepts_list_and_string(self) -> None:
        assert normalize_signup_domains(
            ["@Example.COM", "penguintech.io", "example.com"]
        ) == (
            "example.com",
            "penguintech.io",
        )
        assert normalize_signup_domains([]) == ()
        assert normalize_signup_domains(None) == ()


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


class TestPerTenantSignupDomainPrecedence:
    """Tests for per-tenant domain policy precedence."""

    def test_tenant_settings_overrides_env_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALLOWED_SIGNUP_DOMAINS", "penguintech.io")
        domains, source = resolve_allowed_signup_domains(
            {"allowed_signup_domains": ["@Acme.COM", "partner.io"]},
            tenant_slug="system",
        )
        assert domains == ("acme.com", "partner.io")
        assert source == "tenant"
        assert (
            is_signup_email_allowed(
                "alice@acme.com",
                tenant_settings={"allowed_signup_domains": ["acme.com"]},
                tenant_slug="system",
            )
            is True
        )
        assert (
            is_signup_email_allowed(
                "alice@penguintech.io",
                tenant_settings={"allowed_signup_domains": ["acme.com"]},
                tenant_slug="system",
            )
            is False
        )

    def test_explicit_empty_list_in_tenant_overrides_env_to_allow_all(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALLOWED_SIGNUP_DOMAINS", "penguintech.io")
        domains, source = resolve_allowed_signup_domains(
            {"allowed_signup_domains": []},
            tenant_slug="system",
        )
        assert domains == ()
        assert source == "tenant"
        assert (
            is_signup_email_allowed(
                "anyone@external.example",
                tenant_settings={"allowed_signup_domains": []},
                tenant_slug="system",
            )
            is True
        )

    @pytest.mark.parametrize("slug", ["system", "default", "SYSTEM"])
    def test_system_and_default_tenants_inherit_env_default(
        self, monkeypatch: pytest.MonkeyPatch, slug: str
    ) -> None:
        monkeypatch.setenv("ALLOWED_SIGNUP_DOMAINS", "penguintech.io")
        domains, source = resolve_allowed_signup_domains({}, tenant_slug=slug)
        assert domains == ("penguintech.io",)
        assert source == "env_default"

    def test_non_default_tenant_without_setting_is_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALLOWED_SIGNUP_DOMAINS", "penguintech.io")
        domains, source = resolve_allowed_signup_domains({}, tenant_slug="acme-corp")
        assert domains == ()
        assert source == "open"
        assert (
            is_signup_email_allowed(
                "alice@acme-corp.com",
                tenant_settings={},
                tenant_slug="acme-corp",
            )
            is True
        )


class TestEmailVerificationPolicy:
    """Tests for require_email_verification precedence."""

    def test_tenant_setting_wins_over_env_and_smtp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
        monkeypatch.setenv("SMTP_HOST", "smtp-relay.gmail.com")
        monkeypatch.setenv("SMTP_FROM_EMAIL", "no-reply@penguintech.io")
        assert (
            is_email_verification_required({"require_email_verification": False})
            is False
        )

        monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "false")
        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.delenv("SMTP_FROM_EMAIL", raising=False)
        assert (
            is_email_verification_required({"require_email_verification": True}) is True
        )

    def test_env_var_wins_over_smtp_auto_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SMTP_HOST", "smtp-relay.gmail.com")
        monkeypatch.setenv("SMTP_FROM_EMAIL", "no-reply@penguintech.io")
        monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "false")
        assert is_email_verification_required({}) is False

        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.delenv("SMTP_FROM_EMAIL", raising=False)
        monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
        assert is_email_verification_required({}) is True

    def test_defaults_to_smtp_configured_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("REQUIRE_EMAIL_VERIFICATION", raising=False)
        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.delenv("SMTP_FROM_EMAIL", raising=False)
        assert is_email_verification_required({}) is False

        monkeypatch.setenv("SMTP_HOST", "smtp-relay.gmail.com")
        monkeypatch.setenv("SMTP_FROM_EMAIL", "no-reply@penguintech.io")
        assert is_email_verification_required({}) is True

    def test_resolve_tenant_signup_policy_combines_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALLOWED_SIGNUP_DOMAINS", "penguintech.io")
        monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
        policy = resolve_tenant_signup_policy({}, tenant_slug="system")
        assert policy == {
            "allowed_signup_domains": ["penguintech.io"],
            "require_email_verification": True,
            "source": "env_default",
        }


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
