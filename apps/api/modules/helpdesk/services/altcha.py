"""Altcha proof-of-work challenge generation and solution verification.

Implements the standard Altcha protocol (https://altcha.org/) used to gate
public intake-form submissions against automated spam without a third-party
CAPTCHA service: the server issues a challenge embedding a hidden random
number, the client brute-forces that number client-side (proof of work),
and the server re-derives + verifies the result. This supersedes the
placeholder `/captcha-challenge` route in `auth.py`, which does not embed a
solvable number and cannot be verified against.

Every challenge also embeds a signed unix `timestamp` so a solution can only
be redeemed within a short window (`CHALLENGE_TTL_SECONDS`) after issuance —
without this, a single solved challenge could be replayed against the public
submit endpoint indefinitely. Single-use enforcement (so a still-fresh
solution can't be replayed twice within the window) is layered on top by the
caller (see `intake_forms.py`), since that requires Redis, which this module
intentionally has no dependency on.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Optional

#: Maximum age, in seconds, of a challenge's signed `timestamp` at the time
#: its solution is submitted. Bounds how long a captured/solved challenge
#: remains redeemable, closing the indefinite-replay window a bare
#: proof-of-work + signature check (with no expiry) would otherwise leave
#: open.
CHALLENGE_TTL_SECONDS = 300


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
    the returned `challenge`, and signs `challenge + timestamp` with
    `CAPTCHA_SECRET` so `verify_solution` can later confirm neither the
    challenge nor its issuance time were forged. The solution `number`
    itself is never returned to the caller.
    """
    if difficulty <= 0:
        raise ValueError("difficulty must be >= 1")
    number = secrets.randbelow(difficulty)
    salt = secrets.token_hex(12)
    challenge = hashlib.sha256(f"{salt}{number}".encode()).hexdigest()
    timestamp = int(time.time())
    signature = _hmac_sha256(_secret(), f"{challenge}{timestamp}")
    return {
        "algorithm": "SHA-256",
        "challenge": challenge,
        "salt": salt,
        "maxnumber": difficulty,
        "signature": signature,
        "timestamp": timestamp,
    }


def _decode_payload(payload: dict[str, Any] | str) -> dict[str, Any]:
    """Normalize a widget solution payload to a `dict`.

    Accepts either the widget's solution object (`dict`) directly or its
    base64-encoded JSON string form; decodes the latter. Raises on
    malformed input — callers are expected to catch broadly, since these
    payloads are always untrusted/public input.
    """
    if isinstance(payload, str):
        return json.loads(base64.b64decode(payload))
    return payload


def extract_challenge(payload: dict[str, Any] | str) -> str | None:
    """Return the `challenge` id embedded in a submitted Altcha payload.

    Used to key the single-use "already consumed" Redis marker independent
    of full signature/PoW verification, so callers can dedupe a payload
    even outside `verify_solution`'s own decode path. Returns None on any
    malformed input rather than raising.
    """
    try:
        decoded = _decode_payload(payload)
        challenge = decoded.get("challenge")
        return challenge if isinstance(challenge, str) else None
    except Exception:
        return None


def verify_solution(payload: dict[str, Any] | str) -> bool:
    """Verify a client-submitted Altcha solution.

    Accepts either the widget's solution object (`dict`) or its
    base64-encoded JSON string form. Returns True iff ALL of the following
    hold, otherwise False:
      - the submitted `number` reproduces `challenge` via
        SHA-256(salt + number);
      - `challenge + timestamp` carries a valid HMAC signature for
        `CAPTCHA_SECRET` (compared in constant time) — this also proves
        `timestamp` itself wasn't tampered with, since it's covered by the
        signature;
      - `timestamp` is no older than `CHALLENGE_TTL_SECONDS`, so a solved
        challenge can't be redeemed indefinitely after issuance.
    Any malformed input, missing field, or unexpected exception yields
    False rather than raising, since this gates untrusted public form
    submissions.
    """
    try:
        payload = _decode_payload(payload)

        if payload.get("algorithm") != "SHA-256":
            return False

        challenge = payload["challenge"]
        number = payload["number"]
        salt = payload["salt"]
        signature = payload["signature"]
        timestamp = int(payload["timestamp"])

        expected_challenge = hashlib.sha256(f"{salt}{number}".encode()).hexdigest()
        if expected_challenge != challenge:
            return False

        expected_signature = _hmac_sha256(_secret(), f"{challenge}{timestamp}")
        if not hmac.compare_digest(expected_signature, signature):
            return False

        if int(time.time()) - timestamp > CHALLENGE_TTL_SECONDS:
            return False

        return True
    except Exception:
        return False
