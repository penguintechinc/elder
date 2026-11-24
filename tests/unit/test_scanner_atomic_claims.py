"""Atomic-claim tests for SBOM scans and discovery scanner jobs.

svc-scanner runs as a multi-replica Deployment plus a remote fleet. These
tests race 20 claimers against real Postgres and require each job to be
claimed exactly once, and cover the two properties a security review added:

* the claim lease lives in dedicated columns, so only a job that is actually
  running can be re-queued -- a completed/failed job never is, whatever its
  ``last_run_at``/``next_run_at`` say;
* completion is bound to the claimant by an unguessable claim token, so a
  stranger or a superseded claimant cannot complete someone else's job.
"""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import sqlalchemy as sa

from alembic.migration import MigrationContext
from alembic.operations import Operations
from apps.api.modules.sbom.routes import sbom_scans
from apps.worker.discovery import service as discovery_service
from apps.worker.discovery.service import (
    DiscoveryClaimTokenError,
    DiscoveryJobNotClaimableError,
    DiscoveryService,
)
from shared import claim_tokens

CLAIMERS = 20
JOBS = 5
COMPONENT = {"name": "left-pad", "version": "1.0.0", "package_type": "npm"}


def _unique_parent_id() -> int:
    """Parent id no other test uses, so component counts are isolated."""
    return 1_000_000 + (uuid4().int % 1_000_000_000)


def _sbom_scan(db, status: str = "pending", **fields) -> int:
    """Insert an SBOM scan row (tenant 1) and return its id."""
    scan_id = db.sbom_scans.insert(
        tenant_id=1,
        parent_type="service",
        parent_id=_unique_parent_id(),
        scan_type="git_clone",
        status=status,
        components_found=0,
        components_added=0,
        components_updated=0,
        components_removed=0,
        created_at=datetime.now(UTC),
        **fields,
    )
    db.commit()
    return scan_id


def _discovery_job(
    db, schedule_interval: int = 0, provider: str = "network", **fields
) -> int:
    """Insert an enabled discovery job owned by tenant 1."""
    now = datetime.now(UTC)
    org_id = db.organizations.insert(
        name=f"claim-org-{uuid4().hex[:8]}", tenant_id=1, created_at=now, updated_at=now
    )
    job_id = db.discovery_jobs.insert(
        name=f"claim-job-{uuid4().hex[:8]}",
        provider=provider,
        config_json={"_organization_id": org_id},
        schedule_interval=schedule_interval,
        enabled=True,
        created_at=now,
        updated_at=now,
        **fields,
    )
    db.commit()
    return job_id


def _expire_claim(db, job_id: int) -> None:
    """Simulate a dead claimant: push the job's lease deadline into the past."""
    db(db.discovery_jobs.id == job_id).update(
        claim_expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    db.commit()


def _race(fn, targets: list[int]) -> dict[int, list]:
    """Run ``fn(target)`` from CLAIMERS threads per target, released together.

    Returns each target's list of results (an exception instance when the
    call raised) so callers can count winners per job.
    """
    barrier = threading.Barrier(CLAIMERS)

    def worker(_: int) -> list[tuple[int, object]]:
        barrier.wait(timeout=30)
        out: list[tuple[int, object]] = []
        for target in targets:
            try:
                out.append((target, fn(target)))
            except Exception as exc:  # collected and asserted on by the caller
                out.append((target, exc))
        return out

    results: dict[int, list] = {t: [] for t in targets}
    with ThreadPoolExecutor(max_workers=CLAIMERS) as pool:
        for per_thread in pool.map(worker, range(CLAIMERS)):
            for target, value in per_thread:
                results[target].append(value)
    return results


def _history(db, job_id: int, status: str | None = None) -> list:
    """Return a job's discovery_history rows, optionally filtered by status."""
    query = db.discovery_history.job_id == job_id
    if status:
        query &= db.discovery_history.status == status
    return list(db(query).select())


def _pending_ids(service: DiscoveryService) -> set[int]:
    """Ids currently offered to scanners."""
    return {j["id"] for j in service.get_pending_jobs()}


def _components(db, scan_id: int) -> list[str]:
    """Names of the components stored for a scan's parent."""
    scan = db.sbom_scans[scan_id]
    rows = db(
        (db.sbom_components.parent_type == scan.parent_type)
        & (db.sbom_components.parent_id == scan.parent_id)
    ).select()
    return sorted(c.name for c in rows)


@pytest.fixture
def scanner_auth(generate_token):
    """Authorization header for a tenant-1 scanner caller."""
    with patch("apps.api.auth.decorators.get_current_user") as mock_user:
        mock_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(
            tenant_id=1,
            scopes=["sbom:read", "sbom:write", "discovery:read", "discovery:write"],
        )
        yield {"Authorization": f"Bearer {token}"}


class TestClaimConfig:
    """Lease/token env parsing can never crash or silently fail open."""

    @pytest.mark.parametrize("raw", ["", "abc", "0", "-10", "59", "1.5"])
    def test_invalid_lease_falls_back_to_default(self, monkeypatch, raw: str) -> None:
        """Garbage or too-small leases use the 900s default."""
        monkeypatch.setenv("ELDER_SBOM_CLAIM_LEASE_SECONDS", raw)
        monkeypatch.setenv("ELDER_DISCOVERY_CLAIM_LEASE_SECONDS", raw)
        assert sbom_scans._claim_lease_seconds() == 900
        assert discovery_service._claim_lease_seconds() == 900

    def test_valid_lease_override(self, monkeypatch) -> None:
        """A sane value is honoured."""
        monkeypatch.setenv("ELDER_SBOM_CLAIM_LEASE_SECONDS", "1800")
        monkeypatch.setenv("ELDER_DISCOVERY_CLAIM_LEASE_SECONDS", "120")
        assert sbom_scans._claim_lease_seconds() == 1800
        assert discovery_service._claim_lease_seconds() == 120

    @pytest.mark.parametrize("raw", [None, "", "true", "1", "yes", "garbage", "nope"])
    def test_token_enforcement_defaults_on(self, monkeypatch, raw) -> None:
        """Unset or unrecognised values keep enforcement on (fail closed)."""
        if raw is None:
            monkeypatch.delenv(claim_tokens.CLAIM_TOKEN_REQUIRED_ENV, raising=False)
        else:
            monkeypatch.setenv(claim_tokens.CLAIM_TOKEN_REQUIRED_ENV, raw)
        assert claim_tokens.claim_token_required() is True

    @pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", " off "])
    def test_token_enforcement_explicit_opt_out(self, monkeypatch, raw: str) -> None:
        """Only an explicit false value turns enforcement off."""
        monkeypatch.setenv(claim_tokens.CLAIM_TOKEN_REQUIRED_ENV, raw)
        assert claim_tokens.claim_token_required() is False

    def test_tokens_are_random_and_stored_hashed(self) -> None:
        """Tokens are high-entropy, unique, and only their digest is stored."""
        pairs = [claim_tokens.new_claim_token() for _ in range(200)]
        assert len({t for t, _ in pairs}) == 200
        for token, digest in pairs:
            assert len(token) >= 43
            assert digest == claim_tokens.hash_claim_token(token)
            assert len(digest) == 64 and token not in digest

    @pytest.mark.parametrize("raw", [None, "", "   ", 123, ["x"], "x" * 257])
    def test_unusable_tokens_normalise_to_none(self, raw) -> None:
        """Blank, non-string and oversized inputs count as 'no token'."""
        assert claim_tokens.normalize_claim_token(raw) is None


class TestSbomAtomicClaim:
    """SBOM scans: claim once, recover only stale running claims, bound results."""

    def test_twenty_concurrent_claimers_claim_each_scan_exactly_once(self, app) -> None:
        """20 threads x 5 scans against Postgres: one winner per scan."""
        db = app.db
        scan_ids = [_sbom_scan(db) for _ in range(JOBS)]

        results = _race(lambda sid: sbom_scans._claim_scan(db, sid, 1), scan_ids)

        for scan_id in scan_ids:
            outcome = results[scan_id]
            assert len(outcome) == CLAIMERS
            tokens = [o for o in outcome if isinstance(o, str)]
            assert len(tokens) == 1, (scan_id, outcome)
            assert outcome.count(None) == CLAIMERS - 1
            row = db.sbom_scans[scan_id]
            assert row.status == "running"
            assert row.claim_token_hash == claim_tokens.hash_claim_token(tokens[0])
            assert row.claim_expires_at > datetime.now(UTC) + timedelta(seconds=800)

    def test_claim_is_tenant_scoped(self, app) -> None:
        """A claim under the wrong tenant id never takes the scan."""
        db = app.db
        scan_id = _sbom_scan(db)
        assert sbom_scans._claim_scan(db, scan_id, 987_654) is None
        assert db.sbom_scans[scan_id].status == "pending"

    @pytest.mark.asyncio
    async def test_concurrent_start_requests_one_winner(
        self, app, async_client, scanner_auth
    ) -> None:
        """20 concurrent POST /start: exactly one 200 (with a token), rest 400."""
        scan_id = _sbom_scan(app.db)

        responses = await asyncio.gather(
            *(
                async_client.post(
                    f"/api/v1/sbom/scans/{scan_id}/start", headers=scanner_auth
                )
                for _ in range(CLAIMERS)
            )
        )

        codes = sorted(r.status_code for r in responses)
        assert codes == [200] + [400] * (CLAIMERS - 1)
        (winner,) = [r for r in responses if r.status_code == 200]
        assert len((await winner.get_json())["claim_token"]) >= 43

    @pytest.mark.asyncio
    async def test_only_stale_running_scans_are_requeued(
        self, app, async_client, scanner_auth
    ) -> None:
        """Expired running claims return to pending; nothing else moves."""
        db = app.db
        now = datetime.now(UTC)
        past, future = now - timedelta(hours=2), now + timedelta(minutes=10)
        fresh = _sbom_scan(db)
        live = _sbom_scan(
            db,
            "running",
            started_at=now,
            claim_token_hash="a" * 64,
            claim_expires_at=future,
        )
        stale = _sbom_scan(
            db,
            "running",
            started_at=past,
            claim_token_hash="b" * 64,
            claim_expires_at=past,
        )
        legacy_live = _sbom_scan(db, "running", started_at=now)
        legacy_stale = _sbom_scan(db, "running", started_at=past)
        # Terminal scans must never be re-queued, even with leftover lease data.
        done = _sbom_scan(
            db,
            "completed",
            started_at=past,
            claim_token_hash="c" * 64,
            claim_expires_at=past,
        )
        failed = _sbom_scan(db, "failed", started_at=past, claim_expires_at=past)

        resp = await async_client.get(
            "/api/v1/sbom/scans/pending", headers=scanner_auth
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()
        body = await resp.get_json()
        listed = {s["id"] for s in body}

        assert {fresh, stale, legacy_stale} <= listed
        assert not {live, legacy_live, done, failed} & listed
        for scan_id in (stale, legacy_stale):
            row = db.sbom_scans[scan_id]
            assert (row.status, row.started_at) == ("pending", None)
            assert (row.claim_token_hash, row.claim_expires_at) == (None, None)
        assert db.sbom_scans[done].status == "completed"
        assert db.sbom_scans[failed].status == "failed"
        assert db.sbom_scans[live].status == "running"
        # Claim internals are never serialised.
        assert not any(k.startswith("claim_") for s in body for k in s)

    @pytest.mark.asyncio
    async def test_results_require_the_claim_token(
        self, app, async_client, scanner_auth
    ) -> None:
        """No token / wrong token -> 409 and nothing written; right token -> 200."""
        db = app.db
        scan_id = _sbom_scan(db)
        url = f"/api/v1/sbom/scans/{scan_id}"
        start = await async_client.post(f"{url}/start", headers=scanner_auth)
        token = (await start.get_json())["claim_token"]
        payload = {"success": True, "components": [COMPONENT]}

        missing = await async_client.post(
            f"{url}/results", headers=scanner_auth, json=payload
        )
        wrong = await async_client.post(
            f"{url}/results",
            headers=scanner_auth,
            json={**payload, "claim_token": claim_tokens.new_claim_token()[0]},
        )
        wrong_header = await async_client.post(
            f"{url}/results",
            headers={**scanner_auth, "X-Claim-Token": "not-the-token"},
            json=payload,
        )

        assert [missing.status_code, wrong.status_code, wrong_header.status_code] == [
            409,
            409,
            409,
        ]
        assert db.sbom_scans[scan_id].status == "running"
        assert _components(db, scan_id) == []

        ok = await async_client.post(
            f"{url}/results",
            headers=scanner_auth,
            json={**payload, "claim_token": token},
        )
        assert ok.status_code == 200, (await ok.get_data()).decode()
        row = db.sbom_scans[scan_id]
        assert row.status == "completed"
        assert (row.claim_token_hash, row.claim_expires_at) == (None, None)
        assert _components(db, scan_id) == ["left-pad"]

    @pytest.mark.asyncio
    async def test_claim_token_accepted_from_header(
        self, app, async_client, scanner_auth
    ) -> None:
        """The token may travel in X-Claim-Token instead of the body."""
        scan_id = _sbom_scan(app.db)
        url = f"/api/v1/sbom/scans/{scan_id}"
        start = await async_client.post(f"{url}/start", headers=scanner_auth)
        token = (await start.get_json())["claim_token"]

        ok = await async_client.post(
            f"{url}/results",
            headers={**scanner_auth, "X-Claim-Token": token},
            json={"success": False, "error_message": "clone failed"},
        )

        assert ok.status_code == 200, (await ok.get_data()).decode()
        assert app.db.sbom_scans[scan_id].status == "failed"

    @pytest.mark.asyncio
    async def test_late_completion_from_expired_claimant_is_rejected(
        self, app, async_client, scanner_auth
    ) -> None:
        """After lease expiry + re-claim, the first claimant's token is dead."""
        db = app.db
        scan_id = _sbom_scan(db)
        url = f"/api/v1/sbom/scans/{scan_id}"
        first = await async_client.post(f"{url}/start", headers=scanner_auth)
        old_token = (await first.get_json())["claim_token"]

        # A live claim cannot be taken over...
        busy = await async_client.post(f"{url}/start", headers=scanner_auth)
        assert busy.status_code == 400
        # ...until the claimant dies and its lease runs out.
        db(db.sbom_scans.id == scan_id).update(
            claim_expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )
        db.commit()
        second = await async_client.post(f"{url}/start", headers=scanner_auth)
        assert second.status_code == 200, (await second.get_data()).decode()
        new_token = (await second.get_json())["claim_token"]
        assert new_token != old_token

        late = await async_client.post(
            f"{url}/results",
            headers=scanner_auth,
            json={
                "success": True,
                "claim_token": old_token,
                "components": [{**COMPONENT, "name": "from-dead-claimant"}],
            },
        )
        assert late.status_code == 409
        assert db.sbom_scans[scan_id].status == "running"
        assert _components(db, scan_id) == []

        ok = await async_client.post(
            f"{url}/results",
            headers=scanner_auth,
            json={"success": True, "claim_token": new_token, "components": [COMPONENT]},
        )
        assert ok.status_code == 200, (await ok.get_data()).decode()
        assert _components(db, scan_id) == ["left-pad"]

    @pytest.mark.asyncio
    async def test_duplicate_completion_is_ignored(
        self, app, async_client, scanner_auth
    ) -> None:
        """A replayed /results neither rewrites the scan nor its components."""
        db = app.db
        scan_id = _sbom_scan(db)
        url = f"/api/v1/sbom/scans/{scan_id}"
        start = await async_client.post(f"{url}/start", headers=scanner_auth)
        token = (await start.get_json())["claim_token"]

        first = await async_client.post(
            f"{url}/results",
            headers=scanner_auth,
            json={
                "success": True,
                "claim_token": token,
                "components": [COMPONENT],
                "commit_hash": "abc123",
            },
        )
        assert first.status_code == 200, (await first.get_data()).decode()
        completed_at = db.sbom_scans[scan_id].completed_at

        replay = await async_client.post(
            f"{url}/results",
            headers=scanner_auth,
            json={
                "success": False,
                "claim_token": token,
                "error_message": "late duplicate",
                "components": [{**COMPONENT, "name": "evil"}],
            },
        )
        assert replay.status_code == 200, (await replay.get_data()).decode()
        assert "claim_token" not in await replay.get_json()

        after = db.sbom_scans[scan_id]
        assert after.status == "completed"
        assert after.commit_hash == "abc123"
        assert after.error_message is None
        assert after.completed_at == completed_at
        assert _components(db, scan_id) == ["left-pad"]

        # A completed scan can never be claimed or re-queued again.
        again = await async_client.post(f"{url}/start", headers=scanner_auth)
        assert again.status_code == 400
        sbom_scans._requeue_stale_scans(db, 1)
        assert db.sbom_scans[scan_id].status == "completed"

    @pytest.mark.asyncio
    async def test_concurrent_completions_apply_once(
        self, app, async_client, scanner_auth
    ) -> None:
        """10 racing /results calls insert the component set exactly once."""
        db = app.db
        scan_id = _sbom_scan(db)
        token = sbom_scans._claim_scan(db, scan_id, 1)
        payload = {
            "success": True,
            "claim_token": token,
            "components": [{**COMPONENT, "name": f"pkg-{i}"} for i in range(5)],
        }

        responses = await asyncio.gather(
            *(
                async_client.post(
                    f"/api/v1/sbom/scans/{scan_id}/results",
                    headers=scanner_auth,
                    json=payload,
                )
                for _ in range(10)
            )
        )

        assert {r.status_code for r in responses} == {200}
        assert len(_components(db, scan_id)) == 5
        assert db.sbom_scans[scan_id].status == "completed"

    @pytest.mark.asyncio
    async def test_transition_flag_accepts_tokenless_legacy_scanner(
        self, app, async_client, scanner_auth, monkeypatch
    ) -> None:
        """With enforcement explicitly off, a pre-token scanner still completes."""
        monkeypatch.setenv(claim_tokens.CLAIM_TOKEN_REQUIRED_ENV, "false")
        scan_id = _sbom_scan(app.db)
        url = f"/api/v1/sbom/scans/{scan_id}"
        await async_client.post(f"{url}/start", headers=scanner_auth)

        ok = await async_client.post(
            f"{url}/results", headers=scanner_auth, json={"success": True}
        )

        assert ok.status_code == 200, (await ok.get_data()).decode()
        assert app.db.sbom_scans[scan_id].status == "completed"

    @pytest.mark.asyncio
    async def test_scan_detail_never_exposes_claim_fields(
        self, app, async_client, scanner_auth
    ) -> None:
        """GET of a claimed scan leaks neither the token nor its hash."""
        scan_id = _sbom_scan(app.db)
        token = sbom_scans._claim_scan(app.db, scan_id, 1)

        resp = await async_client.get(
            f"/api/v1/sbom/scans/{scan_id}", headers=scanner_auth
        )

        assert resp.status_code == 200, (await resp.get_data()).decode()
        text = (await resp.get_data()).decode()
        assert token not in text
        assert claim_tokens.hash_claim_token(token) not in text
        assert "claim_token" not in text


class TestDiscoveryAtomicClaim:
    """Discovery scanner jobs: claim once, lease in own columns, bound completion."""

    @pytest.mark.parametrize("schedule_interval", [0, 3600])
    def test_twenty_concurrent_claimers_claim_each_job_exactly_once(
        self, app, schedule_interval: int
    ) -> None:
        """20 threads x 5 jobs against Postgres: one winner, one history row."""
        db = app.db
        service = DiscoveryService(db)
        job_ids = [_discovery_job(db, schedule_interval) for _ in range(JOBS)]

        results = _race(service.mark_job_running, job_ids)

        for job_id in job_ids:
            outcome = results[job_id]
            assert len(outcome) == CLAIMERS
            winners = [o for o in outcome if isinstance(o, dict)]
            losers = [
                o for o in outcome if isinstance(o, DiscoveryJobNotClaimableError)
            ]
            assert len(winners) == 1, (job_id, outcome)
            assert len(losers) == CLAIMERS - 1, (job_id, outcome)
            assert len(_history(db, job_id)) == 1
            assert len(_history(db, job_id, "running")) == 1
            job = db.discovery_jobs[job_id]
            assert job.claim_token_hash == claim_tokens.hash_claim_token(
                winners[0]["claim_token"]
            )

    def test_claim_does_not_touch_next_run_at(self, app) -> None:
        """The lease lives in claim_expires_at; scheduling state is untouched."""
        db = app.db
        service = DiscoveryService(db)
        due = datetime.now(UTC) - timedelta(minutes=5)
        one_time = _discovery_job(db)
        scheduled = _discovery_job(db, schedule_interval=3600, next_run_at=due)

        service.mark_job_running(one_time)
        service.mark_job_running(scheduled)

        assert db.discovery_jobs[one_time].next_run_at is None
        assert db.discovery_jobs[scheduled].next_run_at == due
        for job_id in (one_time, scheduled):
            lease = db.discovery_jobs[job_id].claim_expires_at
            assert lease > datetime.now(UTC) + timedelta(seconds=800)

    def test_claimed_job_leaves_pending_list_without_leaking_claim(self, app) -> None:
        """Once claimed, neither job kind is offered again; no claim fields out."""
        db = app.db
        service = DiscoveryService(db)
        one_time = _discovery_job(db)
        scheduled = _discovery_job(db, schedule_interval=3600)

        pending = service.get_pending_jobs()
        assert {one_time, scheduled} <= {j["id"] for j in pending}
        assert not any(k.startswith("claim_") for j in pending for k in j)

        service.mark_job_running(one_time)
        service.mark_job_running(scheduled)

        assert not {one_time, scheduled} & _pending_ids(service)
        assert not any(k.startswith("claim_") for k in service.get_job(one_time))

    def test_scheduled_job_not_due_cannot_be_claimed(self, app) -> None:
        """A scheduled job whose next run is in the future is not claimable."""
        db = app.db
        job_id = _discovery_job(
            db,
            schedule_interval=3600,
            next_run_at=datetime.now(UTC) + timedelta(hours=1),
        )
        with pytest.raises(DiscoveryJobNotClaimableError):
            DiscoveryService(db).mark_job_running(job_id)
        assert _history(db, job_id) == []

    def test_missing_job_still_raises_not_found(self, app) -> None:
        """Unknown ids keep the original 'Job not found' error."""
        with pytest.raises(Exception, match="Job not found: 2147480000"):
            DiscoveryService(app.db).mark_job_running(2_147_480_000)

    @pytest.mark.parametrize("success", [True, False])
    def test_finished_one_time_job_is_never_requeued(self, app, success: bool) -> None:
        """regression (security review, HIGH): completed/failed never re-runs.

        Covers immediately after completion and long after the lease would
        have expired, including the run-now path that rewrites next_run_at.
        """
        db = app.db
        service = DiscoveryService(db)
        job_id = _discovery_job(db)
        token = service.mark_job_running(job_id)["claim_token"]
        done = service.complete_job(job_id, success, {"hosts": 1}, claim_token=token)
        assert done["duplicate"] is False

        def assert_not_requeued() -> None:
            assert service.requeue_stale_claims(job_id) == 0
            assert job_id not in _pending_ids(service)
            job = db.discovery_jobs[job_id]
            assert job.last_run_at is not None
            assert (job.claim_token_hash, job.claim_expires_at) == (None, None)
            with pytest.raises(DiscoveryJobNotClaimableError):
                service.mark_job_running(job_id)
            assert len(_history(db, job_id)) == 1

        assert_not_requeued()  # before the lease would have expired

        # Long after: last run hours ago, and next_run_at in the past exactly
        # as the "run now" path (queue_job_for_worker) leaves it.
        past = datetime.now(UTC) - timedelta(hours=3)
        db(db.discovery_jobs.id == job_id).update(last_run_at=past, next_run_at=past)
        db.commit()
        service.queue_job_for_worker(job_id)
        assert db.discovery_jobs[job_id].next_run_at is not None

        assert_not_requeued()
        (row,) = _history(db, job_id)
        assert row.status == ("completed" if success else "failed")

    @pytest.mark.parametrize("provider", ["network", "aws"])
    def test_unclaimed_job_with_past_next_run_at_is_never_requeued(
        self, app, provider: str
    ) -> None:
        """A run-and-finished one-time job with a stale next_run_at stays put.

        This is the exact row shape the old lease-in-next_run_at predicate
        matched: one-time, last_run_at set, next_run_at in the past.
        """
        db = app.db
        service = DiscoveryService(db)
        past = datetime.now(UTC) - timedelta(hours=2)
        job_id = _discovery_job(
            db, provider=provider, last_run_at=past, next_run_at=past
        )

        assert service.requeue_stale_claims(job_id) == 0
        assert job_id not in _pending_ids(service)
        job = db.discovery_jobs[job_id]
        assert (job.last_run_at, job.next_run_at) == (past, past)

    def test_remote_provider_claim_columns_are_ignored_by_requeue(self, app) -> None:
        """Requeue is restricted to scanner providers."""
        db = app.db
        past = datetime.now(UTC) - timedelta(hours=2)
        job_id = _discovery_job(
            db,
            provider="aws",
            last_run_at=past,
            claim_token_hash="d" * 64,
            claim_expires_at=past,
        )
        assert DiscoveryService(db).requeue_stale_claims(job_id) == 0
        assert db.discovery_jobs[job_id].last_run_at == past

    def test_disabled_job_with_expired_claim_is_not_offered(self, app) -> None:
        """A job cancelled (disabled) mid-run is released but never re-offered."""
        db = app.db
        service = DiscoveryService(db)
        job_id = _discovery_job(db)
        service.mark_job_running(job_id)
        db(db.discovery_jobs.id == job_id).update(enabled=False)
        _expire_claim(db, job_id)

        assert job_id not in _pending_ids(service)
        assert len(_history(db, job_id, "running")) == 0

    @pytest.mark.parametrize("schedule_interval", [0, 3600])
    def test_stuck_running_job_is_requeued(self, app, schedule_interval: int) -> None:
        """A genuinely stuck claim (lease expired, never completed) is recovered."""
        db = app.db
        service = DiscoveryService(db)
        job_id = _discovery_job(db, schedule_interval)
        old_token = service.mark_job_running(job_id)["claim_token"]

        # Lease still held: not pending, not claimable, not requeued.
        assert service.requeue_stale_claims(job_id) == 0
        assert job_id not in _pending_ids(service)
        with pytest.raises(DiscoveryJobNotClaimableError):
            service.mark_job_running(job_id)

        _expire_claim(db, job_id)

        assert job_id in _pending_ids(service)
        failed = _history(db, job_id, "failed")
        assert len(failed) == 1
        assert "lease expired" in failed[0].error_message
        new_token = service.mark_job_running(job_id)["claim_token"]
        assert new_token != old_token
        assert len(_history(db, job_id, "running")) == 1

    def test_completion_requires_matching_claim_token(self, app) -> None:
        """Missing or wrong token is rejected and changes nothing."""
        db = app.db
        service = DiscoveryService(db)
        job_id = _discovery_job(db, schedule_interval=3600)
        token = service.mark_job_running(job_id)["claim_token"]

        for bad in (None, "", "wrong-token", claim_tokens.new_claim_token()[0]):
            with pytest.raises(DiscoveryClaimTokenError):
                service.complete_job(job_id, True, {"hosts": 9}, claim_token=bad)

        job = db.discovery_jobs[job_id]
        assert job.claim_token_hash == claim_tokens.hash_claim_token(token)
        assert job.next_run_at is None
        (row,) = _history(db, job_id)
        assert (row.status, row.results_json) == ("running", None)

        done = service.complete_job(job_id, True, {"hosts": 3}, claim_token=token)
        assert (done["status"], done["duplicate"]) == ("completed", False)
        assert db.discovery_jobs[job_id].next_run_at > datetime.now(UTC) + timedelta(
            seconds=3500
        )

    def test_late_completion_from_expired_claimant_after_reclaim_rejected(
        self, app
    ) -> None:
        """The superseded claimant cannot complete the new claimant's run."""
        db = app.db
        service = DiscoveryService(db)
        job_id = _discovery_job(db)
        old_token = service.mark_job_running(job_id)["claim_token"]
        _expire_claim(db, job_id)
        new_token = service.mark_job_running(job_id)["claim_token"]

        with pytest.raises(DiscoveryClaimTokenError):
            service.complete_job(job_id, True, {"hosts": 99}, claim_token=old_token)

        assert len(_history(db, job_id, "running")) == 1
        assert _history(db, job_id, "completed") == []

        done = service.complete_job(job_id, True, {"hosts": 2}, claim_token=new_token)
        assert done["duplicate"] is False
        (completed,) = _history(db, job_id, "completed")
        assert completed.results_json == {"hosts": 2}

    def test_late_completion_after_requeue_is_ignored(self, app) -> None:
        """A claimant that lost its lease cannot complete the re-queued job."""
        db = app.db
        service = DiscoveryService(db)
        job_id = _discovery_job(db)
        token = service.mark_job_running(job_id)["claim_token"]
        _expire_claim(db, job_id)
        assert service.requeue_stale_claims(job_id) == 1

        late = service.complete_job(job_id, True, {"hosts": 99}, claim_token=token)

        assert (late["duplicate"], late["status"]) == (True, "ignored")
        (row,) = _history(db, job_id)
        assert (row.status, row.results_json) == ("failed", None)
        assert job_id in _pending_ids(service)

    def test_duplicate_completion_is_ignored(self, app) -> None:
        """A replayed completion changes neither results nor the schedule."""
        db = app.db
        service = DiscoveryService(db)
        job_id = _discovery_job(db, schedule_interval=3600)
        token = service.mark_job_running(job_id)["claim_token"]

        first = service.complete_job(job_id, True, {"hosts": 3}, claim_token=token)
        assert (first["status"], first["duplicate"]) == ("completed", False)
        next_run = db.discovery_jobs[job_id].next_run_at

        replay = service.complete_job(
            job_id, False, {"hosts": 0}, "late duplicate", claim_token=token
        )
        assert (replay["success"], replay["duplicate"]) == (True, True)

        (row,) = _history(db, job_id)
        assert row.status == "completed"
        assert row.results_json == {"hosts": 3}
        assert row.error_message is None
        assert db.discovery_jobs[job_id].next_run_at == next_run

    def test_concurrent_completions_apply_once(self, app) -> None:
        """20 racing completions with the right token: exactly one is applied."""
        db = app.db
        service = DiscoveryService(db)
        job_id = _discovery_job(db)
        token = service.mark_job_running(job_id)["claim_token"]

        results = _race(
            lambda jid: service.complete_job(jid, True, {"ok": 1}, claim_token=token),
            [job_id],
        )

        applied = [r for r in results[job_id] if r["duplicate"] is False]
        assert len(applied) == 1
        assert len(results[job_id]) == CLAIMERS
        assert len(_history(db, job_id, "completed")) == 1

    def test_transition_flag_accepts_tokenless_legacy_scanner(
        self, app, monkeypatch
    ) -> None:
        """With enforcement explicitly off, a pre-token scanner still completes."""
        monkeypatch.setenv(claim_tokens.CLAIM_TOKEN_REQUIRED_ENV, "false")
        db = app.db
        service = DiscoveryService(db)
        job_id = _discovery_job(db)
        service.mark_job_running(job_id)

        done = service.complete_job(job_id, True, {"hosts": 1})

        assert done["duplicate"] is False
        assert len(_history(db, job_id, "completed")) == 1
        # Even in transition mode a wrong token is still a wrong token.
        other = _discovery_job(db)
        service.mark_job_running(other)
        with pytest.raises(DiscoveryClaimTokenError):
            service.complete_job(other, True, {}, claim_token="wrong-token")

    @pytest.mark.asyncio
    async def test_http_claim_and_bound_completion(
        self, app, async_client, scanner_auth
    ) -> None:
        """Over HTTP: loser 409, tokenless/wrong completion 409, claimant 200."""
        db = app.db
        job_id = _discovery_job(db)
        base = f"/api/v1/discovery/jobs/{job_id}"

        first = await async_client.post(f"{base}/start", headers=scanner_auth)
        second = await async_client.post(f"{base}/start", headers=scanner_auth)
        assert first.status_code == 200, (await first.get_data()).decode()
        assert second.status_code == 409, (await second.get_data()).decode()
        token = (await first.get_json())["claim_token"]
        assert len(_history(db, job_id)) == 1

        body = {"success": True, "results": {"hosts": 4}}
        missing = await async_client.post(
            f"{base}/complete", headers=scanner_auth, json=body
        )
        wrong = await async_client.post(
            f"{base}/complete",
            headers=scanner_auth,
            json={**body, "claim_token": "wrong-token"},
        )
        assert (missing.status_code, wrong.status_code) == (409, 409)
        assert token not in (await wrong.get_data()).decode()
        assert len(_history(db, job_id, "running")) == 1

        ok = await async_client.post(
            f"{base}/complete",
            headers={**scanner_auth, "X-Claim-Token": token},
            json=body,
        )
        assert ok.status_code == 200, (await ok.get_data()).decode()
        assert (await ok.get_json())["status"] == "completed"
        (row,) = _history(db, job_id)
        assert row.results_json == {"hosts": 4}


class TestMigration055ScannerClaimColumns:
    """Migration 055 on real Postgres: up/down/up plus the lease data clean-up."""

    @staticmethod
    def _load():
        path = (
            Path(__file__).resolve().parents[2]
            / "alembic"
            / "versions"
            / "055_scanner_claim_columns.py"
        )
        spec = importlib.util.spec_from_file_location("mig_055", path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @staticmethod
    def _run(conn, fn) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            fn()

    @staticmethod
    def _claim_columns(conn, table: str) -> set[str]:
        cols = {c["name"] for c in sa.inspect(conn).get_columns(table)}
        return cols & {"claim_token_hash", "claim_expires_at"}

    def test_upgrade_downgrade_upgrade_and_lease_cleanup(
        self, test_database_url
    ) -> None:
        """Columns appear/disappear cleanly and stale leases are cleared."""
        mod = self._load()
        assert (mod.revision, mod.down_revision) == ("055", "052")

        schema = f"mig055_{uuid4().hex[:12]}"
        engine = sa.create_engine(test_database_url)
        now = datetime.now(UTC)
        past, future = now - timedelta(hours=2), now + timedelta(minutes=10)
        try:
            with engine.begin() as conn:
                conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
                conn.execute(sa.text(f"SET search_path TO {schema}"))
                conn.execute(
                    sa.text(
                        """
                        CREATE TABLE discovery_jobs (
                            id INTEGER PRIMARY KEY, provider VARCHAR(50) NOT NULL,
                            schedule_interval INTEGER NOT NULL,
                            last_run_at TIMESTAMPTZ, next_run_at TIMESTAMPTZ);
                        CREATE TABLE discovery_history (
                            id SERIAL PRIMARY KEY, job_id INTEGER NOT NULL,
                            status VARCHAR(50) NOT NULL);
                        CREATE TABLE sbom_scans (
                            id INTEGER PRIMARY KEY, status VARCHAR(20) NOT NULL);
                        INSERT INTO sbom_scans VALUES (1, 'completed');
                        """
                    )
                )
                # 1 completed one-time job left with a stale lease in next_run_at
                # 2 failed one-time job, same
                # 3 one-time job still running under an old-style live lease
                # 4 scheduled scanner job (next_run_at is real scheduling state)
                # 5 one-time cloud job queued via run-now (not a scanner job)
                # 6 never-run one-time job
                jobs = [
                    (1, "network", 0, past, past),
                    (2, "banner", 0, past, past),
                    (3, "http_screenshot", 0, now, future),
                    (4, "network", 3600, past, future),
                    (5, "aws", 0, past, past),
                    (6, "network", 0, None, None),
                ]
                for row in jobs:
                    conn.execute(
                        sa.text(
                            "INSERT INTO discovery_jobs VALUES (:i, :p, :s, :l, :n)"
                        ),
                        dict(zip("ipsln", row, strict=True)),
                    )
                conn.execute(
                    sa.text(
                        "INSERT INTO discovery_history (job_id, status) VALUES "
                        "(1, 'completed'), (2, 'failed'), (3, 'running'), (4, 'completed')"
                    )
                )

                self._run(conn, mod.upgrade)

                for table in ("discovery_jobs", "sbom_scans"):
                    assert len(self._claim_columns(conn, table)) == 2, table
                rows = {
                    r.id: r
                    for r in conn.execute(
                        sa.text("SELECT * FROM discovery_jobs ORDER BY id")
                    )
                }
                assert len(rows) == 6
                # Finished one-time jobs: stale lease gone, nothing claimed,
                # last_run_at kept -> can never look stale or pending again.
                for job_id in (1, 2):
                    row = rows[job_id]
                    assert row.next_run_at is None
                    assert (row.claim_token_hash, row.claim_expires_at) == (None, None)
                    assert row.last_run_at == past
                # In-flight job: lease carried into the dedicated column.
                assert rows[3].next_run_at is None
                assert rows[3].claim_expires_at == future
                assert rows[3].claim_token_hash == "migrated-from-next-run-at"
                # Untouched: scheduled, non-scanner, never-run.
                assert (rows[4].next_run_at, rows[4].claim_token_hash) == (future, None)
                assert (rows[5].next_run_at, rows[5].claim_token_hash) == (past, None)
                assert (rows[6].last_run_at, rows[6].next_run_at) == (None, None)

                self._run(conn, mod.upgrade)  # idempotent re-run
                self._run(conn, mod.downgrade)
                for table in ("discovery_jobs", "sbom_scans"):
                    assert self._claim_columns(conn, table) == set(), table
                count = conn.execute(
                    sa.text("SELECT count(*) FROM discovery_jobs")
                ).scalar()
                assert count == 6

                self._run(conn, mod.upgrade)
                for table in ("discovery_jobs", "sbom_scans"):
                    assert len(self._claim_columns(conn, table)) == 2, table
                stale = conn.execute(
                    sa.text(
                        "SELECT count(*) FROM discovery_jobs WHERE id IN (1, 2) "
                        "AND (next_run_at IS NOT NULL OR claim_token_hash IS NOT NULL)"
                    )
                ).scalar()
                assert stale == 0
        finally:
            with engine.begin() as conn:
                conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            engine.dispose()
