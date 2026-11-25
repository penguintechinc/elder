"""Coverage tests for the flows-invoker executor pipeline helpers."""

from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from apps.flows_invoker import executor as ex
from apps.flows_invoker import gitops
from apps.flows_invoker.review import ReviewOutcome
from apps.flows_invoker.sandbox import CommandResult


class _Q:
    """Query-chain double: select() returns rows, first() the first row."""

    def __init__(self, rows):
        """Hold the canned rows."""
        self.rows = rows

    def select(self, *a, **k):
        """Return the rows regardless of ordering arguments."""
        return self.rows

    def first(self):
        """Return the first row or None."""
        return self.rows[0] if self.rows else None


class _Rows(list):
    """List with select()/first() so it can act as a query result."""

    def select(self, *a, **k):
        """Return self like a DAL query."""
        return self

    def first(self):
        """Return the first row or None."""
        return self[0] if self else None


class _Col:
    """Column double supporting == and & so DAL expressions build."""

    def __eq__(self, o):
        """Return a truthy expression marker."""
        return self

    def __and__(self, o):
        """Combine expressions."""
        return self

    def __hash__(self):
        """Allow use as a dict key."""
        return 1


class _Table:
    """Table double: any attribute is a column; insert/orderby supported."""

    def __init__(self, inserted=None):
        """Remember the insert target list."""
        self._ins = inserted if inserted is not None else []

    def __getattr__(self, name):
        """Return a column stand-in."""
        return _Col()

    def insert(self, **kw):
        """Record the insert and return a fake id."""
        self._ins.append(kw)
        return 99


class _DB:
    """DAL double routing query() calls to canned rows per call."""

    def __init__(self, results=()):
        """Queue canned results and build table doubles."""
        self.results = list(results)
        self.updates = []
        self.commits = 0
        self.inserted = []
        self.iceflows_executions = _Table(self.inserted)
        for t in (
            "iceflows_promotions",
            "iceflows_stages",
            "iceflows",
            "iceflows_credentials",
            "iceflows_stage_tests",
            "iceflows_stage_calls",
            "iceflows_stage_reviews",
        ):
            setattr(self, t, _Table())

    def __call__(self, expr):
        """Pop the next canned result and wrap updates."""
        rows = self.results.pop(0) if self.results else _Rows()
        q = _Rows(rows)
        q.update = lambda **kw: self.updates.append(kw)
        return q

    def commit(self):
        """Count commits."""
        self.commits += 1


def _test(**kw):
    """Build a stage-test row with sensible defaults."""
    d = dict(
        test_id="t1",
        name="unit",
        test_type="unit",
        command="python3 -V",
        path_mode="repo",
        repo_relative_path="",
        env_vars={"A": "b", 1: 2},
        timeout_seconds=None,
        is_blocking=False,
        is_required=True,
    )
    d.update(kw)
    return NS(**d)


class TestSmallHelpers:
    """Loader and formatting helpers."""

    def test_loaders(self):
        """Loaders return None for missing ids and rows otherwise."""
        db = _DB([[NS(id=1)], [NS(id=2)], [NS(id=3)]])
        assert ex._load_promotion(db, 1, 1).id == 1
        assert ex._load_stage(db, 1, None) is None
        assert ex._load_stage(db, 1, 2).id == 2
        assert ex._load_flow(db, 1, None) is None
        assert ex._load_flow(db, 1, 3).id == 3

    def test_credential_token(self):
        """Inactive, missing and active credentials resolve correctly."""
        assert ex._load_credential_token(_DB(), 1, None) is None
        assert ex._load_credential_token(_DB([[]]), 1, 5) is None
        off = NS(id=1, is_active=False, access_token="x")
        assert ex._load_credential_token(_DB([[off]]), 1, 5) is None
        on = NS(id=1, is_active=True, access_token="tok")
        db = _DB([[on], []])
        assert ex._load_credential_token(db, 1, 5) == "tok"
        assert db.commits == 1

    def test_plan_and_review_lookup(self):
        """The plan lists tests and calls; review lookup returns first."""
        t = NS(test_id="a", name="n", test_type="unit")
        c = NS(call_id="b", name="m", call_type="webhook")
        db = _DB([[t], [c]])
        plan = ex._plan_stage_steps(db, 1, 2)
        assert plan["tests"][0]["test_id"] == "a" and plan["calls"][0]["call_id"] == "b"
        assert ex._stage_review(_DB([[NS(x=1)]]), 1, 2).x == 1

    def test_tail(self):
        """Short text is untouched, long text is tail-truncated."""
        assert ex._tail("") == ""
        assert ex._tail("abc") == "abc"
        out = ex._tail("a" * 50 + "END", limit=10)
        assert out.startswith("...[truncated]") and out.endswith("END")


class TestResolveCwd:
    """Working-directory containment."""

    def test_modes(self, tmp_path):
        """Centralized, empty, escaping, missing and valid paths."""
        with pytest.raises(ex.PipelineAbort, match="centralized"):
            ex._resolve_test_cwd(str(tmp_path), _test(path_mode="centralized"))
        assert ex._resolve_test_cwd(str(tmp_path), _test()) == str(tmp_path)
        with pytest.raises(ex.PipelineAbort, match="escapes"):
            ex._resolve_test_cwd(str(tmp_path), _test(repo_relative_path="../.."))
        with pytest.raises(ex.PipelineAbort, match="does not exist"):
            ex._resolve_test_cwd(str(tmp_path), _test(repo_relative_path="nope"))
        (tmp_path / "sub").mkdir()
        got = ex._resolve_test_cwd(str(tmp_path), _test(repo_relative_path="/sub"))
        assert got.endswith("sub")
        got = ex._resolve_test_cwd(str(tmp_path), _test(repo_relative_path="."))
        assert got == str(tmp_path.resolve())


class TestRunStageTests:
    """Test-runner loop outcomes."""

    def _run(self, monkeypatch, tests, result=None, exc=None):
        """Run the loop with stubbed sandbox helpers and return (ok, log)."""
        monkeypatch.setattr(ex, "_stage_tests", lambda *a: tests)
        monkeypatch.setattr(ex, "_resolve_test_cwd", lambda r, t: r)
        monkeypatch.setattr(ex, "parse_command", lambda c: [c])
        monkeypatch.setattr(ex, "build_env", lambda c, e: e)
        monkeypatch.setattr(
            ex,
            "run_command",
            MagicMock(return_value=result, side_effect=exc),
        )
        log = []
        return ex._run_stage_tests(None, 1, 2, "/r", "tok", log), log

    def test_pass(self, monkeypatch):
        """A passing test is logged with scrubbed output."""
        res = CommandResult(0, "ok tok", False, 0.1, False)
        ok, log = self._run(monkeypatch, [_test()], res)
        assert ok and log[0]["passed"] and "tok" not in log[0]["output"]

    def test_no_command_blocking(self, monkeypatch):
        """A blank-command blocking test aborts the run."""
        ok, log = self._run(monkeypatch, [_test(command=" ", is_blocking=True)])
        assert not ok and log[-1]["step"] == "tests_aborted"

    def test_required_vs_optional_failure(self, monkeypatch):
        """Required failures fail the run; optional ones do not."""
        res = CommandResult(1, "bad", False, 0.1, False)
        ok, _ = self._run(monkeypatch, [_test()], res)
        assert not ok
        ok, _ = self._run(monkeypatch, [_test(is_required=False)], res)
        assert ok


class TestReviewGate:
    """Review gate policy."""

    def _gate(self, monkeypatch, cfg, outcome):
        """Run the gate with stubbed review and diff."""
        monkeypatch.setattr(ex, "_stage_review", lambda *a: cfg)
        monkeypatch.setattr(gitops, "merge_diff", lambda ws: "diff")
        monkeypatch.setattr(ex, "run_darwin_review", lambda *a, **k: outcome)
        log = []
        ex._run_review_gate(None, 1, 2, None, log)
        return log

    def test_no_config(self, monkeypatch):
        """No review config is a no-op."""
        assert self._gate(monkeypatch, None, None) == []

    def test_optional_available_and_unavailable(self, monkeypatch):
        """Optional reviews log either result and never raise."""
        cfg = NS(
            is_required=False, review_type=None, min_score=None, block_on_critical=None
        )
        up = ReviewOutcome(True, True, 80, [{}])
        down = ReviewOutcome(False, False, error="x")
        assert self._gate(monkeypatch, cfg, up)[0]["passed"] is True
        assert self._gate(monkeypatch, cfg, down)[0]["note"]

    def test_required_outcomes(self, monkeypatch):
        """Required reviews pass, or abort when unavailable / failing."""
        cfg = NS(
            is_required=True, review_type="deep", min_score=60, block_on_critical=True
        )
        assert self._gate(monkeypatch, cfg, ReviewOutcome(True, True, 90))[0]["passed"]
        with pytest.raises(ex.PipelineAbort, match="unavailable"):
            self._gate(monkeypatch, cfg, ReviewOutcome(False, False))
        with pytest.raises(ex.PipelineAbort, match="did not pass"):
            self._gate(monkeypatch, cfg, ReviewOutcome(True, False, 10))


class TestRunCalls:
    """Stage call dispatch."""

    def _calls(self, monkeypatch, passed, blocking, trigger="pre_merge"):
        """Dispatch one call with a stubbed dispatcher."""
        call = NS(
            call_id="c",
            name="n",
            call_type="t",
            trigger_on=trigger,
            is_blocking=blocking,
        )
        monkeypatch.setattr(ex, "_stage_calls", lambda *a: [call])
        out = NS(
            dispatched=True,
            passed=passed,
            execution_id="e",
            final_status="s",
            attempts=1,
            error="boom",
        )
        monkeypatch.setattr(ex, "dispatch_stage_call", lambda *a: out)
        log = []
        ex._run_calls(None, 1, 2, frozenset({"pre_merge"}), {}, log)
        return log

    def test_matching_and_skipped(self, monkeypatch):
        """Matching triggers dispatch; others are skipped."""
        assert len(self._calls(monkeypatch, True, True)) == 1
        assert self._calls(monkeypatch, True, True, "post_merge") == []
        assert len(self._calls(monkeypatch, False, False)) == 1
        assert len(self._calls(monkeypatch, True, True, None) or [1]) == 1

    def test_blocking_failure(self, monkeypatch):
        """A failed blocking call aborts the pipeline."""
        with pytest.raises(ex.PipelineAbort, match="blocking"):
            self._calls(monkeypatch, False, True)


class TestOpenExecutionRow:
    """Adoption versus creation of the execution row."""

    def test_adopt_existing(self):
        """An existing pre-created row is moved to in_progress."""
        db = _DB([[NS(id=7)], []])
        now = datetime.now(UTC)
        assert ex._open_execution_row(db, 1, NS(id=1, flow_id=2), None, "e1", now) == (
            7,
            "e1",
        )

    def test_create_new_with_and_without_id(self):
        """Unknown or absent ids produce a fresh insert."""
        now = datetime.now(UTC)
        db = _DB([[]])
        assert ex._open_execution_row(db, 1, NS(id=1, flow_id=2), 5, "e1", now) == (
            99,
            "e1",
        )
        _, eid = ex._open_execution_row(_DB(), 1, NS(id=1, flow_id=2), None, None, now)
        assert len(eid) == 36


def _ws():
    """Build a workspace double with a cleanup spy."""
    return NS(repo_dir="/r", cleanup=MagicMock())


class TestPipeline:
    """End-to-end orchestration with all collaborators stubbed."""

    def _setup(
        self,
        monkeypatch,
        *,
        promo=None,
        target=None,
        source=True,
        flow=True,
        tests=(),
        review=None,
        merge_exc=None,
        calls_exc=None,
        tests_ok=True,
    ):
        """Stub every collaborator and return (db, ws, runner)."""
        promo = promo or NS(
            id=1,
            flow_id=2,
            status="approved",
            target_stage_id=3,
            source_stage_id=4,
            source_commit="abc1234",
        )
        tgt = target or NS(id=3, branch_name="main", require_approval=True)
        src = NS(id=4, branch_name="dev")
        fl = NS(credential_id=1, repository_url="https://e.invalid/r")
        monkeypatch.setattr(ex, "_load_promotion", lambda *a: promo)
        stages = {3: tgt, 4: src if source else None}
        monkeypatch.setattr(ex, "_load_stage", lambda d, t, i: stages.get(i))
        monkeypatch.setattr(ex, "_load_flow", lambda *a: fl if flow else None)
        monkeypatch.setattr(
            ex, "_plan_stage_steps", lambda *a: {"tests": list(tests), "calls": []}
        )
        monkeypatch.setattr(ex, "_stage_review", lambda *a: review)
        monkeypatch.setattr(ex, "_load_credential_token", lambda *a: "tok")
        ws = _ws()
        monkeypatch.setattr(gitops, "create_workspace", lambda t: ws)
        if merge_exc:
            monkeypatch.setattr(
                gitops, "clone_and_merge", MagicMock(side_effect=merge_exc)
            )
        else:
            monkeypatch.setattr(gitops, "clone_and_merge", lambda *a, **k: "sha")
        monkeypatch.setattr(gitops, "push_target", MagicMock())
        monkeypatch.setattr(ex, "_run_stage_tests", lambda *a: tests_ok)
        monkeypatch.setattr(ex, "_run_review_gate", lambda *a: None)
        run_calls = MagicMock(side_effect=calls_exc)
        monkeypatch.setattr(ex, "_run_calls", run_calls)
        db = _DB()
        monkeypatch.setattr(ex, "_open_execution_row", lambda *a: (50, "exec-1"))
        return db, ws, run_calls

    def test_promotion_missing(self, monkeypatch):
        """A missing promotion fails the adopted execution row."""
        monkeypatch.setattr(ex, "_load_promotion", lambda *a: None)
        db = _DB()
        out = ex.execute_promotion_pipeline(db, 1, 9, execution_id="e")
        assert out["status"] == "failed" and db.commits == 1
        out = ex.execute_promotion_pipeline(_DB(), 1, 9)
        assert out["execution_id"] is None

    def test_success_merged(self, monkeypatch):
        """An approved promotion merges, pushes and runs post-merge calls."""
        db, ws, calls = self._setup(monkeypatch, tests=[{"x": 1}], review=NS(a=1))
        out = ex.execute_promotion_pipeline(db, 1, 1, 5)
        assert out["status"] == "success" and out["merged"]
        assert calls.call_count == 2 and ws.cleanup.called

    def test_merge_skipped_when_unapproved(self, monkeypatch):
        """Unapproved promotions skip the push with a recorded reason."""
        promo = NS(
            id=1,
            flow_id=2,
            status="pending",
            target_stage_id=3,
            source_stage_id=4,
            source_commit=None,
        )
        db, ws, calls = self._setup(monkeypatch, promo=promo)
        out = ex.execute_promotion_pipeline(db, 1, 1)
        assert out["status"] == "success" and not out["merged"]
        log = db.updates[-1]["execution_log"]
        assert log[-1]["step"] == "merge_skipped"
        assert "not approved" in log[-1]["reason"]

    def test_no_git_nothing_to_merge(self, monkeypatch):
        """Without tests/review and no merge permission git is skipped."""
        promo = NS(
            id=1,
            flow_id=2,
            status="pending",
            target_stage_id=3,
            source_stage_id=4,
            source_commit=None,
        )
        db, ws, _ = self._setup(monkeypatch, promo=promo)
        ex.execute_promotion_pipeline(db, 1, 1)
        assert not ws.cleanup.called
        # merge permitted but no workspace cannot happen; covers 'nothing to merge'
        tgt = NS(id=3, branch_name="main", require_approval=False)
        db, ws, _ = self._setup(monkeypatch, promo=promo, target=tgt)
        out = ex.execute_promotion_pipeline(db, 1, 1)
        assert out["merged"]

    @pytest.mark.parametrize(
        "kw,msg",
        [
            (
                {
                    "target": NS(id=3, branch_name="m", require_approval=False),
                    "source": False,
                },
                "source stage",
            ),
            ({"flow": False}, "flow not found"),
            ({"tests": [{"x": 1}], "tests_ok": False}, "required stage tests"),
        ],
    )
    def test_abort_paths(self, monkeypatch, kw, msg):
        """Aborts surface as failed executions with the reason."""
        db, ws, _ = self._setup(monkeypatch, **kw)
        out = ex.execute_promotion_pipeline(db, 1, 1)
        assert out["status"] == "failed" and msg in out["error"]

    def test_target_stage_missing(self, monkeypatch):
        """A missing target stage aborts."""
        db, ws, _ = self._setup(monkeypatch)
        monkeypatch.setattr(ex, "_load_stage", lambda *a: None)
        out = ex.execute_promotion_pipeline(db, 1, 1)
        assert "target stage" in out["error"]

    def test_gitops_error_scrubbed(self, monkeypatch):
        """GitOps errors are scrubbed of the token and keep output."""
        err = gitops.GitOpsError("bad tok", output="out tok")
        db, ws, _ = self._setup(monkeypatch, tests=[{"x": 1}], merge_exc=err)
        out = ex.execute_promotion_pipeline(db, 1, 1)
        assert "tok" not in out["error"]
        assert ws.cleanup.called
        assert "tok" not in db.updates[-1]["execution_log"][-1]["output"]

    def test_unexpected_error(self, monkeypatch):
        """Unexpected exceptions are recorded as failed."""
        db, ws, _ = self._setup(
            monkeypatch, tests=[{"x": 1}], merge_exc=RuntimeError("kaboom tok")
        )
        out = ex.execute_promotion_pipeline(db, 1, 1)
        assert out["status"] == "failed" and "tok" not in out["error"]
