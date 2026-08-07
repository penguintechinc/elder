"""Unit tests for the Altcha proof-of-work challenge/verifier service.

Covers the standard Altcha protocol round-trip (solve a low-difficulty
challenge, verify the solution) and rejection of a tampered solution.
"""

import hashlib
import os

os.environ.setdefault("CAPTCHA_SECRET", "test-secret")
from apps.api.modules.helpdesk.services.altcha import create_challenge, verify_solution  # noqa: E402


def _solve(ch):
    for n in range(ch["maxnumber"] + 1):
        if hashlib.sha256(f"{ch['salt']}{n}".encode()).hexdigest() == ch["challenge"]:
            return {
                "algorithm": ch["algorithm"],
                "challenge": ch["challenge"],
                "number": n,
                "salt": ch["salt"],
                "signature": ch["signature"],
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
