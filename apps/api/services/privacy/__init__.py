"""Self-service DSAR (Data Subject Access Rights) service package."""

from apps.api.services.privacy.service import LegalHoldError, PrivacyService

__all__ = ["LegalHoldError", "PrivacyService"]
