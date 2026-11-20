"""Validation helpers for SSO IdP URLs and X.509 certificates.

regression: gh-312 -- enforces ``https://`` for all IdP HTTP endpoint URLs
and checks X.509 certificate PEM structure and ``not_valid_after_utc`` expiry
both at CRUD time (create/update) and during SAML login / assertion handling.
"""

from __future__ import annotations

import datetime
from urllib.parse import urlsplit

from cryptography import x509

from apps.api.services.sso.idp_access import IdPValidationError

# IdP configuration fields that hold HTTP(S) endpoint URLs and must use https://.
# ``entity_id`` is intentionally excluded because SAML Entity IDs may be URNs
# (e.g. ``urn:example:idp``).
HTTPS_URL_FIELDS: frozenset[str] = frozenset(
    {
        "metadata_url",
        "sso_url",
        "slo_url",
        "oidc_issuer_url",
    }
)


def validate_https_url(url: str, *, field: str = "url") -> str:
    """Validate that ``url`` is a well-formed ``https://`` URL with a host.

    Raises ``IdPValidationError`` with a static message (never echoing the
    input value) on any non-HTTPS scheme, missing host, or embedded whitespace.
    """
    if not isinstance(url, str) or not url or any(ch.isspace() for ch in url):
        raise IdPValidationError(
            field, "URL must use the https:// scheme with a valid host"
        )

    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise IdPValidationError(
            field, "URL must use the https:// scheme with a valid host"
        )

    return url


def validate_x509_certificate(
    pem_data: str,
    *,
    now: datetime.datetime | None = None,
    field: str = "certificate",
) -> x509.Certificate:
    """Parse a PEM-encoded X.509 certificate and verify it has not expired.

    Args:
        pem_data: PEM-encoded X.509 certificate string
        now: Optional UTC reference timestamp (defaults to ``datetime.now(UTC)``)
        field: Schema field name reported in ``IdPValidationError``

    Returns:
        The parsed ``cryptography.x509.Certificate`` object.

    Raises:
        IdPValidationError: If the PEM cannot be parsed or ``not_valid_after_utc``
            is at or before ``now``.
    """
    if not isinstance(pem_data, str) or not pem_data.strip():
        raise IdPValidationError(field, "Invalid X.509 PEM certificate")

    try:
        cert = x509.load_pem_x509_certificate(pem_data.strip().encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise IdPValidationError(field, "Invalid X.509 PEM certificate") from exc

    current_time = now if now is not None else datetime.datetime.now(datetime.UTC)
    if cert.not_valid_after_utc <= current_time:
        raise IdPValidationError(field, "IdP X.509 certificate has expired")

    return cert
