"""Exception hierarchy and HTTP error mapping for the Elder Python SDK."""

from __future__ import annotations

from typing import Any

import httpx


class ElderError(Exception):
    """Base exception for all Elder SDK errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        details: Any = None,
        request_id: str | None = None,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.details = details
        self.request_id = request_id
        prefix = f"[{status_code}] " if status_code is not None else ""
        super().__init__(f"{prefix}{message}")

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(message={self.message!r}, "
            f"status_code={self.status_code!r}, error_code={self.error_code!r})"
        )


class ElderAuthError(ElderError):
    """Raised on authentication or authorization failures (HTTP 401/403)."""


class ElderNotFoundError(ElderError):
    """Raised when the requested resource does not exist (HTTP 404)."""


class ElderValidationError(ElderError):
    """Raised on malformed requests or validation failures (HTTP 400/422)."""


class ElderRateLimitError(ElderError):
    """Raised when the API rate limit is exceeded (HTTP 429)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = 429,
        error_code: str | None = None,
        details: Any = None,
        request_id: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.retry_after = retry_after
        super().__init__(
            message,
            status_code=status_code,
            error_code=error_code,
            details=details,
            request_id=request_id,
        )


class ElderServerError(ElderError):
    """Raised on server-side errors (HTTP 5xx)."""


class ElderTransportError(ElderError):
    """Raised on network, DNS, TLS, or timeout failures."""


class ElderCatalogError(ElderError):
    """Raised on invalid service, operation, or path parameter lookup."""


def _parse_retry_after(header_value: str | None) -> float | None:
    """Parse numeric Retry-After header seconds if present."""
    if not header_value:
        return None
    try:
        parsed = float(header_value.strip())
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _extract_error_fields(
    response: httpx.Response,
) -> tuple[str, str | None, Any]:
    """Extract human message, machine error_code, and details from a response."""
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        msg = text[:300] if text else (response.reason_phrase or "Request failed")
        return msg, None, text or None

    if isinstance(payload, dict):
        msg = str(
            payload.get("message")
            or payload.get("error")
            or payload.get("detail")
            or response.reason_phrase
            or "Request failed"
        )
        raw_code = payload.get("errorCode") or payload.get("error_code")
        error_code = str(raw_code) if raw_code is not None else None
        details = payload.get("details") if "details" in payload else payload
        return msg, error_code, details

    return str(payload), None, payload


def error_from_response(response: httpx.Response) -> ElderError:
    """Build the appropriate ``ElderError`` subclass from an HTTP response."""
    status = response.status_code
    message, error_code, details = _extract_error_fields(response)
    request_id = response.headers.get("X-Request-Id") or response.headers.get(
        "x-request-id"
    )

    common_kwargs: dict[str, Any] = {
        "status_code": status,
        "error_code": error_code,
        "details": details,
        "request_id": request_id,
    }

    if status in (401, 403):
        return ElderAuthError(message, **common_kwargs)
    if status == 404:
        return ElderNotFoundError(message, **common_kwargs)
    if status in (400, 422):
        return ElderValidationError(message, **common_kwargs)
    if status == 429:
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        return ElderRateLimitError(
            message,
            retry_after=retry_after,
            **common_kwargs,
        )
    if status >= 500:
        return ElderServerError(message, **common_kwargs)
    return ElderError(message, **common_kwargs)


def raise_for_status(response: httpx.Response) -> None:
    """Raise a typed ``ElderError`` when ``response.status_code >= 400``."""
    if response.status_code >= 400:
        raise error_from_response(response)
