"""Per-tenant and environment-default signup policy for self-service registration.

Supports:
1. Per-tenant ``tenants.settings["allowed_signup_domains"]`` (list of lowercase
   domains; an explicit empty list ``[]`` overrides any environment default and
   allows all domains).
2. Environment default ``ALLOWED_SIGNUP_DOMAINS`` for the ``system``/``default``
   tenant (comma- or whitespace-separated list of domain names).
3. Per-tenant ``tenants.settings["require_email_verification"]`` (bool), falling
   back to ``REQUIRE_EMAIL_VERIFICATION`` env var, and finally to
   ``is_smtp_configured()`` so deployments with SMTP enforce verification
   automatically while offline unit tests remain unbroken.
"""

from __future__ import annotations

import os
import re
from typing import Any

try:
    from apps.api.utils.email import is_smtp_configured
except ImportError:  # pragma: no cover - lightweight unit-test fallback

    def is_smtp_configured() -> bool:
        """Return True if SMTP_HOST and SMTP_FROM_EMAIL are configured."""
        return bool(
            os.environ.get("SMTP_HOST", "").strip()
            and os.environ.get("SMTP_FROM_EMAIL", "").strip()
        )


_SPLIT_RE = re.compile(r"[\s,;]+")
_DEFAULT_ENV_TENANT_SLUGS = frozenset({"system", "default"})
_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSY_VALUES = frozenset({"0", "false", "no", "off"})


def normalize_signup_domains(raw_value: Any) -> tuple[str, ...]:
    """Normalize a string or iterable of domain names into unique lowercase domains.

    Strips whitespace and leading ``@`` characters and preserves declaration order.
    """
    if raw_value is None:
        return ()

    tokens: list[str] = []
    if isinstance(raw_value, str):
        if raw_value.strip():
            tokens.extend(_SPLIT_RE.split(raw_value.strip()))
    elif isinstance(raw_value, (list, tuple, set)):
        for item in raw_value:
            if isinstance(item, str) and item.strip():
                tokens.extend(_SPLIT_RE.split(item.strip()))
    else:
        return ()

    seen: set[str] = set()
    domains: list[str] = []
    for token in tokens:
        cleaned = token.strip().lstrip("@").lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            domains.append(cleaned)
    return tuple(domains)


def get_allowed_signup_domains(raw_value: str | None = None) -> tuple[str, ...]:
    """Return normalized lowercase signup domains configured in the environment.

    Args:
        raw_value: Optional override for ``ALLOWED_SIGNUP_DOMAINS`` (used in tests).

    Returns:
        Tuple of unique lowercase domain names in declaration order, or an
        empty tuple when no allowlist is configured.
    """
    value = (
        os.environ.get("ALLOWED_SIGNUP_DOMAINS", "") if raw_value is None else raw_value
    )
    return normalize_signup_domains(value)


def resolve_allowed_signup_domains(
    tenant_settings: dict[str, Any] | None = None,
    *,
    tenant_slug: str | None = None,
    raw_allowed: str | None = None,
) -> tuple[tuple[str, ...], str]:
    """Resolve effective allowed signup domains and their policy source.

    Precedence:
        1. ``tenant_settings["allowed_signup_domains"]`` when present (even if
           ``[]``, which explicitly opens signup for that tenant) -> ``"tenant"``.
        2. ``ALLOWED_SIGNUP_DOMAINS`` env var when ``tenant_slug`` is
           ``"system"``, ``"default"``, or omitted (``None``) and non-empty
           -> ``"env_default"``.
        3. Empty tuple -> ``"open"``.
    """
    if (
        isinstance(tenant_settings, dict)
        and "allowed_signup_domains" in tenant_settings
        and tenant_settings["allowed_signup_domains"] is not None
    ):
        return normalize_signup_domains(
            tenant_settings["allowed_signup_domains"]
        ), "tenant"

    slug_normalized = (
        tenant_slug.strip().lower() if isinstance(tenant_slug, str) else None
    )
    if slug_normalized is None or slug_normalized in _DEFAULT_ENV_TENANT_SLUGS:
        env_domains = get_allowed_signup_domains(raw_allowed)
        if env_domains:
            return env_domains, "env_default"

    return (), "open"


def is_email_verification_required(
    tenant_settings: dict[str, Any] | None = None,
) -> bool:
    """Return True if self-service signups require email verification.

    Precedence:
        1. Explicit ``tenant_settings["require_email_verification"]`` (bool).
        2. Explicit ``REQUIRE_EMAIL_VERIFICATION`` env var (``true``/``false``).
        3. ``is_smtp_configured()`` auto-default.
    """
    if (
        isinstance(tenant_settings, dict)
        and "require_email_verification" in tenant_settings
        and tenant_settings["require_email_verification"] is not None
    ):
        val = tenant_settings["require_email_verification"]
        if isinstance(val, str):
            lowered = val.strip().lower()
            if lowered in _TRUTHY_VALUES:
                return True
            if lowered in _FALSY_VALUES:
                return False
        return bool(val)

    env_raw = (os.environ.get("REQUIRE_EMAIL_VERIFICATION") or "").strip().lower()
    if env_raw in _TRUTHY_VALUES:
        return True
    if env_raw in _FALSY_VALUES:
        return False

    return is_smtp_configured()


def resolve_tenant_signup_policy(
    tenant_settings: dict[str, Any] | None = None,
    *,
    tenant_slug: str | None = None,
    raw_allowed: str | None = None,
) -> dict[str, Any]:
    """Return the effective signup policy dict for a tenant."""
    domains, source = resolve_allowed_signup_domains(
        tenant_settings=tenant_settings,
        tenant_slug=tenant_slug,
        raw_allowed=raw_allowed,
    )
    return {
        "allowed_signup_domains": list(domains),
        "require_email_verification": is_email_verification_required(
            tenant_settings=tenant_settings
        ),
        "source": source,
    }


def extract_email_domain(email: str) -> str:
    """Extract the normalized lowercase domain portion of an email address."""
    if not isinstance(email, str) or "@" not in email:
        return ""
    local, _, domain = email.strip().rpartition("@")
    if not local or not domain:
        return ""
    return domain.strip().lower()


def is_signup_email_allowed(
    email: str,
    *,
    raw_allowed: str | None = None,
    tenant_settings: dict[str, Any] | None = None,
    tenant_slug: str | None = None,
) -> bool:
    """Return True if ``email`` is permitted to self-register under current policy.

    Matches the exact domain name (case-insensitively). Subdomains are NOT
    implicitly matched unless explicitly listed. When the resolved allowlist is
    empty, all valid emails are allowed.
    """
    allowed, _source = resolve_allowed_signup_domains(
        tenant_settings=tenant_settings,
        tenant_slug=tenant_slug,
        raw_allowed=raw_allowed,
    )
    if not allowed:
        return True
    domain = extract_email_domain(email)
    return bool(domain) and domain in allowed
