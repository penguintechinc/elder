"""Darwin AI code review step for the Flows invoker (slice 3).

Evaluates a stage's ``iceflows_stage_reviews`` configuration against the diff
introduced by the promotion merge. The reviewer is an external Darwin service
configured via ``DARWIN_API_URL`` / ``DARWIN_API_TOKEN``.

FAIL-CLOSED CONTRACT: when a stage review is marked ``is_required`` and the
reviewer is unconfigured, unreachable, or errors, the review does NOT pass —
a required security/quality gate must never be silently skipped. Optional
reviews degrade gracefully to a recorded skip.

AI gating: Darwin review is an AI feature (Pro tier). Deployments without an
entitled Darwin endpoint simply leave ``DARWIN_API_URL`` unset; required
reviews then block promotion until the stage config is changed or the
entitlement is provisioned.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Issue severities that block when block_on_critical is set.
_CRITICAL_SEVERITIES = frozenset({"critical", "error", "security"})

DEFAULT_REVIEW_TIMEOUT = 120


@dataclass(slots=True)
class ReviewOutcome:
    """Result of a Darwin review attempt."""

    available: bool
    passed: bool
    score: int | None = None
    issues: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def darwin_configured() -> bool:
    """Whether a Darwin reviewer endpoint is configured for this deployment."""
    return bool(os.environ.get("DARWIN_API_URL"))


def run_darwin_review(
    diff: str,
    *,
    review_type: str = "standard",
    min_score: int = 70,
    block_on_critical: bool = True,
    timeout: int = DEFAULT_REVIEW_TIMEOUT,
) -> ReviewOutcome:
    """Submit a diff to the Darwin reviewer and evaluate the gate policy.

    Never raises — all failures resolve to ``available=False`` (the caller
    applies the fail-closed/graceful-skip policy based on ``is_required``).
    """
    base_url = os.environ.get("DARWIN_API_URL")
    if not base_url:
        return ReviewOutcome(
            available=False, passed=False, error="Darwin reviewer not configured"
        )

    try:
        import httpx

        headers = {"Content-Type": "application/json"}
        token = os.environ.get("DARWIN_API_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = httpx.post(
            f"{base_url.rstrip('/')}/api/v1/review",
            json={"diff": diff, "review_type": review_type},
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:  # noqa: BLE001 - fail closed, never crash the run
        logger.warning("darwin_review_unavailable", extra={"error": str(e)})
        return ReviewOutcome(available=False, passed=False, error=str(e))

    score = body.get("score")
    issues = body.get("issues") or []
    if not isinstance(issues, list):
        issues = []

    passed = True
    if isinstance(score, int) and score < min_score:
        passed = False
    if block_on_critical and any(
        str(i.get("severity", "")).lower() in _CRITICAL_SEVERITIES
        for i in issues
        if isinstance(i, dict)
    ):
        passed = False

    return ReviewOutcome(
        available=True,
        passed=passed,
        score=score if isinstance(score, int) else None,
        issues=[i for i in issues if isinstance(i, dict)][:100],
    )
