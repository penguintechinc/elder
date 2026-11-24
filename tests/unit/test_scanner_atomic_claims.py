"""Atomic-claim tests for SBOM scans and discovery scanner jobs.

svc-scanner runs as a multi-replica Deployment plus a remote fleet. Before
this suite's fix, ``/start`` for both job kinds was read-then-write, so every
replica that listed a pending job could start it and the same scan ran N
times. These tests race 20 claimers against real Postgres and require each
job to be claimed exactly once, a dead claimant's job to be re-queued after
its lease, and duplicate completions to be harmless.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from apps.api.modules.sbom.routes import sbom_scans
from apps.worker.discovery import service as discovery_service
from apps.worker.discovery.service import (
    DiscoveryJobNotClaimableError,
    DiscoveryService,
)

CLAIMERS = 20
JOBS = 5


def _unique_parent_id() -> int:
    """Parent id no other test uses, so component counts are isolated."""
    return 1_000_000 + (uuid4().int % 1_000_000_000)


def _sbom_scan(db, status: str = "pending", started_at=None, tenant_id: int = 1) -> int:
    """Insert an SBOM scan row and return its id."""
    scan_id = db.sbom_scans.insert(
        tenant_id=tenant_id,
        parent_type="service",
        parent_id=_unique_parent_id(),
        scan_type="git_clone",
        status=status,
        started_at=started_at,
        components_found=0,
        components_added=0,
        components_updated=0,
        components_removed=0,
        created_at=datetime.now(UTC),
    )
    db.commit()
    return scan_id


def _discovery_job(db, schedule_interval: int = 0, next_run_at=None) -> int:
    """Insert an enabled local-provider discovery job owned by tenant 1."""
    now = datetime.now(UTC)
    org_id = db.organizations.insert(
        name=f"claim-org-{uuid4().hex[:8]}", tenant_id=1, created_at=now, updated_at=now
    )
    job_id = db.discovery_jobs.insert(
        name=f"claim-job-{uuid4().hex[:8]}",
        provider="network",
        config_json={"_organization_id": org_id},
        schedule_interval=schedule_interval,
        enabled=True,
        next_run_at=next_run_at,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return job_id


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


@pytest.fixture
def sbom_auth(generate_token):
    """Authorization header for a tenant-1 scanner caller."""
    with patch("apps.api.auth.decorators.get_current_user") as mock_user:
        mock_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["sbom:read", "sbom:write"])
        yield {"Authorization": f"Bearer {token}"}


class TestLeaseConfig:
    """Lease env parsing can never crash the scanner endpoints."""

    @pytest.mark.parametrize("raw", ["", "abc", "0", "-10", "59", "1.5"])
    def test_invalid_values_fall_back_to_default(self, monkeypatch, raw: str) -> None:
        """Garbage or too-small leases use the 900s default."""
        monkeypatch.setenv("ELDER_SBOM_CLAIM_LEASE_SECONDS", raw)
        monkeypatch.setenv("ELDER_DISCOVERY_CLAIM_LEASE_SECONDS", raw)
        assert sbom_scans._claim_lease_seconds() == 900
        assert discovery_service._claim_lease_seconds() == 900

    def test_valid_override(self, monkeypatch) -> None:
        """A sane value is honoured."""
        monkeypatch.setenv("ELDER_SBOM_CLAIM_LEASE_SECONDS", "1800")
        monkeypatch.setenv("ELDER_DISCOVERY_CLAIM_LEASE_SECONDS", "120")
        assert sbom_scans._claim_lease_seconds() == 1800
        assert discovery_service._claim_lease_seconds() == 120


class TestSbomAtomicClaim:
    """SBOM scans: claim exactly once, recover stale claims, idempotent results."""

    def test_twenty_concurrent_claimers_claim_each_scan_exactly_once(self, app) -> None:
        """20 threads x 5 scans against Postgres: one winner per scan."""
        db = app.db
        scan_ids = [_sbom_scan(db) for _ in range(JOBS)]

        results = _race(lambda sid: sbom_scans._claim_scan(db, sid, 1), scan_ids)

        for scan_id in scan_ids:
            outcome = results[scan_id]
            assert len(outcome) == CLAIMERS
            assert outcome.count(True) == 1, (scan_id, outcome)
            assert outcome.count(False) == CLAIMERS - 1
            assert db.sbom_scans[scan_id].status == "running"

    def test_claim_is_tenant_scoped(self, app) -> None:
        """A claim under the wrong tenant id never takes the scan."""
        db = app.db
        scan_id = _sbom_scan(db)
        assert sbom_scans._claim_scan(db, scan_id, 987_654) is False
        assert db.sbom_scans[scan_id].status == "pending"

    @pytest.mark.asyncio
    async def test_concurrent_start_requests_one_winner(
        self, app, async_client, sbom_auth
    ) -> None:
        """20 concurrent POST /start: exactly one 200, the rest 400."""
        scan_id = _sbom_scan(app.db)

        responses = await asyncio.gather(
            *(
                async_client.post(
                    f"/api/v1/sbom/scans/{scan_id}/start", headers=sbom_auth
                )
                for _ in range(CLAIMERS)
            )
        )

        codes = sorted(r.status_code for r in responses)
        assert codes == [200] + [400] * (CLAIMERS - 1)

    @pytest.mark.asyncio
    async def test_pending_list_hides_claimed_and_requeues_stale(
        self, app, async_client, sbom_auth
    ) -> None:
        """A live claim is hidden; a claim past its lease is listed again."""
        db = app.db
        now = datetime.now(UTC)
        fresh = _sbom_scan(db)
        live = _sbom_scan(db, status="running", started_at=now - timedelta(seconds=60))
        stale = _sbom_scan(db, status="running", started_at=now - timedelta(hours=2))

        resp = await async_client.get("/api/v1/sbom/scans/pending", headers=sbom_auth)
        assert resp.status_code == 200, (await resp.get_data()).decode()
        listed = {s["id"] for s in await resp.get_json()}

        assert fresh in listed
        assert stale in listed
        assert live not in listed
        assert db.sbom_scans[stale].status == "pending"
        assert db.sbom_scans[stale].started_at is None
        assert db.sbom_scans[live].status == "running"

    @pytest.mark.asyncio
    async def test_stale_claim_can_be_started_by_another_scanner(
        self, app, async_client, sbom_auth
    ) -> None:
        """/start on a scan abandoned past its lease succeeds; a live one 400s."""
        db = app.db
        now = datetime.now(UTC)
        stale = _sbom_scan(db, status="running", started_at=now - timedelta(hours=2))
        live = _sbom_scan(db, status="running", started_at=now)

        ok = await async_client.post(
            f"/api/v1/sbom/scans/{stale}/start", headers=sbom_auth
        )
        assert ok.status_code == 200, (await ok.get_data()).decode()
        restarted = db.sbom_scans[stale]
        assert restarted.status == "running"
        assert restarted.started_at > now - timedelta(seconds=30)

        busy = await async_client.post(
            f"/api/v1/sbom/scans/{live}/start", headers=sbom_auth
        )
        assert busy.status_code == 400

    @pytest.mark.asyncio
    async def test_duplicate_completion_is_ignored(
        self, app, async_client, sbom_auth
    ) -> None:
        """A second /results neither rewrites the scan nor its components."""
        db = app.db
        scan_id = _sbom_scan(db)
        scan = db.sbom_scans[scan_id]
        url = f"/api/v1/sbom/scans/{scan_id}"
        assert (
            await async_client.post(f"{url}/start", headers=sbom_auth)
        ).status_code == 200

        first = await async_client.post(
            f"{url}/results",
            headers=sbom_auth,
            json={
                "success": True,
                "components": [
                    {"name": "left-pad", "version": "1.0.0", "package_type": "npm"}
                ],
                "commit_hash": "abc123",
            },
        )
        assert first.status_code == 200, (await first.get_data()).decode()
        completed_at = db.sbom_scans[scan_id].completed_at

        replay = await async_client.post(
            f"{url}/results",
            headers=sbom_auth,
            json={
                "success": False,
                "error_message": "late duplicate",
                "components": [
                    {"name": "evil", "version": "9.9.9", "package_type": "npm"}
                ],
            },
        )
        assert replay.status_code == 200, (await replay.get_data()).decode()

        after = db.sbom_scans[scan_id]
        assert after.status == "completed"
        assert after.commit_hash == "abc123"
        assert after.error_message is None
        assert after.completed_at == completed_at
        components = db(
            (db.sbom_components.parent_type == scan.parent_type)
            & (db.sbom_components.parent_id == scan.parent_id)
        ).select()
        assert [c.name for c in components] == ["left-pad"]

        # A completed scan can never be claimed again.
        again = await async_client.post(f"{url}/start", headers=sbom_auth)
        assert again.status_code == 400

    @pytest.mark.asyncio
    async def test_concurrent_completions_apply_once(
        self, app, async_client, sbom_auth
    ) -> None:
        """10 racing /results calls insert the component set exactly once."""
        db = app.db
        scan_id = _sbom_scan(db, status="running", started_at=datetime.now(UTC))
        scan = db.sbom_scans[scan_id]
        payload = {
            "success": True,
            "components": [
                {"name": f"pkg-{i}", "version": "1.0.0", "package_type": "npm"}
                for i in range(5)
            ],
        }

        responses = await asyncio.gather(
            *(
                async_client.post(
                    f"/api/v1/sbom/scans/{scan_id}/results",
                    headers=sbom_auth,
                    json=payload,
                )
                for _ in range(10)
            )
        )

        assert {r.status_code for r in responses} == {200}
        count = db(
            (db.sbom_components.parent_type == scan.parent_type)
            & (db.sbom_components.parent_id == scan.parent_id)
        ).count()
        assert count == 5
        assert db.sbom_scans[scan_id].status == "completed"


class TestDiscoveryAtomicClaim:
    """Discovery scanner jobs: claim exactly once, lease recovery, idempotency."""

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

    def test_claimed_job_leaves_pending_list(self, app) -> None:
        """Once claimed, neither job kind is handed to another caller."""
        db = app.db
        service = DiscoveryService(db)
        one_time = _discovery_job(db)
        scheduled = _discovery_job(db, schedule_interval=3600)

        pending = {j["id"] for j in service.get_pending_jobs()}
        assert {one_time, scheduled} <= pending

        service.mark_job_running(one_time)
        service.mark_job_running(scheduled)

        pending = {j["id"] for j in service.get_pending_jobs()}
        assert not {one_time, scheduled} & pending

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

    @pytest.mark.parametrize("schedule_interval", [0, 3600])
    def test_stale_claim_is_requeued(self, app, schedule_interval: int) -> None:
        """A claim past its lease returns to pending and can be re-claimed."""
        db = app.db
        service = DiscoveryService(db)
        job_id = _discovery_job(db, schedule_interval)
        service.mark_job_running(job_id)
        lease_until = db.discovery_jobs[job_id].next_run_at
        assert lease_until > datetime.now(UTC) + timedelta(seconds=800)

        # Lease still held: not pending, not claimable.
        assert job_id not in {j["id"] for j in service.get_pending_jobs()}
        with pytest.raises(DiscoveryJobNotClaimableError):
            service.mark_job_running(job_id)

        # The claimant dies: its lease deadline passes.
        db(db.discovery_jobs.id == job_id).update(
            next_run_at=datetime.now(UTC) - timedelta(seconds=1)
        )
        db.commit()

        assert job_id in {j["id"] for j in service.get_pending_jobs()}
        assert service.mark_job_running(job_id)["success"] is True

        failed = _history(db, job_id, "failed")
        assert len(failed) == 1
        assert "lease expired" in failed[0].error_message
        assert len(_history(db, job_id, "running")) == 1

    def test_duplicate_completion_is_ignored(self, app) -> None:
        """A replayed completion changes neither results nor the schedule."""
        db = app.db
        service = DiscoveryService(db)
        job_id = _discovery_job(db, schedule_interval=3600)
        service.mark_job_running(job_id)

        first = service.complete_job(job_id, True, {"hosts": 3})
        assert (first["status"], first["duplicate"]) == ("completed", False)
        next_run = db.discovery_jobs[job_id].next_run_at
        assert next_run > datetime.now(UTC) + timedelta(seconds=3500)

        replay = service.complete_job(job_id, False, {"hosts": 0}, "late duplicate")
        assert replay["success"] is True
        assert replay["duplicate"] is True

        (row,) = _history(db, job_id)
        assert row.status == "completed"
        assert row.results_json == {"hosts": 3}
        assert row.error_message is None
        assert db.discovery_jobs[job_id].next_run_at == next_run

    def test_one_time_job_completion_never_requeues(self, app) -> None:
        """Completing a one-time job clears its lease so it is not re-run."""
        db = app.db
        service = DiscoveryService(db)
        job_id = _discovery_job(db)
        service.mark_job_running(job_id)
        service.complete_job(job_id, True, {})

        job = db.discovery_jobs[job_id]
        assert job.last_run_at is not None
        assert job.next_run_at is None
        assert service.requeue_stale_claims(job_id) == 0
        assert job_id not in {j["id"] for j in service.get_pending_jobs()}
        with pytest.raises(DiscoveryJobNotClaimableError):
            service.mark_job_running(job_id)

    def test_late_completion_after_requeue_is_ignored(self, app) -> None:
        """A claimant that lost its lease cannot complete the re-queued job."""
        db = app.db
        service = DiscoveryService(db)
        job_id = _discovery_job(db)
        service.mark_job_running(job_id)
        db(db.discovery_jobs.id == job_id).update(
            next_run_at=datetime.now(UTC) - timedelta(seconds=1)
        )
        db.commit()
        assert service.requeue_stale_claims(job_id) == 1

        late = service.complete_job(job_id, True, {"hosts": 99})

        assert late["duplicate"] is True
        (row,) = _history(db, job_id)
        assert row.status == "failed"
        assert row.results_json is None
        assert job_id in {j["id"] for j in service.get_pending_jobs()}

    def test_concurrent_completions_apply_once(self, app) -> None:
        """20 racing completions: exactly one is applied."""
        db = app.db
        service = DiscoveryService(db)
        job_id = _discovery_job(db)
        service.mark_job_running(job_id)

        results = _race(
            lambda jid: service.complete_job(jid, True, {"ok": 1}), [job_id]
        )

        applied = [r for r in results[job_id] if r["duplicate"] is False]
        assert len(applied) == 1
        assert len(results[job_id]) == CLAIMERS
        assert len(_history(db, job_id, "completed")) == 1

    @pytest.mark.asyncio
    async def test_losing_scanner_gets_404_not_500(
        self, app, async_client, generate_token
    ) -> None:
        """Over HTTP the loser is told to skip (404), never a 500."""
        job_id = _discovery_job(app.db)
        with patch("apps.api.auth.decorators.get_current_user") as mock_user:
            mock_user.return_value = MagicMock(id=1, is_superuser=False)
            token = generate_token(tenant_id=1, scopes=["discovery:write"])
            headers = {"Authorization": f"Bearer {token}"}
            url = f"/api/v1/discovery/jobs/{job_id}/start"

            first = await async_client.post(url, headers=headers)
            second = await async_client.post(url, headers=headers)

        assert first.status_code == 200, (await first.get_data()).decode()
        assert second.status_code == 404, (await second.get_data()).decode()
        assert len(_history(app.db, job_id)) == 1
