"""Tests for the shared ReDoS guard (apps/worker/streams/nodes/regex_guard.py).

Regression coverage: comparisons.py's RegexMatch node, if_then.py's and
filter.py's "regex" operator all ran user-supplied regex directly on the
worker's single asyncio event loop with no bound -- a pathological pattern
against adversarial input could backtrack exponentially and hang that loop
forever (worker runs replicas: 1, so this stalls every other in-flight job,
not just the offending node).

NOTE: this file deliberately never exercises a *real* catastrophic-
backtracking match (e.g. `(a|a)*$` against 20+ 'a's can already run
essentially forever in CPython's backtracking `re` engine). Doing so inside
a test would leak an unkillable thread-pool worker for the rest of the test
process. The nested-quantifier cases below are rejected by the static
check *before* any matching happens; the timeout path is exercised by
monkeypatching the match function to a short, self-terminating sleep.
"""

from __future__ import annotations

import time

import pytest

from apps.worker.streams.nodes import regex_guard
from apps.worker.streams.nodes.regex_guard import UnsafeRegexError, safe_regex_search


class TestValidMatches:
    """Ordinary patterns behave exactly like re.search."""

    @pytest.mark.asyncio
    async def test_match_found(self) -> None:
        assert await safe_regex_search(r"^\d+$", "12345") is True

    @pytest.mark.asyncio
    async def test_match_not_found(self) -> None:
        assert await safe_regex_search(r"^\d+$", "abc123") is False

    @pytest.mark.asyncio
    async def test_search_not_anchored(self) -> None:
        assert await safe_regex_search(r"error", "some error occurred") is True


class TestSizeBounds:
    """Pattern/input length caps reject before any matching is attempted."""

    @pytest.mark.asyncio
    async def test_oversized_pattern_rejected(self) -> None:
        pattern = "a" * (regex_guard.MAX_PATTERN_LENGTH + 1)
        with pytest.raises(UnsafeRegexError, match="pattern rejected"):
            await safe_regex_search(pattern, "text")

    @pytest.mark.asyncio
    async def test_pattern_at_max_length_is_allowed(self) -> None:
        pattern = "a" * regex_guard.MAX_PATTERN_LENGTH
        # Should not raise UnsafeRegexError for length; may or may not match.
        await safe_regex_search(pattern, "text")

    @pytest.mark.asyncio
    async def test_oversized_input_rejected(self) -> None:
        text = "a" * (regex_guard.MAX_INPUT_LENGTH + 1)
        with pytest.raises(UnsafeRegexError, match="input rejected"):
            await safe_regex_search(r"a+", text)


class TestComplexityGuard:
    """Nested-quantifier shapes are rejected before matching -- no exponential work ever runs."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "pattern",
        [
            r"(a+)+$",
            r"(a*)*$",
            r"(a+)*$",
            r"(.*)+$",
            r"(\w+)+$",
        ],
    )
    async def test_nested_quantifier_patterns_rejected(self, pattern: str) -> None:
        with pytest.raises(UnsafeRegexError, match="nested quantifier"):
            await safe_regex_search(pattern, "irrelevant")

    @pytest.mark.asyncio
    async def test_non_nested_quantifier_pattern_is_allowed(self) -> None:
        """A single, non-nested quantifier is never flagged."""
        assert await safe_regex_search(r"a+b*c?", "aaabbbc") is True


class TestInvalidPattern:
    """Patterns that don't compile surface as re.error, not a hang."""

    @pytest.mark.asyncio
    async def test_unbalanced_parens_raises_re_error(self) -> None:
        import re

        with pytest.raises(re.error):
            await safe_regex_search(r"(unclosed", "text")


class TestTimeoutPath:
    """A match that overruns the wall-clock budget fails the node, not the loop.

    The underlying re.search call is replaced with a short, self-terminating
    sleep -- this proves asyncio.wait_for's timeout actually fires and
    control returns to the caller promptly, without ever running a genuine
    unbounded-backtracking match (see module docstring).
    """

    @pytest.mark.asyncio
    async def test_slow_match_times_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _slow_search(pattern: str, text: str) -> bool:
            time.sleep(0.3)
            return True

        monkeypatch.setattr(regex_guard, "_bounded_search", _slow_search)
        monkeypatch.setattr(regex_guard, "MATCH_TIMEOUT_S", 0.05)

        start = time.monotonic()
        with pytest.raises(TimeoutError):
            await safe_regex_search(r"a+", "aaa")
        elapsed = time.monotonic() - start

        # The caller must be freed close to the configured timeout, not
        # after the full 0.3s the underlying "match" actually takes.
        assert elapsed < 0.2

    @pytest.mark.asyncio
    async def test_fast_match_does_not_time_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(regex_guard, "MATCH_TIMEOUT_S", 0.05)
        assert await safe_regex_search(r"\d+", "42") is True
