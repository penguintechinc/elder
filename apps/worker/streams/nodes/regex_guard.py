"""ReDoS guard shared by conditional/filter stream nodes.

Workflow authors supply arbitrary regex patterns (and text) evaluated on the
worker's single asyncio event loop. The worker runs with replicas: 1, so a
pathological pattern such as `(a+)+$` matched against adversarial input can
backtrack exponentially and hang that loop forever, stalling every other
in-flight job — not just the one node. This module bounds pattern/input
size, statically rejects the classic nested-quantifier catastrophic-
backtracking shape, and runs the actual match in a thread under a
wall-clock timeout so a pattern that slips past the heuristics still fails
the node instead of the process.
"""

from __future__ import annotations

import asyncio
import re

# Generous for real Streams use cases (field values, log lines, short
# strings) while keeping even a moderately bad pattern's backtracking
# blowup bounded by the timeout below.
MAX_PATTERN_LENGTH = 500
MAX_INPUT_LENGTH = 10_000
MATCH_TIMEOUT_S = 1.0

# A group whose own body ends in an unbounded quantifier, itself quantified
# -- e.g. `(a+)+`, `(a*)*`, `(.*)+` -- is the textbook catastrophic-
# backtracking trigger. This is a coarse static check (not a full
# backtracking analysis) that pairs with, not replaces, the runtime timeout.
_NESTED_QUANTIFIER_RE = re.compile(r"\([^()]*[+*]\)[+*]")


class UnsafeRegexError(ValueError):
    """Raised when a pattern/input is rejected before matching is attempted."""


def _check_complexity(pattern: str) -> None:
    """Reject patterns matching a known catastrophic-backtracking shape."""
    if _NESTED_QUANTIFIER_RE.search(pattern):
        raise UnsafeRegexError(
            "regex pattern rejected: nested quantifier "
            f"(catastrophic-backtracking risk): {pattern!r}"
        )


def _bounded_search(pattern: str, text: str) -> bool:
    """Run re.search in a worker thread (invoked via asyncio.to_thread)."""
    return bool(re.search(pattern, text))


async def safe_regex_search(pattern: str, text: str) -> bool:
    """Safely evaluate `re.search(pattern, text)` with ReDoS guards.

    Offloads the actual match to a thread pool and bounds it with a
    wall-clock timeout so a pathological pattern can never block the
    calling event loop -- it can only fail this one node.

    Raises:
        UnsafeRegexError: pattern/input too large, or pattern shape looks
            like a catastrophic-backtracking trigger.
        re.error: pattern doesn't compile.
        TimeoutError: match didn't finish within MATCH_TIMEOUT_S. The
            underlying thread is abandoned (re.search isn't cancellable),
            but the event loop is freed immediately.
    """
    if not isinstance(pattern, str):
        raise TypeError(f"regex pattern must be a string, got {type(pattern).__name__}")
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise UnsafeRegexError(
            f"regex pattern rejected: length {len(pattern)} exceeds max {MAX_PATTERN_LENGTH}"
        )
    if len(text) > MAX_INPUT_LENGTH:
        raise UnsafeRegexError(
            f"regex input rejected: length {len(text)} exceeds max {MAX_INPUT_LENGTH}"
        )

    _check_complexity(pattern)
    re.compile(pattern)  # surface re.error before handing off to a thread

    return await asyncio.wait_for(
        asyncio.to_thread(_bounded_search, pattern, text), timeout=MATCH_TIMEOUT_S
    )
