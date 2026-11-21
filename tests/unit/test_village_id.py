"""Unit tests for village_id module (AD-3 format: TTTTTTTT-OOOOOOOOOOOOOOOO)."""

from unittest.mock import MagicMock, Mock

import pytest

from shared.utils.village_id import (
    VILLAGE_ID_RE,
    VillageId,
    generate_village_id,
    is_valid_village_id,
    parse_village_id,
)


class TestVillageIdFormat:
    """Test the AD-3 format compliance."""

    def test_village_id_regex_matches_valid_format(self):
        """Test VILLAGE_ID_RE matches correct format: TTTTTTTT-OOOOOOOOOOOOOOOO."""
        assert VILLAGE_ID_RE.match("0000002a-000000000000f3c1")
        assert VILLAGE_ID_RE.match("ffffffff-ffffffffffffffff")
        assert VILLAGE_ID_RE.match("00000001-0000000000000001")

    def test_village_id_regex_rejects_invalid_format(self):
        """Test VILLAGE_ID_RE rejects incorrect formats."""
        assert not VILLAGE_ID_RE.match("0000002a-0000f3c1")  # Wrong lengths
        assert not VILLAGE_ID_RE.match("0000002A-000000000000F3C1")  # Uppercase
        assert not VILLAGE_ID_RE.match("0000002a_000000000000f3c1")  # Wrong separator
        assert not VILLAGE_ID_RE.match("0000002a-000000000000f3c1 ")  # Trailing space
        assert not VILLAGE_ID_RE.match("0000002a-000000000000f3cg")  # Invalid hex


class TestIsValidVillageId:
    """Test is_valid_village_id function."""

    def test_valid_ids_pass(self):
        """Test valid IDs return True."""
        assert is_valid_village_id("0000002a-000000000000f3c1")
        assert is_valid_village_id("00000001-0000000000000001")
        assert is_valid_village_id("ffffffff-ffffffffffffffff")

    def test_invalid_ids_fail(self):
        """Test invalid IDs return False."""
        assert not is_valid_village_id("invalid")
        assert not is_valid_village_id("")
        assert not is_valid_village_id("0000002a-0000f3c1")  # Wrong lengths
        assert not is_valid_village_id("0000002A-000000000000F3C1")  # Uppercase

    def test_non_string_input_returns_false(self):
        """Test non-string inputs return False."""
        assert not is_valid_village_id(None)
        assert not is_valid_village_id(123)
        assert not is_valid_village_id([])
        assert not is_valid_village_id({})


class TestGenerateVillageId:
    """Test generate_village_id function."""

    def test_generate_returns_valid_format(self):
        """Test generated IDs match AD-3 format."""
        redis_client = Mock()
        redis_client.incr.return_value = 1

        village_id = generate_village_id(42, redis_client)

        assert is_valid_village_id(village_id)
        assert village_id.startswith("0000002a-")  # 42 in hex = 0x2a
        assert len(village_id) == 25

    def test_generate_includes_tenant_id_in_hex(self):
        """Test tenant ID is correctly formatted in hex."""
        redis_client = Mock()
        redis_client.incr.return_value = 100

        village_id = generate_village_id(255, redis_client)

        assert village_id.startswith("000000ff-")  # 255 in hex = 0xff

    def test_generate_includes_sequence_in_hex(self):
        """Test sequence is correctly formatted in hex (16 hex chars)."""
        redis_client = Mock()
        redis_client.incr.return_value = 0xF3C1

        village_id = generate_village_id(42, redis_client)

        assert village_id.endswith("-000000000000f3c1")  # 0xf3c1 in 16-char hex

    def test_generate_calls_redis_incr_with_correct_key(self):
        """Test Redis INCR is called with correct counter key."""
        redis_client = Mock()
        redis_client.incr.return_value = 1

        generate_village_id(42, redis_client)

        redis_client.incr.assert_called_once_with("elder:vid:0000002a")

    def test_generate_raises_on_invalid_tenant_id(self):
        """Test ValueError raised for invalid tenant_id."""
        redis_client = Mock()

        with pytest.raises(ValueError, match="tenant_id must be non-negative int"):
            generate_village_id(-1, redis_client)

        with pytest.raises(ValueError, match="tenant_id must be non-negative int"):
            generate_village_id("not_an_int", redis_client)

    def test_generate_raises_on_missing_redis_client(self):
        """Test ValueError raised when redis_client is None."""
        with pytest.raises(ValueError, match="redis_client required"):
            generate_village_id(42, None)

    def test_sequential_generation_produces_monotonic_ids(self):
        """Test that sequential calls produce increasing sequence numbers."""
        redis_client = Mock()
        redis_client.incr.side_effect = [1, 2, 3, 4, 5]

        ids = [generate_village_id(42, redis_client) for _ in range(5)]

        # Extract sequence numbers
        seqs = [int(v.split("-")[1], 16) for v in ids]
        assert seqs == [1, 2, 3, 4, 5]

    def test_different_tenants_use_independent_counters(self):
        """Test that different tenants have independent sequences."""
        redis_client = Mock()
        # Simulate different counter keys
        counter_values = {}

        def mock_incr(key):
            counter_values[key] = counter_values.get(key, 0) + 1
            return counter_values[key]

        redis_client.incr.side_effect = mock_incr

        # Generate IDs for two different tenants
        id1 = generate_village_id(1, redis_client)
        id2 = generate_village_id(2, redis_client)
        id3 = generate_village_id(1, redis_client)  # Back to tenant 1

        # Extract sequence numbers
        seq1 = int(id1.split("-")[1], 16)
        seq2 = int(id2.split("-")[1], 16)
        seq3 = int(id3.split("-")[1], 16)

        # Tenant 1 should have sequence 1 and 2
        assert seq1 == 1
        assert seq3 == 2
        # Tenant 2 should have sequence 1
        assert seq2 == 1

    def test_length_fits_string_32(self):
        """Test generated ID length is <= 32 chars (String(32) column)."""
        redis_client = Mock()
        redis_client.incr.return_value = 0xFFFFFFFFFFFFFFFF

        village_id = generate_village_id(0xFFFFFFFF, redis_client)

        assert len(village_id) <= 32
        assert len(village_id) == 25  # Exact expected length


class TestParseVillageId:
    """Test parse_village_id function."""

    def test_parse_valid_id_returns_dataclass(self):
        """Test parsing returns VillageId dataclass."""
        result = parse_village_id("0000002a-000000000000f3c1")

        assert isinstance(result, VillageId)
        assert result.tenant_id == 42
        assert result.object_seq == 0xF3C1

    def test_parse_extracts_tenant_id_correctly(self):
        """Test tenant ID is correctly extracted from hex."""
        result = parse_village_id("000000ff-0000000000000001")

        assert result.tenant_id == 255

    def test_parse_extracts_sequence_correctly(self):
        """Test sequence is correctly extracted from hex."""
        result = parse_village_id("00000001-0000000000001000")

        assert result.object_seq == 0x1000

    def test_parse_rejects_invalid_format(self):
        """Test ValueError raised for invalid format."""
        with pytest.raises(ValueError, match="Invalid village ID format"):
            parse_village_id("invalid")

        with pytest.raises(ValueError, match="Invalid village ID format"):
            parse_village_id("0000002a-0000f3c1")  # Wrong lengths

    def test_parse_round_trip(self):
        """Test round-trip: generate -> parse -> values match."""
        redis_client = Mock()
        redis_client.incr.return_value = 0xDEADBEEF

        generated = generate_village_id(0x12345678, redis_client)
        parsed = parse_village_id(generated)

        assert parsed.tenant_id == 0x12345678
        assert parsed.object_seq == 0xDEADBEEF


class TestVillageIdDataclass:
    """Test VillageId dataclass."""

    def test_dataclass_has_slots(self):
        """Test VillageId uses __slots__ (no __dict__)."""
        vid = VillageId(tenant_id=1, object_seq=2)
        assert not hasattr(vid, "__dict__")
        assert hasattr(vid, "__slots__")

    def test_dataclass_immutable_access(self):
        """Test VillageId attributes are accessible."""
        vid = VillageId(tenant_id=42, object_seq=100)
        assert vid.tenant_id == 42
        assert vid.object_seq == 100


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_max_tenant_id_32_bit(self):
        """Test maximum 32-bit tenant ID."""
        redis_client = Mock()
        redis_client.incr.return_value = 1

        # Max 32-bit: 0xffffffff (4294967295)
        village_id = generate_village_id(0xFFFFFFFF, redis_client)

        assert village_id.startswith("ffffffff-")
        assert is_valid_village_id(village_id)

    def test_tenant_id_zero(self):
        """Test tenant ID of zero."""
        redis_client = Mock()
        redis_client.incr.return_value = 1

        village_id = generate_village_id(0, redis_client)

        assert village_id.startswith("00000000-")
        assert is_valid_village_id(village_id)

    def test_sequence_zero(self):
        """Test sequence of zero (though Redis INCR never returns 0)."""
        redis_client = Mock()
        redis_client.incr.return_value = 0  # Manually set to 0

        village_id = generate_village_id(1, redis_client)

        assert village_id == "00000001-0000000000000000"
        assert is_valid_village_id(village_id)
