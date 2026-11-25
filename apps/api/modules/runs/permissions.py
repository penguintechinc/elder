"""Canonical grantable permission catalog and egress domain validation for Elder CodeRun."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from typing import Any

CODERUN_PERMISSION_CATALOG: dict[str, tuple[str, ...]] = {
    "resources": (
        "entities:read",
        "entities:write",
        "compute:read",
        "compute:write",
        "networking:read",
        "networking:write",
        "organizations:read",
        "organizations:write",
        "dependencies:read",
        "dependencies:write",
        "software:read",
        "software:write",
        "services:read",
        "services:write",
    ),
    "integrations": (
        "integrations:read",
        "integrations:write",
        "integrations:sync",
    ),
    "workstreams": (
        "webhooks:read",
        "webhooks:write",
        "streams:read",
        "streams:execute",
        "runs:read",
        "runs:invoke",
    ),
    "work_management": (
        "issues:read",
        "issues:write",
        "projects:read",
        "projects:write",
    ),
}

ALL_GRANTABLE_SCOPES: frozenset[str] = frozenset(
    scope for scopes in CODERUN_PERMISSION_CATALOG.values() for scope in scopes
)

_HOSTNAME_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TLD_RE = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9]{1,59})$")
_FORBIDDEN_EXACT_HOSTS: frozenset[str] = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "metadata.azure.internal",
        "metadata.aws.internal",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
_FORBIDDEN_SUFFIXES: tuple[str, ...] = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".home.arpa",
    ".cluster.local",
    ".svc.cluster.local",
    ".nip.io",
    ".xip.io",
    ".sslip.io",
)
_DISALLOWED_HOST_CHARS = frozenset(":/?#@\\[] \t\r\n*")


class ScopeValidationError(ValueError):
    """Raised when requested CodeRun granted_scopes are invalid or escalate privilege."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        invalid_scopes: list[str] | None = None,
        missing_scopes: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.invalid_scopes = invalid_scopes or []
        self.missing_scopes = missing_scopes or []


class EgressDomainValidationError(ValueError):
    """Raised when allowed_egress_domains contains an invalid or forbidden entry."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        invalid_domains: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.invalid_domains = invalid_domains or []


def _normalize_scope_set(scopes: Iterable[str] | str | None) -> set[str]:
    """Normalize a caller's scope collection into a clean set of strings."""
    if scopes is None:
        return set()
    if isinstance(scopes, str):
        return {s.strip() for s in scopes.split() if s.strip()}
    return {s.strip() for s in scopes if isinstance(s, str) and s.strip()}


def validate_granted_scopes(
    requested: Any,
    caller_scopes: Iterable[str] | str | None,
    is_superuser: bool = False,
) -> list[str]:
    """Validate requested CodeRun ``granted_scopes`` against catalog and caller privileges.

    Rejects unknown or ungrantable scopes with HTTP 400 and rejects any scope
    not held by the caller (unless ``is_superuser`` is True) with HTTP 403 to
    prevent privilege escalation.
    """
    if requested is None:
        return []
    if isinstance(requested, (str, bytes)) or not isinstance(
        requested, (list, tuple, set)
    ):
        raise ScopeValidationError(
            "granted_scopes must be a list of scope strings",
            status_code=400,
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for item in requested:
        if not isinstance(item, str) or not item.strip():
            raise ScopeValidationError(
                "granted_scopes must contain only non-empty scope strings",
                status_code=400,
            )
        cleaned = item.strip()
        if cleaned not in seen:
            seen.add(cleaned)
            deduped.append(cleaned)

    unknown = [scope for scope in deduped if scope not in ALL_GRANTABLE_SCOPES]
    if unknown:
        raise ScopeValidationError(
            f"Unknown or ungrantable scope(s): {', '.join(unknown)}",
            status_code=400,
            invalid_scopes=unknown,
        )

    if not is_superuser:
        caller_set = _normalize_scope_set(caller_scopes)
        missing = [scope for scope in deduped if scope not in caller_set]
        if missing:
            raise ScopeValidationError(
                f"Cannot grant scope(s) not held by caller: {', '.join(missing)}",
                status_code=403,
                missing_scopes=missing,
            )

    return deduped


def _is_ip_literal(value: str) -> bool:
    """Return True if *value* parses as an IPv4 or IPv6 address literal."""
    candidate = value.strip("[]")
    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        return False


def validate_egress_domains(domains: Any) -> list[str]:
    """Validate CodeRun ``allowed_egress_domains`` as pure public DNS hostnames.

    Rejects URLs with schemes, paths, ports, wildcards, IP literals (including
    RFC1918, loopback, and link-local/metadata), localhost, and internal cluster
    or cloud metadata hostnames.
    """
    if domains is None:
        return []
    if isinstance(domains, (str, bytes)) or not isinstance(domains, (list, tuple, set)):
        raise EgressDomainValidationError(
            "allowed_egress_domains must be a list of hostnames",
            status_code=400,
        )

    validated: list[str] = []
    seen: set[str] = set()
    invalid: list[str] = []

    for entry in domains:
        if not isinstance(entry, str):
            invalid.append(repr(entry))
            continue

        host = entry.strip().lower()
        if (
            not host
            or len(host) > 253
            or "://" in host
            or any(ch in _DISALLOWED_HOST_CHARS for ch in host)
            or host.startswith(".")
            or host.endswith(".")
            or ".." in host
        ):
            invalid.append(entry)
            continue

        if _is_ip_literal(host):
            invalid.append(entry)
            continue

        if host in _FORBIDDEN_EXACT_HOSTS or any(
            host.endswith(suffix) for suffix in _FORBIDDEN_SUFFIXES
        ):
            invalid.append(entry)
            continue

        labels = host.split(".")
        if len(labels) < 2:
            invalid.append(entry)
            continue

        if not all(_HOSTNAME_LABEL_RE.match(label) for label in labels):
            invalid.append(entry)
            continue

        if not _TLD_RE.match(labels[-1]):
            invalid.append(entry)
            continue

        if host not in seen:
            seen.add(host)
            validated.append(host)

    if invalid:
        raise EgressDomainValidationError(
            f"Invalid or disallowed egress domain(s): {', '.join(invalid)}",
            status_code=400,
            invalid_domains=invalid,
        )

    return validated


def build_permission_catalog_payload(
    caller_scopes: Iterable[str] | str | None,
    is_superuser: bool = False,
) -> dict[str, Any]:
    """Build the response payload for ``GET /api/v1/runs/permissions/catalog``."""
    caller_set = _normalize_scope_set(caller_scopes)
    catalog: dict[str, list[str]] = {}
    groups: dict[str, list[dict[str, Any]]] = {}
    eligible_scopes: list[str] = []

    for group_name, scopes in CODERUN_PERMISSION_CATALOG.items():
        catalog[group_name] = list(scopes)
        group_entries: list[dict[str, Any]] = []
        for scope in scopes:
            eligible = bool(is_superuser or scope in caller_set)
            if eligible:
                eligible_scopes.append(scope)
            group_entries.append({"scope": scope, "eligible": eligible})
        groups[group_name] = group_entries

    return {
        "catalog": catalog,
        "groups": groups,
        "eligible_scopes": eligible_scopes,
    }
