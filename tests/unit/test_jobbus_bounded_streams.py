"""Bounded Redis Streams tests for the job bus and event emitter.

Redis is memory-capped; before this suite's fix none of the four XADD sites
(job, result, dead-letter and event streams) trimmed, so a slow or absent
consumer could grow a stream until Redis hit OOM and took job dispatch, rate
limits, SSO state and collaboration down with it. These tests run against a
real Redis and prove streams stay under their cap *without* dropping entries
a consumer group has not acknowledged yet.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from shared import events
from shared.jobbus import JobBus, JobBusBackpressureError
from shared.jobbus import core as jobbus_core

REDIS_TEST_URL = "redis://localhost:56379/5"
ENQUEUED_AT = "2026-01-01T00:00:00+00:00"
# Redis trims "MAXLEN ~" in whole radix nodes (stream-node-max-entries=100).
APPROX_SLACK = 100


@pytest_asyncio.fixture
async def redis_client():
    """Async Redis client on the isolated test database (db 5)."""
    client = await aioredis.from_url(
        REDIS_TEST_URL, encoding="utf-8", decode_responses=True
    )
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest_asyncio.fixture
async def bus(redis_client) -> JobBus:
    """JobBus with a low delivery limit so dead-lettering is quick to reach."""
    return JobBus(redis_client=redis_client, max_deliveries=3)


async def _enqueue(bus: JobBus, group: str, n: int) -> list[str]:
    """Enqueue ``n`` jobs and return their job ids."""
    return [
        await bus.enqueue(group, "t", {"i": i}, enqueued_at=ENQUEUED_AT)
        for i in range(n)
    ]


@pytest.mark.parametrize("raw", ["", "   ", "abc", "-5", "0", "1e3", "12.5"])
def test_env_int_never_raises_on_bad_values(monkeypatch, raw: str) -> None:
    """A malformed cap falls back to the default instead of crashing."""
    monkeypatch.setenv("ELDER_JOBBUS_MAXLEN", raw)
    assert jobbus_core._env_int("ELDER_JOBBUS_MAXLEN", 777) == 777


def test_stream_cap_per_group_override(monkeypatch) -> None:
    """A per-group env var overrides the global job-stream cap."""
    bus = JobBus(redis_client=MagicMock())
    assert bus.stream_cap("infra") == jobbus_core.DEFAULT_JOB_STREAM_MAXLEN
    monkeypatch.setenv("ELDER_JOBBUS_MAXLEN", "4000")
    monkeypatch.setenv("ELDER_JOBBUS_MAXLEN_HELP_DESK", "250")
    assert bus.stream_cap("infra") == 4000
    assert bus.stream_cap("help-desk") == 250
    monkeypatch.setenv("ELDER_JOBBUS_MAXLEN_HELP_DESK", "garbage")
    assert bus.stream_cap("help-desk") == 4000


@pytest.mark.parametrize("raw", ["", "nope", "0", "-1"])
def test_events_maxlen_never_raises_on_bad_values(monkeypatch, raw: str) -> None:
    """Bad event-stream caps fall back to the 100k default."""
    monkeypatch.setenv("ELDER_EVENTS_MAXLEN", raw)
    monkeypatch.setenv("ELDER_EVENTS_MAXLEN_ISSUES", raw)
    assert events.events_stream_maxlen("issues") == 100_000


def test_events_maxlen_domain_override(monkeypatch) -> None:
    """A per-domain cap wins over the global event cap."""
    monkeypatch.setenv("ELDER_EVENTS_MAXLEN", "5000")
    monkeypatch.setenv("ELDER_EVENTS_MAXLEN_ISSUES", "300")
    assert events.events_stream_maxlen("issues") == 300
    assert events.events_stream_maxlen("discovery") == 5000


@pytest.mark.asyncio
async def test_emit_event_trims_with_approximate_maxlen(app, monkeypatch) -> None:
    """emit_event passes MAXLEN ~ cap on its XADD."""
    monkeypatch.setenv("ELDER_EVENTS_MAXLEN", "1234")
    fake = MagicMock()
    async with app.app_context():
        with patch("redis.from_url", return_value=fake):
            await events.emit_event("issues", "created", 1, tenant_id=1)
    fake.xadd.assert_called_once()
    assert fake.xadd.call_args.kwargs == {"maxlen": 1234, "approximate": True}


@pytest.mark.asyncio
async def test_event_stream_stays_under_cap_under_sustained_xadd(
    app, redis_client, monkeypatch
) -> None:
    """3000 events into a 500-entry event stream never exceed cap + one node."""
    import redis as sync_redis

    monkeypatch.setenv("ELDER_EVENTS_MAXLEN_CAPTEST", "500")
    client = sync_redis.from_url(REDIS_TEST_URL)
    try:
        async with app.app_context():
            with patch("redis.from_url", return_value=client):
                for i in range(3000):
                    await events.emit_event("captest", "created", i, tenant_id=1)
    finally:
        client.close()
    length = await redis_client.xlen("elder:events:captest")
    assert 0 < length <= 500 + APPROX_SLACK


@pytest.mark.asyncio
async def test_results_stream_stays_under_cap_under_sustained_xadd(
    bus, redis_client, monkeypatch
) -> None:
    """3000 results into a 500-entry results stream stay bounded."""
    monkeypatch.setenv("ELDER_JOBBUS_RESULTS_MAXLEN", "500")
    for i in range(3000):
        await bus.publish_result("captest", f"job-{i}", "success")
    length = await redis_client.xlen(bus.results_key("captest"))
    assert 0 < length <= 500 + APPROX_SLACK


@pytest.mark.asyncio
async def test_job_stream_stays_under_cap_with_working_consumer(
    bus, redis_client, monkeypatch
) -> None:
    """2000 jobs through a 200-entry stream: never above cap, none lost."""
    cap = 200
    monkeypatch.setenv("ELDER_JOBBUS_MAXLEN_CAPTEST", str(cap))
    await bus.ensure_group("captest")
    key = bus.stream_key("captest")

    processed: set[str] = set()
    max_len = 0
    for batch in range(40):
        enqueued = await _enqueue(bus, "captest", 50)
        max_len = max(max_len, await redis_client.xlen(key))
        msgs = await bus.read("captest", "c1", count=50, block_ms=10)
        assert {env.job_id for _, env in msgs} == set(enqueued), batch
        for msg_id, env in msgs:
            await bus.ack("captest", msg_id)
            processed.add(env.job_id)

    assert len(processed) == 2000  # every job delivered exactly once
    assert max_len <= cap
    assert await redis_client.xlen(key) <= cap
    info = await redis_client.xinfo_stream(key)
    assert info["entries-added"] == 2000  # proves trimming actually happened


@pytest.mark.asyncio
@pytest.mark.parametrize("decode", [True, False])
async def test_unacked_entries_survive_trimming(decode: bool) -> None:
    """trim_acked removes only entries below the oldest un-acked id."""
    client = await aioredis.from_url(REDIS_TEST_URL, decode_responses=decode)
    await client.flushdb()
    try:
        bus = JobBus(redis_client=client)
        await bus.ensure_group("trim")
        key = bus.stream_key("trim")
        await _enqueue(bus, "trim", 30)
        msgs = await bus.read("trim", "c1", count=30, block_ms=10)
        assert len(msgs) == 30
        unacked = {msgs[5][0], msgs[20][0]}
        for msg_id, _ in msgs:
            if msg_id not in unacked:
                await bus.ack("trim", msg_id)
        await _enqueue(bus, "trim", 10)  # delivered to nobody yet

        removed = await bus.trim_acked("trim")

        assert removed == 5  # only the five acked entries older than msgs[5]
        remaining = [
            (i.decode() if isinstance(i, bytes) else i)
            for i, _ in await client.xrange(key)
        ]
        assert unacked <= set(remaining)
        assert len(remaining) == 35
        # The un-acked and never-delivered work is all still consumable.
        pending = await client.xpending(key, "workers")
        assert pending["pending"] == 2
        fresh = await bus.read("trim", "c2", count=50, block_ms=10)
        assert len(fresh) == 10
    finally:
        await client.flushdb()
        await client.aclose()


@pytest.mark.asyncio
async def test_trim_is_a_noop_without_consumer_group(bus, redis_client) -> None:
    """With no group every entry is undelivered, so nothing may be trimmed."""
    await _enqueue(bus, "nogroup", 20)
    assert await bus.trim_acked("nogroup") == 0
    assert await bus.trim_acked("never-created") == 0
    assert await redis_client.xlen(bus.stream_key("nogroup")) == 20


@pytest.mark.asyncio
async def test_full_unacked_backlog_rejects_enqueue_instead_of_dropping(
    bus, redis_client, monkeypatch
) -> None:
    """At the cap with nothing acked, enqueue fails loudly and keeps all jobs."""
    cap = 50
    monkeypatch.setenv("ELDER_JOBBUS_MAXLEN_FULL", str(cap))
    await bus.ensure_group("full")
    key = bus.stream_key("full")
    job_ids = await _enqueue(bus, "full", cap)
    read = await bus.read("full", "dead-consumer", count=20, block_ms=10)
    assert len(read) == 20  # 20 pending (un-acked), 30 undelivered

    for _ in range(25):
        with pytest.raises(JobBusBackpressureError):
            await bus.enqueue("full", "t", {}, enqueued_at=ENQUEUED_AT)

    assert await redis_client.xlen(key) == cap
    survivors = await redis_client.xrange(key)
    assert [
        jobbus_core.JobEnvelope.from_json(f["data"]).job_id for _, f in survivors
    ] == job_ids

    # Once the consumer acks, capacity returns and enqueue works again.
    for msg_id, _ in read:
        await bus.ack("full", msg_id)
    await bus.enqueue("full", "t", {}, enqueued_at=ENQUEUED_AT)
    # 19 of the 20 acked entries are trimmed: MINID keeps the floor entry
    # itself (the group's last-delivered id), which is the safe side.
    assert await redis_client.xlen(key) == cap - 19 + 1


@pytest.mark.asyncio
async def test_dead_consumer_pending_entries_are_reclaimed(bus, redis_client) -> None:
    """Entries held by a consumer that died are re-owned after min-idle."""
    await bus.ensure_group("reclaim")
    key = bus.stream_key("reclaim")
    job_ids = await _enqueue(bus, "reclaim", 3)
    assert len(await bus.read("reclaim", "dead", count=10, block_ms=10)) == 3

    # Not idle long enough yet: nothing may be stolen from a live consumer.
    assert await bus.reclaim_stale("reclaim", "alive", min_idle_ms=60_000) == []

    await asyncio.sleep(0.05)
    reclaimed = await bus.reclaim_stale("reclaim", "alive", min_idle_ms=20)

    assert [m.envelope.job_id for m in reclaimed] == job_ids
    owners = await redis_client.xpending_range(key, "workers", "-", "+", 10)
    assert {o["consumer"] for o in owners} == {"alive"}
    # reclaim_stale's housekeeping must not have trimmed the un-acked entries.
    assert await redis_client.xlen(key) == 3


@pytest.mark.asyncio
async def test_dead_letter_after_max_deliveries(bus, redis_client) -> None:
    """A job reclaimed more than max_deliveries times moves to the DLQ once."""
    await bus.ensure_group("dlq")
    key = bus.stream_key("dlq")
    (job_id,) = await _enqueue(bus, "dlq", 1)
    await bus.read("dlq", "c1", count=1, block_ms=10)

    for attempt in range(1, bus.max_deliveries + 1):
        reclaimed = await bus.reclaim_stale("dlq", "c2", min_idle_ms=0)
        assert [m.delivery_count for m in reclaimed] == [attempt]

    assert await bus.reclaim_stale("dlq", "c2", min_idle_ms=0) == []
    assert await bus.reclaim_stale("dlq", "c2", min_idle_ms=0) == []

    dead = await redis_client.xrange(bus.dead_key("dlq"))
    assert len(dead) == 1
    assert job_id in dead[0][1]["data"]
    assert "max_deliveries_exceeded" in dead[0][1]["data"]
    assert (await redis_client.xpending(key, "workers"))["pending"] == 0


@pytest.mark.asyncio
async def test_dead_letter_stream_is_capped(bus, redis_client, monkeypatch) -> None:
    """The dead-letter stream itself cannot grow without bound."""
    monkeypatch.setenv("ELDER_JOBBUS_DEAD_MAXLEN", "200")
    await bus.ensure_group("dlqcap")
    env = jobbus_core.JobEnvelope("j", "t", {}, ENQUEUED_AT)
    for i in range(1000):
        await bus.to_dead_letter("dlqcap", f"{i + 1}-0", env, reason="test")
    assert await redis_client.xlen(bus.dead_key("dlqcap")) <= 200 + APPROX_SLACK


@pytest.mark.asyncio
async def test_stream_stats_reports_length_pending_and_age(bus) -> None:
    """stream_stats feeds the length/pending/oldest-age gauges per stream."""
    await bus.ensure_group("stats")
    await _enqueue(bus, "stats", 7)
    await bus.read("stats", "c1", count=4, block_ms=10)
    await asyncio.sleep(0.05)

    stats = await bus.stream_stats("stats")

    assert (stats.length, stats.pending) == (7, 4)
    assert 0.04 < stats.oldest_pending_age_seconds < 30
    assert jobbus_core._STREAM_STATS[bus.stream_key("stats")] is stats
    # Bounded label set: observations are keyed by stream name only.
    observations = jobbus_core._observe("pending")(None)
    assert all(set(o.attributes) == {"stream"} for o in observations)

    empty = await bus.stream_stats("stats-no-group")
    assert (empty.length, empty.pending, empty.oldest_pending_age_seconds) == (0, 0, 0)
