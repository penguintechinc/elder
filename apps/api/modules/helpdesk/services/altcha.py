"""Altcha proof-of-work challenge generation and solution verification.

Implements the standard Altcha protocol (https://altcha.org/) used to gate
public intake-form submissions against automated spam without a third-party
CAPTCHA service: the server issues a challenge embedding a hidden random
number, the client brute-forces that number client-side (proof of work),
and the server re-derives + verifies the result. This supersedes the
placeholder `/captcha-challenge` route in `auth.py`, which does not embed a
solvable number and cannot be verified against.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
from typing import Any


def _secret() -> str:
    """Return the current `CAPTCHA_SECRET`, read fresh on every call.

    Reading at call time (rather than caching a module-level constant at
    import time) lets the secret be rotated at runtime and ensures a
    later-set env var is always honored instead of an import-time default
    silently sticking for the life of the process.
    """
    return os.environ.get("CAPTCHA_SECRET", "elder-captcha-default")


def _hmac_sha256(secret: str, message: str) -> str:
    """Return the hex-encoded HMAC-SHA256 of `message` keyed by `secret`."""
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def create_challenge(difficulty: int = 50000) -> dict[str, Any]:
    """Generate a new Altcha proof-of-work challenge.

    Picks a random `number` in `[0, difficulty)` that the client must find
    by brute force, embeds it (as a SHA-256 digest of `salt + number`) in
    the returned `challenge`, and signs the challenge with `CAPTCHA_SECRET`
    so `verify_solution` can later confirm the challenge wasn't forged.
    The solution `number` itself is never returned to the caller.
    """
    if difficulty <= 0:
        raise ValueError("difficulty must be >= 1")
    number = secrets.randbelow(difficulty)
    salt = secrets.token_hex(12)
    challenge = hashlib.sha256(f"{salt}{number}".encode()).hexdigest()
    signature = _hmac_sha256(_secret(), challenge)
    return {
        "algorithm": "SHA-256",
        "challenge": challenge,
        "salt": salt,
        "maxnumber": difficulty,
        "signature": signature,
    }


def verify_solution(payload: dict[str, Any] | str) -> bool:
    """Verify a client-submitted Altcha solution.

    Accepts either the widget's solution object (`dict`) or its
    base64-encoded JSON string form. Returns True iff the submitted
    `number` reproduces `challenge` via SHA-256(salt + number) AND the
    submitted `challenge` carries a valid HMAC signature for
    `CAPTCHA_SECRET` (compared in constant time). Any malformed input,
    missing field, or unexpected exception yields False rather than
    raising, since this gates untrusted public form submissions.
    """
    try:
        if isinstance(payload, str):
            payload = json.loads(base64.b64decode(payload))

        if payload.get("algorithm") != "SHA-256":
            return False

        challenge = payload["challenge"]
        number = payload["number"]
        salt = payload["salt"]
        signature = payload["signature"]

        expected_challenge = hashlib.sha256(f"{salt}{number}".encode()).hexdigest()
        if expected_challenge != challenge:
            return False

        expected_signature = _hmac_sha256(_secret(), challenge)
        return hmac.compare_digest(expected_signature, signature)
    except Exception:
        return False
