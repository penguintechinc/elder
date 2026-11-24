"""Claim tokens binding a scanner job's completion to the claimant.

A scanner that wins the atomic claim on an SBOM scan or discovery job is
handed a random token; only its SHA-256 digest is stored on the row. The
completion endpoints require the token so a different caller (or a claimant
whose lease expired and whose job was re-claimed) cannot complete the job.
"""

from __future__ import annotations

import hashlib
import os
import secrets

import structlog

logger = structlog.get_logger(__name__)

CLAIM_TOKEN_HEADER = "X-Claim-Token"  # nosec B105 - header name, not a secret
CLAIM_TOKEN_REQUIRED_ENV = "ELDER_SCANNER_CLAIM_TOKEN_REQUIRED"  # nosec B105
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_MAX_TOKEN_LENGTH = 256


def hash_claim_token(token: str) -> str:
    """Return the hex SHA-256 digest stored in ``claim_token_hash`` columns.

    Storing the digest instead of the token means a database read alone
    never yields a credential that can complete someone else's job.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_claim_token() -> tuple[str, str]:
    """Mint an unguessable claim token (256 bits of entropy).

    Returns:
        ``(token, token_hash)`` -- the token goes to the claimant once, the
        hash goes on the claimed row.
    """
    token = secrets.token_urlsafe(32)
    return token, hash_claim_token(token)


def normalize_claim_token(raw: object) -> str | None:
    """Return a usable token string, or None for missing/oversized input.

    Tokens arrive from request bodies and headers, so anything that is not
    a short non-blank string is treated as "no token supplied".
    """
    if not isinstance(raw, str):
        return None
    token = raw.strip()
    if not token or len(token) > _MAX_TOKEN_LENGTH:
        return None
    return token


def claim_token_required() -> bool:
    """Whether completions must present the claim token (default: yes).

    ``ELDER_SCANNER_CLAIM_TOKEN_REQUIRED=false`` is a transition switch for
    fleets still running scanners that predate claim tokens. Anything other
    than an explicit false value -- including garbage -- keeps enforcement
    on, so a typo can only fail closed.
    """
    raw = (os.environ.get(CLAIM_TOKEN_REQUIRED_ENV) or "").strip().lower()
    if raw in _FALSE_VALUES:
        logger.warning("scanner_claim_token_enforcement_disabled")
        return False
    return True
