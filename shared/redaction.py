"""Helpers for keeping credentials out of logs and API responses.

Connection URLs routinely carry a password in their userinfo section. Logging
them verbatim writes that password to stdout and onward into whatever collects
container logs, so URLs are redacted before they are logged.

Several `*_configs`/`*_sync_jobs` tables store a free-form `config_json` blob
that legitimately holds third-party credentials alongside operational fields
(e.g. sync_configs.config_json's GitHub `api_token`, cost_sync_jobs's AWS
`aws_access_key_id`/`aws_secret_access_key`) -- `redact_credential_keys`
scrubs known credential keys before that blob is ever serialized in an API
response, without dropping the non-secret fields callers still need.
"""

from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

# Keys that hold credential material wherever they appear in a config/settings
# blob -- redact regardless of which provider/table the blob belongs to.
CREDENTIAL_KEYS = frozenset(
    {
        "api_token",
        "access_token",
        "api_key",
        "token",
        "password",
        "secret",
        "client_secret",
        "private_key",
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_session_token",
        "service_account_key",
        "service_account_json",
    }
)

REDACTED_PLACEHOLDER = "***REDACTED***"


def redact_credential_keys(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a copy of `config` with known credential keys replaced by a placeholder.

    Only top-level keys are checked (matches how these config blobs are
    populated today); unknown/operational keys pass through unchanged.
    """
    if not config:
        return config
    return {
        k: (REDACTED_PLACEHOLDER if k.lower() in CREDENTIAL_KEYS else v)
        for k, v in config.items()
    }


def redact_url(url: str | None) -> str:
    """Return a connection URL with any embedded password replaced by ``***``.

    Falls back to a bare placeholder if the URL cannot be parsed, so a
    malformed value can never leak its contents through the error path.
    """
    if not url:
        return ""

    # No userinfo marker means there is no password to hide.
    if "@" not in url:
        return url

    # urlsplit is lazy: .password/.hostname/.port do the real parsing and are
    # what raise on a malformed authority, so they must be inside the guard.
    try:
        parts = urlsplit(url)
        # An '@' with no parsed authority means we cannot tell which part is a
        # credential, so refuse to echo the value at all.
        if not parts.netloc:
            return "<unparseable url>"

        password = parts.password
        if not password:
            return url

        user = parts.username or ""
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return "<unparseable url>"

    netloc = f"{user}:***@{host}"
    if port:
        netloc = f"{netloc}:{port}"

    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
