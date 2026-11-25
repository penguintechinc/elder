"""Pure-Python FleetDM sanitization and HTTP parsing helpers.

Shared between ``apps.worker.connectors.fleetdm_connector`` and
``apps.worker.discovery.fleetdm_discovery`` so that importing the discovery
service inside ``elder-api`` does not transitively load worker-only connector
dependencies (e.g. ``google-cloud-compute``, ``ldap3``).
"""

# flake8: noqa: E501

from typing import Any

import httpx

_SENSITIVE_KEY_EXACT = frozenset(
    {
        "author_email",
        "author_name",
        "author_id",
        "device_mapping",
        "end_users",
        "encryption_key",
        "recovery_key",
        "filevault_key",
        "bitlocker_recovery_key",
        "unlock_pin",
        "bootstrap_token",
        "escrow_key",
        "password",
        "secret",
        "token",
        "api_token",
        "api_key",
        "private_key",
        "secret_key",
        "profile_contents",
        "mobileconfig",
    }
)

_SENSITIVE_KEY_SUBSTRINGS = (
    "password",
    "secret",
    "token",
    "encryption_key",
    "recovery_key",
    "unlock_pin",
    "private_key",
)


def _is_sensitive_key(key: str) -> bool:
    """Return True if a key represents PII or secret material."""
    lower = key.lower()
    if lower in _SENSITIVE_KEY_EXACT:
        return True
    return any(sub in lower for sub in _SENSITIVE_KEY_SUBSTRINGS)


def _sanitize_dict(data: Any) -> Any:
    """Recursively strip PII and secret keys from dicts/lists."""
    if isinstance(data, dict):
        return {
            k: _sanitize_dict(v)
            for k, v in data.items()
            if not _is_sensitive_key(str(k))
        }
    if isinstance(data, list):
        return [_sanitize_dict(item) for item in data]
    return data


def _extract_safe_mdm(raw_mdm: Any) -> dict[str, Any] | None:
    """Return PII/secret-free subset of a host's MDM metadata."""
    if not isinstance(raw_mdm, dict):
        return None
    mdm_subset = {
        k: raw_mdm.get(k)
        for k in (
            "enrollment_status",
            "connected_to_fleet",
            "name",
            "macos_settings",
            "os_settings",
            "device_status",
            "pending_action",
        )
        if k in raw_mdm
    }
    return _sanitize_dict(mdm_subset)


def _cvss_to_severity(cvss_score: float | int | None) -> str:
    """Map CVSS score to Elder severity label, handling None safely."""
    if cvss_score is None:
        return "unknown"
    if cvss_score >= 9.0:
        return "critical"
    if cvss_score >= 7.0:
        return "high"
    if cvss_score >= 4.0:
        return "medium"
    return "low"


def _safe_json(response: httpx.Response) -> Any:
    """Safely parse JSON body, returning None for empty or invalid payloads."""
    if response.status_code == 204:
        return None
    try:
        return response.json()
    except (ValueError, TypeError):
        return None


def _parse_retry_after(response: httpx.Response, attempt: int) -> float:
    """Parse Retry-After header with bounded fallback backoff."""
    headers = getattr(response, "headers", None)
    raw = headers.get("Retry-After") if headers else None
    if raw is not None:
        try:
            return min(max(float(raw), 0.0), 30.0)
        except (TypeError, ValueError):
            pass
    return min(float(2**attempt), 10.0)
