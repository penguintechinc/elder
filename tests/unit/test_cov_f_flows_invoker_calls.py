"""Behavioural tests for flows-invoker stage calls and the Darwin review gate."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.flows_invoker import calls, review
from apps.flows_invoker.calls import CallOutcome, dispatch_stage_call
from apps.flows_invoker.review import run_darwin_review
from tests.unit._cov_f_helpers import HttpxResponse, fake_secret

TENANT = 7


class _Expr:
    """A DAL query expression: a table plus a row predicate."""

    def __init__(self, table: "_Table", predicate: Any):
        """Store the target table and predicate."""
        self.table = table
        self.predicate = predicate

    def __and__(self, other: "_Expr") -> "_Expr":
        """Combine two predicates on the same table."""
        return _Expr(
            self.table, lambda row: self.predicate(row) and other.predicate(row)
        )


class _Field:
    """A DAL field supporting ``==`` comparisons."""

    __hash__ = None  # type: ignore[assignment]

    def __init__(self, table: "_Table", name: str):
        """Store the owning table and column name."""
        self.table = table
        self.name = name

    def __eq__(self, value: Any) -> _Expr:  # type: ignore[override]
        """Build an equality predicate."""
        return _Expr(self.table, lambda row: row.get(self.name) == value)


class _Table:
    """An in-memory DAL table."""

    def __init__(self) -> None:
        """Start empty."""
        self.rows: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> _Field:
        """Return a field accessor for any column name."""
        return _Field(self, name)

    def insert(self, **values: Any) -> None:
        """Append a row."""
        self.rows.append(dict(values))


class _Set:
    """The rows matched by an expression."""

    def __init__(self, expr: _Expr):
        """Store the expression."""
        self.expr = expr

    def _matches(self) -> list[dict[str, Any]]:
        """Return matching rows."""
        return [row for row in self.expr.table.rows if self.expr.predicate(row)]

    def select(self) -> Any:
        """Return an object whose ``first`` yields the first match as a record."""
        rows = self._matches()
        return SimpleNamespace(
            first=lambda: SimpleNamespace(**rows[0]) if rows else None
        )

    def update(self, **values: Any) -> int:
        """Update matching rows in place."""
        rows = self._matches()
        for row in rows:
            row.update(values)
        return len(rows)


class _DB:
    """A minimal penguin-dal stand-in with the two tables stage calls touch."""

    def __init__(self) -> None:
        """Create the tables and a commit counter."""
        self.stream_playbooks = _Table()
        self.stream_executions = _Table()
        self.commits = 0

    def __call__(self, expr: _Expr) -> _Set:
        """Return the set of rows matching an expression."""
        return _Set(expr)

    def commit(self) -> None:
        """Count commits."""
        self.commits += 1


def _call(**overrides: Any) -> SimpleNamespace:
    """Build a configured stage call row."""
    values = {
        "call_id": "call-1",
        "call_type": "icestreams",
        "target_id": "5",
        "input_template": {"env": "prod"},
        "is_blocking": False,
        "retry_count": 0,
        "timeout_seconds": 30,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def db() -> _DB:
    """Return a database holding one playbook owned by the test tenant."""
    database = _DB()
    database.stream_playbooks.insert(id=5, tenant_id=TENANT)
    database.stream_playbooks.insert(id=6, tenant_id=99)
    return database


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[dict[str, Any], int]]:
    """Capture enqueued playbook jobs instead of touching Redis."""
    jobs: list[tuple[dict[str, Any], int]] = []
    monkeypatch.setattr(
        calls,
        "_enqueue_streams_job",
        lambda payload, tenant: jobs.append((payload, tenant)),
    )
    return jobs


class TestDispatch:
    """Stage call dispatch rules."""

    def test_non_blocking_call_creates_execution_and_enqueues(
        self, db: _DB, enqueued: list
    ) -> None:
        """A dispatch records a pending, tenant-scoped execution and enqueues its job."""
        outcome = dispatch_stage_call(
            db, TENANT, _call(), {"started_by_identity_id": 3, "promotion_id": 1}
        )
        (row,) = db.stream_executions.rows
        assert (outcome.dispatched, outcome.passed, outcome.attempts) == (True, True, 1)
        assert outcome.execution_id == row["execution_id"]
        assert (row["tenant_id"], row["playbook_id"], row["status"]) == (
            TENANT,
            5,
            "pending",
        )
        assert row["triggered_by_identity_id"] == 3
        assert row["input_json"]["env"] == "prod"
        assert row["input_json"]["_flows"]["promotion_id"] == 1
        assert enqueued == [
            (
                {
                    "execution_id": row["execution_id"],
                    "playbook_id": 5,
                    "tenant_id": TENANT,
                },
                TENANT,
            )
        ]
        assert db.commits == 1

    def test_other_tenants_playbook_is_not_reachable(
        self, db: _DB, enqueued: list
    ) -> None:
        """A playbook owned by another tenant is reported as not found."""
        outcome = dispatch_stage_call(db, TENANT, _call(target_id="6"), {})
        assert (outcome.dispatched, outcome.error) == (
            False,
            "target playbook not found",
        )
        assert db.stream_executions.rows == [] and enqueued == []

    @pytest.mark.parametrize("target", ["abc", None])
    def test_invalid_target_is_rejected(
        self, db: _DB, enqueued: list, target: Any
    ) -> None:
        """A non-numeric target id fails without touching the database."""
        outcome = dispatch_stage_call(db, TENANT, _call(target_id=target), {})
        assert outcome.dispatched is False and "invalid target_id" in outcome.error
        assert db.stream_executions.rows == []

    @pytest.mark.parametrize(
        ("call_type", "message"),
        [
            ("IceRuns", "runs (FaaS) module is not available"),
            ("webhook", "unknown call_type 'webhook'"),
            (None, "unknown call_type None"),
        ],
    )
    def test_unsupported_call_types_fail_loudly(
        self, db: _DB, enqueued: list, call_type: Any, message: str
    ) -> None:
        """Unavailable or unknown call types are refused, never silently skipped."""
        outcome = dispatch_stage_call(db, TENANT, _call(call_type=call_type), {})
        assert outcome.dispatched is False and outcome.passed is False
        assert message in outcome.error
        assert enqueued == []

    def test_enqueue_failure_marks_execution_failed_and_retries(
        self, db: _DB, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Redis failure marks the row failed and is retried per retry_count."""
        attempts: list[int] = []

        def failing(payload: dict[str, Any], tenant: int) -> None:
            """Fail every enqueue."""
            attempts.append(tenant)
            raise ConnectionError("redis down")

        monkeypatch.setattr(calls, "_enqueue_streams_job", failing)
        outcome = dispatch_stage_call(db, TENANT, _call(retry_count=2), {})
        assert len(attempts) == 3 and outcome.attempts == 3
        assert outcome.passed is False
        assert outcome.error == "enqueue failed: redis down"
        assert [row["status"] for row in db.stream_executions.rows] == ["failed"] * 3
        assert "redis down" in db.stream_executions.rows[0]["error_message"]

    def test_negative_retry_count_means_single_attempt(
        self, db: _DB, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A nonsensical negative retry count still makes exactly one attempt."""
        monkeypatch.setattr(
            calls, "_enqueue_streams_job", MagicMock(side_effect=RuntimeError("x"))
        )
        outcome = dispatch_stage_call(db, TENANT, _call(retry_count=-3), {})
        assert outcome.attempts == 1 and len(db.stream_executions.rows) == 1


class TestBlockingWait:
    """Blocking calls poll the execution to a terminal state."""

    @pytest.fixture(autouse=True)
    def _clock(self, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
        """Drive the poll loop with a fake clock that advances on each sleep."""
        clock = SimpleNamespace(now=0.0, sleeps=[])

        def sleep(seconds: float) -> None:
            """Advance the fake clock."""
            clock.sleeps.append(seconds)
            clock.now += seconds

        monkeypatch.setattr(
            calls, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep)
        )
        return clock

    @pytest.mark.parametrize(
        ("status", "passed", "error"),
        [
            ("completed", True, None),
            ("success", True, None),
            ("failed", False, "playbook execution failed"),
            ("cancelled", False, "playbook execution cancelled"),
            ("timeout", False, "playbook execution timeout"),
        ],
    )
    def test_terminal_status_decides_outcome(
        self, db: _DB, enqueued: list, status: str, passed: bool, error: str | None
    ) -> None:
        """A blocking call passes or fails according to the playbook's final status."""

        def finish(payload: dict[str, Any], _tenant: int) -> None:
            """Simulate the streams worker finishing the execution immediately."""
            db(db.stream_executions.execution_id == payload["execution_id"]).update(
                status=status
            )

        with patch.object(calls, "_enqueue_streams_job", finish):
            outcome = dispatch_stage_call(db, TENANT, _call(is_blocking=True), {})
        assert (outcome.passed, outcome.final_status, outcome.error) == (
            passed,
            status,
            error,
        )
        assert outcome.dispatched is True

    def test_times_out_when_execution_never_finishes(
        self, db: _DB, enqueued: list, _clock: SimpleNamespace
    ) -> None:
        """A playbook stuck in pending fails the call after the configured timeout."""
        outcome = dispatch_stage_call(
            db, TENANT, _call(is_blocking=True, timeout_seconds=5), {}
        )
        assert outcome.passed is False and outcome.final_status == "pending"
        assert outcome.error == "timed out after 5s waiting for playbook"
        assert _clock.sleeps == [2.0, 2.0, 2.0]

    def test_missing_execution_row_times_out(self, db: _DB) -> None:
        """Polling an execution that does not exist ends in a timeout, not a crash."""
        outcome = calls._wait_for_streams_result(db, "no-such-execution", 0)
        assert (outcome.passed, outcome.final_status) == (False, None)
        assert "timed out after 0s" in outcome.error

    def test_blocking_failure_is_retried_until_success(
        self, db: _DB, _clock: SimpleNamespace
    ) -> None:
        """A failed blocking attempt is retried and the passing attempt is returned."""
        statuses = iter(["failed", "completed"])

        def finish(payload: dict[str, Any], _tenant: int) -> None:
            """Fail the first execution and complete the second."""
            db(db.stream_executions.execution_id == payload["execution_id"]).update(
                status=next(statuses)
            )

        with patch.object(calls, "_enqueue_streams_job", finish):
            outcome = dispatch_stage_call(
                db, TENANT, _call(is_blocking=True, retry_count=3), {}
            )
        assert (outcome.passed, outcome.attempts) == (True, 2)
        assert len(db.stream_executions.rows) == 2


class TestEnqueue:
    """Redis job-bus bridge."""

    def test_enqueue_uses_idempotency_key_and_closes_redis(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The job is enqueued tenant-scoped with its execution id as idempotency key."""
        from apps.worker.config.settings import settings

        monkeypatch.setattr(settings, "redis_url", "redis://cache.invalid/0")
        redis_client = AsyncMock()
        bus = AsyncMock()
        with (
            patch("redis.asyncio.from_url", return_value=redis_client) as from_url,
            patch("shared.jobbus.JobBus", return_value=bus),
        ):
            calls._enqueue_streams_job(
                {"execution_id": "ex-1", "playbook_id": 5}, TENANT
            )

        from_url.assert_called_once_with("redis://cache.invalid/0")
        bus.ensure_group.assert_awaited_once_with("streams")
        args, kwargs = bus.enqueue.call_args
        assert args == (
            "streams",
            "execute_playbook",
            {"execution_id": "ex-1", "playbook_id": 5},
        )
        assert kwargs["tenant_id"] == TENANT
        assert kwargs["idempotency_key"] == "ex-1"
        redis_client.aclose.assert_awaited_once()

    def test_enqueue_closes_redis_even_when_bus_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A job-bus failure propagates but the Redis connection is still closed."""
        from apps.worker.config.settings import settings

        monkeypatch.setattr(settings, "redis_url", "redis://cache.invalid/0")
        redis_client = AsyncMock()
        bus = AsyncMock()
        bus.enqueue.side_effect = ConnectionError("redis down")
        with (
            patch("redis.asyncio.from_url", return_value=redis_client),
            patch("shared.jobbus.JobBus", return_value=bus),
            pytest.raises(ConnectionError),
        ):
            calls._enqueue_streams_job({"execution_id": "ex-1"}, TENANT)
        redis_client.aclose.assert_awaited_once()

    def test_enqueue_requires_redis_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without a Redis URL the enqueue fails loudly."""
        from apps.worker.config.settings import settings

        monkeypatch.setattr(settings, "redis_url", "")
        with pytest.raises(RuntimeError, match="REDIS_URL not configured"):
            calls._enqueue_streams_job({}, TENANT)

    def test_call_outcome_defaults(self) -> None:
        """A bare outcome starts with one attempt and an empty detail mapping."""
        outcome = CallOutcome(dispatched=False, passed=False)
        assert (outcome.attempts, outcome.detail, outcome.execution_id) == (1, {}, None)


class TestDarwinReview:
    """AI review gate."""

    @pytest.fixture(autouse=True)
    def _configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Point the reviewer at a fake Darwin endpoint without a token."""
        monkeypatch.setenv("DARWIN_API_URL", "https://darwin.test.invalid/")
        monkeypatch.delenv("DARWIN_API_TOKEN", raising=False)

    def test_unconfigured_reviewer_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without an endpoint the review is unavailable and no request is made."""
        monkeypatch.delenv("DARWIN_API_URL")
        assert review.darwin_configured() is False
        with patch("httpx.post") as post:
            outcome = run_darwin_review("diff")
        post.assert_not_called()
        assert (outcome.available, outcome.passed) == (False, False)
        assert outcome.error == "Darwin reviewer not configured"

    def test_request_shape_and_token_header(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The diff is posted with a bearer token that never appears in the outcome."""
        token = fake_secret("darwin", "token")
        monkeypatch.setenv("DARWIN_API_TOKEN", token)
        assert review.darwin_configured() is True
        with patch("httpx.post", return_value=HttpxResponse({"score": 91})) as post:
            outcome = run_darwin_review(
                "--- a\n+++ b", review_type="security", timeout=9
            )
        assert post.call_args.args == ("https://darwin.test.invalid/api/v1/review",)
        kwargs = post.call_args.kwargs
        assert kwargs["json"] == {"diff": "--- a\n+++ b", "review_type": "security"}
        assert kwargs["headers"]["Authorization"] == f"Bearer {token}"
        assert kwargs["timeout"] == 9
        assert (outcome.available, outcome.passed, outcome.score) == (True, True, 91)
        assert token not in repr(outcome) and token not in caplog.text

    @pytest.mark.parametrize(
        ("body", "kwargs", "passed", "score", "issue_count"),
        [
            ({"score": 69}, {}, False, 69, 0),
            ({"score": 70}, {}, True, 70, 0),
            ({"score": 50}, {"min_score": 40}, True, 50, 0),
            ({"score": 99, "issues": [{"severity": "Critical"}]}, {}, False, 99, 1),
            ({"score": 99, "issues": [{"severity": "security"}]}, {}, False, 99, 1),
            (
                {"score": 99, "issues": [{"severity": "critical"}]},
                {"block_on_critical": False},
                True,
                99,
                1,
            ),
            ({"score": 99, "issues": [{"severity": "minor"}, "junk"]}, {}, True, 99, 1),
            ({"score": "high", "issues": "not-a-list"}, {}, True, None, 0),
            ({}, {}, True, None, 0),
        ],
    )
    def test_gate_policy(
        self,
        body: dict[str, Any],
        kwargs: dict[str, Any],
        passed: bool,
        score: int | None,
        issue_count: int,
    ) -> None:
        """Score thresholds and critical findings decide the gate; junk is ignored."""
        with patch("httpx.post", return_value=HttpxResponse(body)):
            outcome = run_darwin_review("diff", **kwargs)
        assert outcome.available is True
        assert (outcome.passed, outcome.score, len(outcome.issues)) == (
            passed,
            score,
            issue_count,
        )

    def test_issue_list_is_capped(self) -> None:
        """At most 100 issues are retained from a noisy review."""
        issues = [{"severity": "minor", "n": n} for n in range(250)]
        with patch(
            "httpx.post", return_value=HttpxResponse({"score": 80, "issues": issues})
        ):
            outcome = run_darwin_review("diff")
        assert len(outcome.issues) == 100 and outcome.issues[-1]["n"] == 99

    @pytest.mark.parametrize(
        "failure",
        [
            HttpxResponse({}, 401),
            HttpxResponse({}, 429),
            HttpxResponse({}, 503),
            HttpxResponse(ValueError("not json")),
            TimeoutError("timed out"),
        ],
        ids=["unauthorized", "rate-limited", "unavailable", "malformed", "timeout"],
    )
    def test_reviewer_failures_fail_closed_without_raising(self, failure: Any) -> None:
        """Any reviewer failure yields available=False so the caller can fail closed."""
        side_effect = failure if isinstance(failure, Exception) else None
        with patch("httpx.post", return_value=failure, side_effect=side_effect):
            outcome = run_darwin_review("diff")
        assert (outcome.available, outcome.passed) == (False, False)
        assert outcome.error
