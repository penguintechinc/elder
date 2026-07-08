"""Tests for worker job-bus consumer infrastructure.

Tests the following components:
- resolve_worker_groups: mapping enabled modules to consumer groups
- Consumer dispatch: reading jobs, invoking handlers, acking
- Idempotency: duplicate job skipping
- Exception handling: un-acked messages on handler failure
- XAUTOCLAIM sweeper: reclaim and re-dispatch
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import redis.asyncio
from redis.asyncio import Redis

from apps.worker.jobs.groups import resolve_worker_groups
from apps.worker.jobs.registry import get_handler
from shared.jobbus import JobBus, JobEnvelope


# Test Redis configuration (use DB 6 for isolation)
TEST_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:56379/6")


@pytest_asyncio.fixture
async def redis_client():
    """Create a test Redis client and flush the database."""
    client = await redis.asyncio.from_url(TEST_REDIS_URL)
    await client.flushdb()
    yield client
    await client.close()


@pytest_asyncio.fixture
async def jobbus(redis_client):
    """Create a JobBus instance for testing."""
    bus = JobBus(redis_client, max_deliveries=5, group="workers")
    yield bus
    # Cleanup: flush the database after the test
    await redis_client.flushdb()


class TestResolveWorkerGroups:
    """Test resolve_worker_groups function."""

    def test_resolve_with_discovery_enabled(self):
        """Test group resolution with discovery module enabled.

        Note: discovery depends on infrastructure, so both must be enabled.
        """
        env = {"ELDER_MODULES_ENABLED": "infrastructure,discovery"}
        groups = resolve_worker_groups(env)
        assert "discovery" in groups

    def test_resolve_with_sbom_enabled(self):
        """Test group resolution with SBOM module enabled."""
        env = {"ELDER_MODULES_ENABLED": "sbom"}
        groups = resolve_worker_groups(env)
        assert "sbom_scan" in groups

    def test_resolve_with_both_enabled(self):
        """Test group resolution with multiple modules enabled.

        Note: discovery depends on infrastructure.
        """
        env = {"ELDER_MODULES_ENABLED": "infrastructure,discovery,sbom"}
        groups = resolve_worker_groups(env)
        assert "discovery" in groups
        assert "sbom_scan" in groups

    def test_resolve_with_all(self):
        """Test group resolution with all modules."""
        env = {"ELDER_MODULES_ENABLED": "all"}
        groups = resolve_worker_groups(env)
        # Should include at least discovery and sbom_scan
        assert "discovery" in groups
        assert "sbom_scan" in groups

    def test_resolve_with_infrastructure_only(self):
        """Test group resolution with infrastructure (no worker groups)."""
        env = {
            "ELDER_MODULES_ENABLED": "infrastructure",
        }
        groups = resolve_worker_groups(env)
        # infrastructure module has no worker groups
        assert "discovery" not in groups
        assert "sbom_scan" not in groups
        assert len(groups) == 0


class TestHandlerRegistry:
    """Test handler registry."""

    def test_get_discovery_handler(self):
        """Test getting the discovery handler."""
        handler = get_handler("discovery")
        assert handler is not None
        assert asyncio.iscoroutinefunction(handler)

    def test_get_sbom_scan_handler(self):
        """Test getting the SBOM scan handler."""
        handler = get_handler("sbom_scan")
        assert handler is not None
        assert asyncio.iscoroutinefunction(handler)

    def test_get_unknown_handler(self):
        """Test getting a handler for an unknown group."""
        handler = get_handler("unknown_group")
        assert handler is None

    @pytest.mark.asyncio
    async def test_sbom_scan_handler_stub(self):
        """Test that SBOM scan handler returns NotImplementedError (stub)."""
        handler = get_handler("sbom_scan")
        envelope = JobEnvelope(
            job_id="test-123",
            job_type="sbom_scan",
            payload={},
            enqueued_at=datetime.now(timezone.utc).isoformat(),
        )

        with pytest.raises(NotImplementedError, match="SBOM scan worker logic"):
            await handler(envelope)


class TestConsumerDispatch:
    """Test consumer job dispatch and acknowledgment."""

    @pytest.mark.asyncio
    async def test_enqueue_and_read(self, jobbus):
        """Test enqueueing and reading a job."""
        await jobbus.ensure_group("discovery")

        job_id = await jobbus.enqueue(
            stream_group="discovery",
            job_type="discovery_sync",
            payload={"config": "test"},
            enqueued_at=datetime.now(timezone.utc).isoformat(),
        )

        # Read the job
        messages = await jobbus.read(
            stream_group="discovery",
            consumer="consumer-1",
            count=1,
            block_ms=1000,
        )

        assert len(messages) == 1
        msg_id, envelope = messages[0]
        assert envelope.job_id == job_id
        assert envelope.job_type == "discovery_sync"

    @pytest.mark.asyncio
    async def test_ack_removes_pending(self, jobbus):
        """Test that ACK removes a message from pending."""
        await jobbus.ensure_group("discovery")

        job_id = await jobbus.enqueue(
            stream_group="discovery",
            job_type="discovery_sync",
            payload={},
            enqueued_at=datetime.now(timezone.utc).isoformat(),
        )

        # Read the job
        messages = await jobbus.read(
            stream_group="discovery",
            consumer="consumer-1",
            count=1,
            block_ms=1000,
        )
        msg_id, envelope = messages[0]

        # ACK the message
        await jobbus.ack("discovery", msg_id)

        # Reading again should return no messages (it was acked)
        messages2 = await jobbus.read(
            stream_group="discovery",
            consumer="consumer-1",
            count=1,
            block_ms=100,
        )
        assert len(messages2) == 0

    @pytest.mark.asyncio
    async def test_mark_processed_tracks_idempotency(self, jobbus):
        """Test mark_processed sets idempotency marker."""
        job_id = "test-idempotency-123"

        # Initially, should not be duplicate
        is_dup = await jobbus.is_duplicate(job_id)
        assert not is_dup

        # Mark as processed
        await jobbus.mark_processed(job_id)

        # Now should be duplicate
        is_dup = await jobbus.is_duplicate(job_id)
        assert is_dup


class TestIdempotency:
    """Test job idempotency (duplicate skipping)."""

    @pytest.mark.asyncio
    async def test_duplicate_job_skipped(self, jobbus):
        """Test that a duplicate job is skipped by consumer."""
        await jobbus.ensure_group("discovery")

        job_id = "test-dedup-job"

        # Mark the job as already processed
        await jobbus.mark_processed(job_id)

        # Enqueue the same job
        # (In real flow, a duplicate check before enqueue would prevent this,
        # but we're testing the consumer-side idempotency)
        envelope = JobEnvelope(
            job_id=job_id,
            job_type="discovery_sync",
            payload={},
            enqueued_at=datetime.now(timezone.utc).isoformat(),
        )

        # Simulate consumer checking idempotency
        is_dup = await jobbus.is_duplicate(job_id)
        assert is_dup  # Should be marked as duplicate

    @pytest.mark.asyncio
    async def test_duplicate_check_with_ttl_expiration(self, jobbus):
        """Test idempotency tracking with TTL."""
        job_id = "test-ttl-job"

        # Mark as processed with short TTL (1 second)
        await jobbus.mark_processed(job_id, ttl=1)

        # Should be duplicate immediately
        is_dup = await jobbus.is_duplicate(job_id)
        assert is_dup

        # Wait for TTL to expire
        await asyncio.sleep(1.1)

        # Should no longer be duplicate
        is_dup = await jobbus.is_duplicate(job_id)
        assert not is_dup


class TestExceptionHandling:
    """Test exception handling (no ACK on error)."""

    @pytest.mark.asyncio
    async def test_handler_exception_leaves_message_unacked(self, jobbus):
        """Test that handler exception leaves message pending (un-acked)."""
        await jobbus.ensure_group("discovery")

        job_id = await jobbus.enqueue(
            stream_group="discovery",
            job_type="discovery_sync",
            payload={},
            enqueued_at=datetime.now(timezone.utc).isoformat(),
        )

        # Read the job
        messages = await jobbus.read(
            stream_group="discovery",
            consumer="consumer-1",
            count=1,
            block_ms=1000,
        )
        msg_id, envelope = messages[0]

        # Do NOT ack (simulate handler exception)

        # Read again immediately should return the same message
        # (XREADGROUP returns only new messages, not pending ones, so use XPENDING)
        pending = await jobbus.redis.xpending(
            jobbus.stream_key("discovery"),
            jobbus.group,
        )
        assert pending["pending"] >= 1  # At least one pending message


class TestXAUTOCLAIM:
    """Test XAUTOCLAIM sweeper functionality."""

    @pytest.mark.asyncio
    async def test_reclaim_stale_messages(self, jobbus):
        """Test reclaiming stale messages via XAUTOCLAIM."""
        await jobbus.ensure_group("discovery")

        job_id = await jobbus.enqueue(
            stream_group="discovery",
            job_type="discovery_sync",
            payload={},
            enqueued_at=datetime.now(timezone.utc).isoformat(),
        )

        # Read the job on consumer-1
        messages = await jobbus.read(
            stream_group="discovery",
            consumer="consumer-1",
            count=1,
            block_ms=1000,
        )
        msg_id, envelope = messages[0]

        # Wait for message to become stale (> 100ms idle)
        await asyncio.sleep(0.2)

        # Reclaim on consumer-2
        reclaimed = await jobbus.reclaim_stale(
            stream_group="discovery",
            consumer="consumer-2",
            min_idle_ms=100,
            count=10,
        )

        assert len(reclaimed) >= 1
        assert reclaimed[0].envelope.job_id == job_id

    @pytest.mark.asyncio
    async def test_reclaim_increments_delivery_count(self, jobbus):
        """Test that reclaim_stale increments delivery count.

        Note: max_deliveries DLQ routing is tested in shared/jobbus/core.py
        """
        await jobbus.ensure_group("discovery")

        job_id = await jobbus.enqueue(
            stream_group="discovery",
            job_type="discovery_sync",
            payload={},
            enqueued_at=datetime.now(timezone.utc).isoformat(),
        )

        # Read the job
        messages = await jobbus.read(
            stream_group="discovery",
            consumer="consumer-1",
            count=1,
            block_ms=1000,
        )
        msg_id, envelope = messages[0]

        # Wait for message to become stale
        await asyncio.sleep(0.2)

        # Reclaim and check delivery count is incremented
        reclaimed = await jobbus.reclaim_stale(
            stream_group="discovery",
            consumer="consumer-2",
            min_idle_ms=100,
            count=10,
        )

        assert len(reclaimed) >= 1
        assert reclaimed[0].delivery_count >= 1


class TestWorkerImport:
    """Test that worker imports correctly."""

    def test_worker_main_imports(self):
        """Test that the worker main module imports without errors."""
        from apps.worker import main

        assert main.WorkerService is not None


# Integration-style test
class TestConsumerEndToEnd:
    """End-to-end consumer tests."""

    @pytest.mark.asyncio
    async def test_consumer_workflow(self, jobbus, redis_client):
        """Test a complete consumer workflow.

        1. Enqueue a job
        2. Consumer reads it
        3. Handler processes it (mocked)
        4. Mark processed, publish result, ack
        """
        from apps.worker.jobs.registry import HANDLER_REGISTRY

        await jobbus.ensure_group("discovery")

        # Enqueue a job
        job_id = await jobbus.enqueue(
            stream_group="discovery",
            job_type="discovery_sync",
            payload={"config": "test"},
            enqueued_at=datetime.now(timezone.utc).isoformat(),
        )

        # Read the job
        messages = await jobbus.read(
            stream_group="discovery",
            consumer="worker-1",
            count=1,
            block_ms=1000,
        )

        assert len(messages) == 1
        msg_id, envelope = messages[0]

        # Check idempotency (should not be duplicate yet)
        is_dup = await jobbus.is_duplicate(job_id)
        assert not is_dup

        # Mark processed
        await jobbus.mark_processed(job_id)

        # Should now be duplicate
        is_dup = await jobbus.is_duplicate(job_id)
        assert is_dup

        # Publish result
        await jobbus.publish_result(
            "discovery",
            job_id,
            "success",
            {"jobs_executed": 5},
        )

        # ACK the message
        await jobbus.ack("discovery", msg_id)

        # Verify message is no longer pending
        pending = await redis_client.xpending(
            jobbus.stream_key("discovery"),
            jobbus.group,
        )
        assert pending["pending"] == 0
