"""Unit tests for the Altcha proof-of-work challenge/verifier service.

Covers the standard Altcha protocol round-trip (solve a low-difficulty
challenge, verify the solution), rejection of a tampered solution, and
rejection of an otherwise-valid solution whose signed timestamp has expired
(security-review fix: replay protection — see altcha.py CHALLENGE_TTL_SECONDS).
"""

import hashlib
import os

os.environ.setdefault("CAPTCHA_SECRET", "test-secret")
from apps.api.modules.helpdesk.services.altcha import (  # noqa: E402
    _hmac_sha256,
    _secret,
    create_challenge,
    verify_solution,
)


def _solve(ch):
    for n in range(ch["maxnumber"] + 1):
        if hashlib.sha256(f"{ch['salt']}{n}".encode()).hexdigest() == ch["challenge"]:
            return {
                "algorithm": ch["algorithm"],
                "challenge": ch["challenge"],
                "number": n,
                "salt": ch["salt"],
                "signature": ch["signature"],
                "timestamp": ch["timestamp"],
            }
    raise AssertionError("unsolvable")


def test_solve_and_verify():
    ch = create_challenge(difficulty=2000)
    assert verify_solution(_solve(ch)) is True


def test_forged_fails():
    ch = create_challenge(difficulty=2000)
    bad = _solve(ch)
    bad["number"] = bad["number"] + 1
    assert verify_solution(bad) is False


def test_expired_timestamp_fails():
    """A solution with a valid PoW + valid signature, but whose signed
    timestamp is older than the 5-minute window, must be rejected. The
    signature is recomputed over the old timestamp (mirroring what a real
    stale-but-otherwise-legitimate client would submit) rather than reusing
    the original signature, so this exercises the expiry check itself
    rather than incidentally failing signature verification."""
    ch = create_challenge(difficulty=2000)
    solved = _solve(ch)
    expired_timestamp = solved["timestamp"] - 301
    solved["timestamp"] = expired_timestamp
    solved["signature"] = _hmac_sha256(
        _secret(), f"{solved['challenge']}{expired_timestamp}"
    )
    assert verify_solution(solved) is False


def test_missing_timestamp_fails():
    """A payload missing the `timestamp` field entirely must be rejected,
    not raise — this covers a pre-fix client/replay payload shaped like
    the old (pre-expiry) protocol."""
    ch = create_challenge(difficulty=2000)
    solved = _solve(ch)
    del solved["timestamp"]
    assert verify_solution(solved) is False
