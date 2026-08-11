"""Integration tests for village_id module with real Redis."""

import os

import pytest
import redis

from shared.utils.village_id import (
    VillageId,
    generate_village_id,
    is_valid_village_id,
    parse_village_id,
)


@pytest.fixture
def redis_client():
    """Fixture providing a real Redis client."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    client = redis.from_url(redis_url)

    # Clean up any existing test keys
    for key in client.keys("elder:vid:*"):
        client.delete(key)

    yield client

    # Clean up after test
    for key in client.keys("elder:vid:*"):
        client.delete(key)


class TestVillageIdWithRealRedis:
    """Test village_id generation with real Redis."""

    def test_generate_with_real_redis(self, redis_client):
        """Test generating a village_id with real Redis."""
        village_id = generate_village_id(42, redis_client)

        assert is_valid_village_id(village_id)
        assert village_id.startswith("0000002a-")
        assert len(village_id) == 25

    def test_sequential_allocation(self, redis_client):
        """Test that sequences are allocated sequentially."""
        tenant_id = 42

        ids = [generate_village_id(tenant_id, redis_client) for _ in range(5)]

        # All should be valid
        assert all(is_valid_village_id(vid) for vid in ids)

        # Extract and verify sequences are 1, 2, 3, 4, 5
        sequences = [int(vid.split("-")[1], 16) for vid in ids]
        assert sequences == [1, 2, 3, 4, 5]

    def test_independent_per_tenant(self, redis_client):
        """Test that different tenants have independent sequence counters."""
        tenant1_ids = [generate_village_id(1, redis_client) for _ in range(3)]
        tenant2_ids = [generate_village_id(2, redis_client) for _ in range(3)]
        tenant1_ids_more = [generate_village_id(1, redis_client) for _ in range(2)]

        # Extract sequences
        t1_seqs = [int(vid.split("-")[1], 16) for vid in tenant1_ids]
        t2_seqs = [int(vid.split("-")[1], 16) for vid in tenant2_ids]
        t1_seqs_more = [int(vid.split("-")[1], 16) for vid in tenant1_ids_more]

        # Tenant 1: 1, 2, 3
        assert t1_seqs == [1, 2, 3]
        # Tenant 2: 1, 2, 3 (independent)
        assert t2_seqs == [1, 2, 3]
        # Tenant 1 continues: 4, 5
        assert t1_seqs_more == [4, 5]

    def test_parse_and_validate_generated_ids(self, redis_client):
        """Test that generated IDs can be parsed correctly."""
        tenant_id = 42
        village_id = generate_village_id(tenant_id, redis_client)

        parsed = parse_village_id(village_id)

        assert parsed.tenant_id == tenant_id
        assert isinstance(parsed.object_seq, int)
        assert parsed.object_seq > 0

    def test_generated_ids_are_globally_unique(self, redis_client):
        """Test that generated IDs across all tenants are unique."""
        # Generate IDs across multiple tenants
        ids_tenant_1 = [generate_village_id(1, redis_client) for _ in range(10)]
        ids_tenant_2 = [generate_village_id(2, redis_client) for _ in range(10)]
        ids_tenant_3 = [generate_village_id(3, redis_client) for _ in range(10)]

        all_ids = ids_tenant_1 + ids_tenant_2 + ids_tenant_3

        # All should be unique
        assert len(set(all_ids)) == 30

    def test_redis_counter_key_format(self, redis_client):
        """Test that Redis counter keys follow the correct format."""
        tenant_id = 0x12345678

        # Generate a few IDs to ensure counter is created
        for _ in range(3):
            generate_village_id(tenant_id, redis_client)

        # Check the Redis key exists and has correct value
        counter_key = f"elder:vid:{tenant_id:08x}"
        counter_value = redis_client.get(counter_key)

        assert counter_value is not None
        assert int(counter_value) == 3

    def test_large_tenant_id(self, redis_client):
        """Test with a large tenant ID."""
        large_tenant_id = 0xDEADBEEF

        village_id = generate_village_id(large_tenant_id, redis_client)

        assert is_valid_village_id(village_id)
        assert village_id.startswith("deadbeef-")

    def test_round_trip_with_real_redis(self, redis_client):
        """Test full round-trip: generate -> parse."""
        tenant_id = 99

        # Generate 3 IDs
        generated_ids = [generate_village_id(tenant_id, redis_client) for _ in range(3)]

        # Parse them all
        parsed = [parse_village_id(vid) for vid in generated_ids]

        # Verify tenant IDs match
        assert all(p.tenant_id == tenant_id for p in parsed)

        # Verify sequences are sequential
        sequences = [p.object_seq for p in parsed]
        assert sequences == [1, 2, 3]

    def test_concurrent_generation_simulation(self, redis_client):
        """Test that rapid sequential generation works (simulates concurrency)."""
        tenant_id = 50

        # Rapidly generate many IDs
        ids = [generate_village_id(tenant_id, redis_client) for _ in range(100)]

        # All should be valid and unique
        assert len(set(ids)) == 100
        assert all(is_valid_village_id(vid) for vid in ids)

        # Sequences should be 1..100
        sequences = sorted([int(vid.split("-")[1], 16) for vid in ids])
        assert sequences == list(range(1, 101))
