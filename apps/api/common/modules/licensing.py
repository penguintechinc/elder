"""Module licensing helper with graceful degradation.

Provides runtime license checking for feature modules with fail-soft behavior.
If the license client is unavailable or raises, gracefully degrades to allowing
the module (never crashes, never hard-denies on infra failure).
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger()

_BYPASS_SUFFIXES = (
    ".penguincloud.io",
    ".penguintech.cloud",
    ".elder.app",
    ".elderrms.app",
)
_BYPASS_EXACT = (
    "penguincloud.io",
    "penguintech.cloud",
    "elder.app",
    "elderrms.app",
)
_HOSTNAME_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)


def _extract_hostname(raw: str | None) -> str:
    """Extract a validated lowercase DNS hostname (without port) from a URL or Host header."""
    if not raw or not isinstance(raw, str):
        return ""
    value = raw.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1].strip()
    if not value:
        return ""

    if "://" in value:
        parsed = urlparse(value)
        if "@" in (parsed.netloc or ""):
            return ""
        try:
            _ = parsed.port
        except ValueError:
            return ""
        host = (parsed.hostname or "").lower()
    else:
        if value.startswith("["):
            return ""
        if ":" in value:
            host_part, port_part = value.split(":", 1)
            if not port_part.isdigit() or not (1 <= int(port_part) <= 65535):
                return ""
            host = host_part.strip().lower()
        else:
            host = value.lower()

    if host.endswith("."):
        host = host[:-1]

    if not host or len(host) > 253 or not _HOSTNAME_RE.match(host):
        return ""
    return host


def _extract_forwarded_hosts(forwarded: str) -> list[str]:
    """Extract all ``host=...`` directive values from an RFC 7239 Forwarded header."""
    hosts: list[str] = []
    for element in forwarded.split(","):
        for param in element.split(";"):
            key, sep, val = param.partition("=")
            if not sep or key.strip().lower() != "host":
                continue
            cleaned = val.strip()
            if len(cleaned) >= 2 and cleaned[0] == '"' and cleaned[-1] == '"':
                cleaned = cleaned[1:-1].strip()
            if cleaned:
                hosts.append(cleaned)
    return hosts


def _matches_bypass_domain(host: str) -> bool:
    """Return True if a validated lowercase hostname matches an allowed bypass domain."""
    if not host:
        return False
    return host in _BYPASS_EXACT or any(host.endswith(s) for s in _BYPASS_SUFFIXES)


def get_matched_bypass_domain() -> str | None:
    """Return the matched bypass hostname (lowercase, without port) or None.

    Inspects the active Quart request headers (``X-Forwarded-Host``,
    ``Forwarded``, and ``Host``) as well as ``SITE_URL`` and ``APP_DOMAIN``.
    """
    candidates: list[str] = []
    try:
        from quart import has_request_context, request

        if has_request_context():
            xfh = request.headers.get("X-Forwarded-Host", "")
            if xfh:
                candidates.extend(
                    part.strip() for part in xfh.split(",") if part.strip()
                )

            forwarded = request.headers.get("Forwarded", "")
            if forwarded:
                candidates.extend(_extract_forwarded_hosts(forwarded))

            if request.host:
                candidates.append(request.host)
    except Exception as exc:
        logger.debug("bypass_domain_request_context_unavailable", error=str(exc))

    candidates.append(os.environ.get("SITE_URL", ""))
    candidates.append(os.environ.get("APP_DOMAIN", ""))

    for raw in candidates:
        host = _extract_hostname(raw)
        if _matches_bypass_domain(host):
            return host
    return None


def is_bypass_domain() -> bool:
    """Return True if the active request host or SITE_URL/APP_DOMAIN is a bypass domain."""
    return get_matched_bypass_domain() is not None


def module_licensed(app: Any, manifest: Any) -> bool:
    """Check if a module is licensed to run.

    Returns True if:
    - manifest.license_feature is None (always available)
    - license client is not available (graceful degradation)
    - license client confirms the feature is licensed

    Returns False if:
    - manifest.license_feature is set AND license client says it's NOT licensed

    On any error (license client unavailable, check fails, etc.):
    - Logs warning, defaults to True (allow the module)
    - Never crashes, never hard-denies on infra failure

    Args:
        app: Quart application with license_client in app.extensions
        manifest: ModuleManifest with optional .license_feature field

    Returns:
        bool: True if module is licensed (or license check unavailable), False if explicitly unlicensed
    """
    # Module doesn't require a license feature
    if manifest.license_feature is None:
        return True

    if is_bypass_domain():
        return True

    try:
        license_client = app.extensions.get("license_client")

        # No license client available (licensing disabled or failed at init)
        if license_client is None:
            logger.warning(
                "license_check_skipped_no_client",
                module=manifest.name,
                feature=manifest.license_feature,
                fallback="allow",
            )
            return True

        # Check if the feature is licensed
        is_licensed = license_client.check_feature(manifest.license_feature)
        logger.debug(
            "license_checked",
            module=manifest.name,
            feature=manifest.license_feature,
            licensed=is_licensed,
        )
        return is_licensed

    except Exception as e:
        # Graceful degradation on any error
        logger.warning(
            "license_check_failed",
            module=manifest.name,
            feature=getattr(manifest, "license_feature", None),
            error=str(e),
            fallback="allow",
        )
        return True  # Default to allowing the module
