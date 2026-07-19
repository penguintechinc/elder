"""Helpers for keeping credentials out of logs.

Connection URLs routinely carry a password in their userinfo section. Logging
them verbatim writes that password to stdout and onward into whatever collects
container logs, so URLs are redacted before they are logged.
"""

from typing import Optional
from urllib.parse import urlsplit, urlunsplit


def redact_url(url: Optional[str]) -> str:
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
