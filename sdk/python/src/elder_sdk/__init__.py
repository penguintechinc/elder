"""Official Python SDK for the Elder platform API."""

from elder_sdk.auth import ApiKeyAuth, AuthConfig, BearerAuth
from elder_sdk.catalog import ApiCatalog, ApiOperation, AsyncServiceProxy, ServiceProxy
from elder_sdk.client import AsyncElderClient, ElderClient
from elder_sdk.exceptions import (
    ElderAuthError,
    ElderCatalogError,
    ElderError,
    ElderNotFoundError,
    ElderRateLimitError,
    ElderServerError,
    ElderTransportError,
    ElderValidationError,
)

__version__ = "4.0.0"

__all__ = [
    "__version__",
    "ApiCatalog",
    "ApiKeyAuth",
    "ApiOperation",
    "AsyncElderClient",
    "AsyncServiceProxy",
    "AuthConfig",
    "BearerAuth",
    "ElderAuthError",
    "ElderCatalogError",
    "ElderClient",
    "ElderError",
    "ElderNotFoundError",
    "ElderRateLimitError",
    "ElderServerError",
    "ElderTransportError",
    "ElderValidationError",
    "ServiceProxy",
]
