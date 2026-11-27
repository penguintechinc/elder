"""Configurable email-domain allowlist for self-service user signups.

Reads ``ALLOWED_SIGNUP_DOMAINS`` from the environment (comma- or
whitespace-separated list of domain names, e.g. ``penguintech.io``).
When unset or empty, no domain restriction is applied so deployments
control signup domains strictly through configuration rather than
hardcoding them in application code.
"""

from __future__ import annotations

import os
import re

_SPLIT_RE = re.compile(r"[\s,;]+")


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
    if not value or not value.strip():
        return ()

    seen: set[str] = set()
    domains: list[str] = []
    for token in _SPLIT_RE.split(value.strip()):
        cleaned = token.strip().lstrip("@").lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            domains.append(cleaned)
    return tuple(domains)


def extract_email_domain(email: str) -> str:
    """Extract the normalized lowercase domain portion of an email address."""
    if not isinstance(email, str) or "@" not in email:
        return ""
    local, _, domain = email.strip().rpartition("@")
    if not local or not domain:
        return ""
    return domain.strip().lower()


def is_signup_email_allowed(email: str, *, raw_allowed: str | None = None) -> bool:
    """Return True if ``email`` is permitted to self-register under current config.

    Matches the exact domain name (case-insensitively). Subdomains are NOT
    implicitly matched unless explicitly listed in ``ALLOWED_SIGNUP_DOMAINS``.
    When ``ALLOWED_SIGNUP_DOMAINS`` is empty/unset, all valid emails are allowed.
    """
    allowed = get_allowed_signup_domains(raw_allowed)
    if not allowed:
        return True
    domain = extract_email_domain(email)
    return bool(domain) and domain in allowed
