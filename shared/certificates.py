"""Certificate lifecycle helpers shared by the API and the discovery worker.

This lives in ``shared`` rather than under ``apps.api`` because the worker image
does not ship ``apps/api`` — importing across that boundary raises
ModuleNotFoundError at runtime.
"""

from datetime import date
from typing import Optional


def calculate_certificate_status(
    expiration_date: Optional[date],
    renewal_days_before: int,
    is_revoked: bool,
) -> str:
    """
    Calculate certificate status based on expiration date and revocation status.

    Args:
        expiration_date: Certificate expiration date (None if unknown)
        renewal_days_before: Days before expiration to mark as "expiring_soon"
        is_revoked: Whether the certificate is revoked

    Returns:
        Status string: "expired", "expiring_soon", "active", "revoked", or
        "pending" when no expiration date is available yet. All of these are
        members of VALID_STATUSES in the certificates API.
    """
    if is_revoked:
        return "revoked"

    if expiration_date is None:
        return "pending"

    today = date.today()

    if expiration_date < today:
        return "expired"

    days_until_expiration = (expiration_date - today).days

    if days_until_expiration <= renewal_days_before:
        return "expiring_soon"

    return "active"
