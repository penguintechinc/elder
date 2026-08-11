"""Tests for Redis Streams job-bus core library.

All tests run against REAL Redis at localhost:56379/db5 (isolated).
Tests exercise real stream semantics: XADD, XREADGROUP, XACK, XAUTOCLAIM, XPENDING.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from shared.jobbus import JobBus, JobEnvelope, ReclamedMessage


@pytest_asyncio.fixture
async def redis_client():
    """Create async Redis client for test database (db 5, isolated)."""
    client = await aioredis.from_url(
        "redis://localhost:56379/5", encoding="utf-8", decode_responses=True
    )
    yield client
    # Cleanup: flush test database
    await client.flushdb()
    await client.aclose()


@pytest_asyncio.fixture
async def jobbus(redis_client):
    """Create JobBus instance with test Redis client."""
    return JobBus(
        redis_client=redis_client,
        max_deliveries=5,
        group="workers",
        idempotency_ttl=86400,
    )


@pytest.mark.asyncio
async def test_ensure_group_creates_consumer_group(
    redis_client: aioredis.Redis, jobbus: JobBus
) -> None:
    """Test that ensure_group creates a consumer group if not exists."""
    stream_group = "test_module"

    # Before: group should not exist
    stream_key = jobbus.stream_key(stream_group)
    try:
        groups_before = await redis_client.xinfo_groups(stream_key)
    except aioredis.ResponseError:
        groups_before = []
    assert len(groups_before) == 0, "Stream should not have any groups initially"

    # Call ensure_group
    await jobbus.ensure_group(stream_group)

    # After: group should exist
    groups_after = await redis_client.xinfo_groups(stream_key)
    assert len(groups_after) == 1, "Group should be created"
    assert groups_after[0]["name"] == "workers", "Group name should be 'workers'"


@pytest.mark.asyncio
async def test_ensure_group_ignores_busygroup(
    redis_client: aioredis.Redis, jobbus: JobBus
) -> None:
    """Test that ensure_group ignores BUSYGROUP error on second call."""
    stream_group = "test_module"

    # First call succeeds
    await jobbus.ensure_group(stream_group)

    # Second call should not raise (BUSYGROUP is caught)
    await jobbus.ensure_group(stream_group)

    # Verify still only one group
    stream_key = jobbus.stream_key(stream_group)
    groups = await redis_client.xinfo_groups(stream_key)
    assert len(groups) == 1


@pytest.mark.asyncio
async def test_enqueue_basic(jobbus: JobBus) -> None:
    """Test basic job enqueue: adds to stream and returns job_id."""
    stream_group = "infrastructure"
    job_type = "sync_entity"
    payload = {"entity_id": 123, "action": "refresh"}
    enqueued_at = datetime.now(UTC).isoformat()
    tenant_id = 42

    job_id = await jobbus.enqueue(
        stream_group=stream_group,
        job_type=job_type,
        payload=payload,
        enqueued_at=enqueued_at,
        tenant_id=tenant_id,
    )

    # Verify job_id is a UUID string
    assert job_id
    assert len(job_id) == 36  # UUID4 format length

    # Verify message in stream
    stream_key = jobbus.stream_key(stream_group)
    entries = await jobbus.redis.xlen(stream_key)
    assert entries == 1


@pytest.mark.asyncio
async def test_enqueue_with_idempotency_key(jobbus: JobBus) -> None:
    """Test enqueue with idempotency_key (passed to caller, not generated)."""
    stream_group = "helpdesk"
    idempotency_key = "ticket-123-send-email"
    enqueued_at = datetime.now(UTC).isoformat()

    job_id = await jobbus.enqueue(
        stream_group=stream_group,
        job_type="send_email",
        payload={"ticket_id": 123},
        enqueued_at=enqueued_at,
        idempotency_key=idempotency_key,
    )

    # Verify idempotency_key is stored in envelope
    stream_key = jobbus.stream_key(stream_group)
    msg = await jobbus.redis.xrange(stream_key, count=1)
    msg_data = msg[0][1]["data"]
    envelope_dict = json.loads(msg_data)
    assert envelope_dict["idempotency_key"] == idempotency_key


@pytest.mark.asyncio
async def test_read_enqueue_ack_roundtrip(jobbus: JobBus) -> None:
    """Test end-to-end: enqueue -> read -> ack."""
    stream_group = "infrastructure"
    await jobbus.ensure_group(stream_group)

    # Enqueue
    enqueued_at = datetime.now(UTC).isoformat()
    job_id = await jobbus.enqueue(
        stream_group=stream_group,
        job_type="scan_network",
        payload={"subnet": "10.0.0.0/8"},
        enqueued_at=enqueued_at,
        tenant_id=1,
    )

    # Read as consumer "worker-1"
    messages = await jobbus.read(
        stream_group=stream_group,
        consumer="worker-1",
        count=1,
        block_ms=100,
    )

    assert len(messages) == 1
    msg_id, envelope = messages[0]
    assert envelope.job_id == job_id
    assert envelope.job_type == "scan_network"
    assert envelope.tenant_id == 1

    # ACK
    await jobbus.ack(stream_group, msg_id)

    # Verify ACKed (should not be in pending)
    stream_key = jobbus.stream_key(stream_group)
    pending = await jobbus.redis.xpending(stream_key, "workers")
    assert pending["pending"] == 0


@pytest.mark.asyncio
async def test_envelope_serialization(jobbus: JobBus) -> None:
    """Test JobEnvelope to_json/from_json roundtrip."""
    original = JobEnvelope(
        job_id=str(uuid4()),
        job_type="export_diagram",
        payload={"diagram_id": 42, "format": "png"},
        enqueued_at="2026-07-08T10:30:00Z",
        tenant_id=99,
        idempotency_key="diagram-export-42",
    )

    # Serialize
    json_str = original.to_json()
    assert isinstance(json_str, str)

    # Deserialize
    restored = JobEnvelope.from_json(json_str)

    assert restored.job_id == original.job_id
    assert restored.job_type == original.job_type
    assert restored.payload == original.payload
    assert restored.enqueued_at == original.enqueued_at
    assert restored.tenant_id == original.tenant_id
    assert restored.idempotency_key == original.idempotency_key


@pytest.mark.asyncio
async def test_is_duplicate_after_mark_processed(jobbus: JobBus) -> None:
    """Test idempotency: is_duplicate returns True after mark_processed."""
    job_id = str(uuid4())

    # Before mark: not a duplicate
    is_dup = await jobbus.is_duplicate(job_id)
    assert is_dup is False

    # Mark as processed
    await jobbus.mark_processed(job_id, ttl=3600)

    # After mark: is a duplicate
    is_dup = await jobbus.is_duplicate(job_id)
    assert is_dup is True


@pytest.mark.asyncio
async def test_mark_processed_expires(jobbus: JobBus) -> None:
    """Test that mark_processed sets TTL (key expires)."""
    job_id = str(uuid4())

    # Mark with short TTL
    await jobbus.mark_processed(job_id, ttl=1)

    # Immediately, key exists
    assert await jobbus.is_duplicate(job_id) is True

    # Wait for expiration
    await asyncio.sleep(1.1)

    # After TTL, key should be gone
    assert await jobbus.is_duplicate(job_id) is False


@pytest.mark.asyncio
async def test_publish_result(jobbus: JobBus) -> None:
    """Test result publication to results stream."""
    stream_group = "diagrams"
    job_id = str(uuid4())

    # Publish result
    await jobbus.publish_result(
        stream_group=stream_group,
        job_id=job_id,
        status="success",
        result={"export_url": "s3://bucket/diagram.png"},
    )

    # Verify in results stream
    results_key = jobbus.results_key(stream_group)
    msg = await jobbus.redis.xrange(results_key, count=1)
    msg_data = msg[0][1]["data"]
    result_dict = json.loads(msg_data)

    assert result_dict["job_id"] == job_id
    assert result_dict["status"] == "success"
    assert result_dict["result"]["export_url"] == "s3://bucket/diagram.png"


@pytest.mark.asyncio
async def test_reclaim_stale_basic(jobbus: JobBus) -> None:
    """Test XAUTOCLAIM reclaim of stale pending messages.

    Scenario: consumer-1 reads but doesn't ACK, then consumer-2 reclaims.
    """
    stream_group = "infrastructure"
    await jobbus.ensure_group(stream_group)

    # Consumer-1 enqueues and reads (but doesn't ACK)
    enqueued_at = datetime.now(UTC).isoformat()
    job_id = await jobbus.enqueue(
        stream_group=stream_group,
        job_type="sync_entity",
        payload={"entity_id": 100},
        enqueued_at=enqueued_at,
        tenant_id=1,
    )

    messages = await jobbus.read(
        stream_group=stream_group,
        consumer="consumer-1",
        count=1,
        block_ms=100,
    )
    assert len(messages) == 1
    msg_id, envelope = messages[0]
    assert envelope.job_id == job_id

    # Simulate dead consumer: consumer-2 reclaims with min_idle_ms=0 (immediate)
    reclaimed = await jobbus.reclaim_stale(
        stream_group=stream_group,
        consumer="consumer-2",
        min_idle_ms=0,
        count=10,
    )

    assert len(reclaimed) == 1
    reclaimed_msg = reclaimed[0]
    assert reclaimed_msg.msg_id == msg_id
    assert reclaimed_msg.envelope.job_id == job_id
    assert reclaimed_msg.delivery_count >= 1


@pytest.mark.asyncio
async def test_dlq_after_max_deliveries(jobbus: JobBus) -> None:
    """Test DLQ routing after max_deliveries exceeded.

    Scenario:
    1. Enqueue a message
    2. Read/reclaim multiple times (each increments delivery count)
    3. After max_deliveries reclaims, message routes to DLQ
    4. Message should NOT be returned from reclaim, should be in dead-letter stream
    """
    stream_group = "helpdesk"
    max_deliveries = 2  # Use 2 for quicker test
    jobbus_custom = JobBus(
        redis_client=jobbus.redis,
        max_deliveries=max_deliveries,
        group="workers",
        idempotency_ttl=86400,
    )
    await jobbus_custom.ensure_group(stream_group)

    # Enqueue
    enqueued_at = datetime.now(UTC).isoformat()
    job_id = await jobbus_custom.enqueue(
        stream_group=stream_group,
        job_type="send_email",
        payload={"ticket_id": 456},
        enqueued_at=enqueued_at,
        tenant_id=2,
    )

    msg_id = None

    # First read (delivery_count will be 0 until first reclaim)
    messages = await jobbus_custom.read(
        stream_group=stream_group,
        consumer="consumer-0",
        count=1,
        block_ms=100,
    )
    assert len(messages) == 1
    msg_id = messages[0][0]

    # Reclaim multiple times (each increment the delivery counter)
    for i in range(max_deliveries + 1):
        reclaimed = await jobbus_custom.reclaim_stale(
            stream_group=stream_group,
            consumer=f"reclaimer-{i}",
            min_idle_ms=0,
            count=10,
        )
        if i < max_deliveries:
            # Before max, message should be reclaimed
            assert len(reclaimed) == 1, f"Iteration {i}: message should be reclaimed"
            assert reclaimed[0].msg_id == msg_id
        else:
            # At max_deliveries, message should be DLQ'd
            assert len(reclaimed) == 0, f"Iteration {i}: message should be DLQ'd"

    # Verify in dead-letter stream
    dead_key = jobbus_custom.dead_key(stream_group)
    dead_messages = await jobbus_custom.redis.xrange(dead_key)
    assert len(dead_messages) == 1

    dead_msg_data = dead_messages[0][1]["data"]
    dead_dict = json.loads(dead_msg_data)
    assert dead_dict["job_id"] == job_id
    assert "dlq_reason" in dead_dict


@pytest.mark.asyncio
async def test_stream_keys_helpers(jobbus: JobBus) -> None:
    """Test stream key helper methods."""
    group = "test_group"

    main_key = jobbus.stream_key(group)
    assert main_key == "elder:jobs:test_group"

    results_key = jobbus.results_key(group)
    assert results_key == "elder:results:test_group"

    dead_key = jobbus.dead_key(group)
    assert dead_key == "elder:jobs:test_group:dead"

    job_id = str(uuid4())
    idempo_key = jobbus.idempotency_key(job_id)
    assert idempo_key.startswith("elder:jobs:processed:")


@pytest.mark.asyncio
async def test_read_with_no_consumer_group(jobbus: JobBus) -> None:
    """Test read gracefully handles missing consumer group (returns empty)."""
    stream_group = "nonexistent"

    messages = await jobbus.read(
        stream_group=stream_group,
        consumer="consumer-1",
        count=1,
        block_ms=100,
    )

    assert messages == []


@pytest.mark.asyncio
async def test_reclaim_with_no_consumer_group(jobbus: JobBus) -> None:
    """Test reclaim gracefully handles missing consumer group (returns empty)."""
    stream_group = "nonexistent"

    reclaimed = await jobbus.reclaim_stale(
        stream_group=stream_group,
        consumer="consumer-1",
        min_idle_ms=0,
        count=10,
    )

    assert reclaimed == []


@pytest.mark.asyncio
async def test_multiple_jobs_enqueue_and_read(jobbus: JobBus) -> None:
    """Test enqueuing multiple jobs and reading them."""
    stream_group = "ai_search"
    await jobbus.ensure_group(stream_group)

    job_ids = []
    enqueued_at = datetime.now(UTC).isoformat()

    # Enqueue 5 jobs
    for i in range(5):
        job_id = await jobbus.enqueue(
            stream_group=stream_group,
            job_type="embed_document",
            payload={"doc_id": i, "content": f"doc {i}"},
            enqueued_at=enqueued_at,
            tenant_id=1,
        )
        job_ids.append(job_id)

    # Read batch of 3
    messages = await jobbus.read(
        stream_group=stream_group,
        consumer="worker-1",
        count=3,
        block_ms=100,
    )

    assert len(messages) == 3
    read_ids = [envelope.job_id for _, envelope in messages]
    assert read_ids == job_ids[:3]

    # Read remaining 2
    messages = await jobbus.read(
        stream_group=stream_group,
        consumer="worker-1",
        count=3,
        block_ms=100,
    )

    assert len(messages) == 2
    read_ids = [envelope.job_id for _, envelope in messages]
    assert read_ids == job_ids[3:]


@pytest.mark.asyncio
async def test_reclaim_mixed_delivery_counts(jobbus: JobBus) -> None:
    """Test reclaim correctly separates DLQ vs requeue based on delivery count.

    Scenario:
    - Enqueue 2 messages
    - Reclaim msg1 once (delivery_count=1, below max)
    - Reclaim msg1 twice more (delivery_count=3, exceeds max=2)
    - Reclaim msg2 once (delivery_count=1, below max)
    - Verify: msg1 goes to DLQ, msg2 is reclaimed
    """
    stream_group = "streams"
    max_deliveries = 2
    jobbus_custom = JobBus(
        redis_client=jobbus.redis,
        max_deliveries=max_deliveries,
        group="workers",
        idempotency_ttl=86400,
    )
    await jobbus_custom.ensure_group(stream_group)

    # Enqueue 2 jobs
    enqueued_at = datetime.now(UTC).isoformat()
    job_id_1 = await jobbus_custom.enqueue(
        stream_group=stream_group,
        job_type="execute_stream",
        payload={"stream_id": 1},
        enqueued_at=enqueued_at,
        tenant_id=1,
    )
    job_id_2 = await jobbus_custom.enqueue(
        stream_group=stream_group,
        job_type="execute_stream",
        payload={"stream_id": 2},
        enqueued_at=enqueued_at,
        tenant_id=1,
    )

    # Initial read (no delivery count incremented yet, just reading)
    messages = await jobbus_custom.read(
        stream_group=stream_group,
        consumer="consumer-0",
        count=2,
        block_ms=100,
    )
    assert len(messages) == 2
    msg_id_1 = messages[0][0]
    msg_id_2 = messages[1][0]

    # Reclaim msg1 multiple times (to trigger DLQ)
    for _ in range(max_deliveries + 1):
        reclaimed = await jobbus_custom.reclaim_stale(
            stream_group=stream_group,
            consumer="reclaimer-1",
            min_idle_ms=0,
            count=1,  # Only get first message each time
        )
        # After max_deliveries+1 reclaims, msg1 goes to DLQ

    # Reclaim msg2 once (should still be available)
    reclaimed = await jobbus_custom.reclaim_stale(
        stream_group=stream_group,
        consumer="reclaimer-2",
        min_idle_ms=0,
        count=10,
    )

    # msg2 should be reclaimed (only 1 reclaim, below max_deliveries=2)
    assert len(reclaimed) >= 1, "msg2 should still be reclaimable"

    # Verify msg1 in dead-letter stream
    dead_key = jobbus_custom.dead_key(stream_group)
    dead_messages = await jobbus_custom.redis.xrange(dead_key)
    assert len(dead_messages) >= 1


@pytest.mark.asyncio
async def test_full_workflow_idempotent_retry(jobbus: JobBus) -> None:
    """Test full workflow: enqueue -> read -> fail -> mark_processed -> no retry.

    Idempotency check prevents reprocessing of already-handled jobs.
    """
    stream_group = "discovery"
    idempotency_key = "sync-aws-account-123"
    await jobbus.ensure_group(stream_group)

    enqueued_at = datetime.now(UTC).isoformat()
    job_id = await jobbus.enqueue(
        stream_group=stream_group,
        job_type="sync_cloud_provider",
        payload={"account_id": 123},
        enqueued_at=enqueued_at,
        tenant_id=1,
        idempotency_key=idempotency_key,
    )

    # Read and process
    messages = await jobbus.read(
        stream_group=stream_group,
        consumer="worker-1",
        count=1,
        block_ms=100,
    )
    assert len(messages) == 1
    msg_id, envelope = messages[0]

    # Simulate successful processing: mark as processed and ACK
    await jobbus.mark_processed(job_id, ttl=3600)
    await jobbus.ack(stream_group, msg_id)

    # Verify it's now marked as duplicate
    is_dup = await jobbus.is_duplicate(job_id)
    assert is_dup is True
