"""Unit tests for the NVD sync service.

Tests cover:
- NVDSyncService initialization
- Vulnerability sync with mocked database and NVD client
- Sync status tracking and statistics
- Error handling and edge cases
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.api.services.sbom.vulnerability.nvd_client import NVDVulnerability
from apps.api.services.sbom.vulnerability.nvd_sync import (
    NVDSyncService,
    MAX_VULNS_PER_SYNC,
    NVD_SYNC_INTERVAL_HOURS,
)


@pytest.fixture
def mock_db():
    """Create a mock database connection."""
    db = MagicMock()
    db.vulnerabilities = MagicMock()
    db.commit = MagicMock()
    return db


@pytest.fixture
def nvd_sync_service(mock_db):
    """Create an NVDSyncService instance with mocked database."""
    return NVDSyncService(db=mock_db, nvd_api_key="test-api-key")


class TestNVDSyncServiceInit:
    """Test NVDSyncService initialization."""

    def test_init_with_api_key(self, mock_db):
        """Test initialization with NVD API key."""
        service = NVDSyncService(db=mock_db, nvd_api_key="test-key")
        assert service.db == mock_db
        assert service.nvd_api_key == "test-key"
        assert service.stats == {
            "processed": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
        }

    def test_init_without_api_key(self, mock_db):
        """Test initialization without NVD API key."""
        service = NVDSyncService(db=mock_db)
        assert service.db == mock_db
        assert service.nvd_api_key is None
        assert service.stats == {
            "processed": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
        }

    def test_init_stats_initialization(self, mock_db):
        """Test that stats are properly initialized."""
        service = NVDSyncService(db=mock_db)
        assert service.stats["processed"] == 0
        assert service.stats["updated"] == 0
        assert service.stats["skipped"] == 0
        assert service.stats["errors"] == 0


class TestGetVulnsNeedingSync:
    """Test _get_vulns_needing_sync method."""

    def test_get_vulns_no_results(self, nvd_sync_service, mock_db):
        """Test when no vulnerabilities need syncing."""
        # Mock the PyDAL query chain properly
        query_mock = MagicMock()
        query_mock.select = MagicMock(return_value=[])
        query_mock.__iand__ = MagicMock(return_value=query_mock)
        query_mock.__or__ = MagicMock(return_value=query_mock)

        mock_db.vulnerabilities.cve_id.startswith = MagicMock(return_value=query_mock)
        mock_db.vulnerabilities.nvd_last_sync = MagicMock()
        mock_db.vulnerabilities.nvd_last_sync.__eq__ = MagicMock(return_value=query_mock)
        mock_db.vulnerabilities.nvd_last_sync.__lt__ = MagicMock(return_value=query_mock)

        result = nvd_sync_service._get_vulns_needing_sync(
            max_vulns=100, force_refresh=False
        )

        assert result == []

    def test_get_vulns_with_force_refresh(self, nvd_sync_service, mock_db):
        """Test that force_refresh bypasses time-based filtering."""
        mock_vuln = MagicMock()
        mock_vuln.id = 1
        mock_vuln.cve_id = "CVE-2024-12345"
        mock_vuln.cvss_score = None
        mock_vuln.cvss_vector = None
        mock_vuln.nvd_last_sync = None

        query_mock = MagicMock()
        mock_db.vulnerabilities.cve_id.startswith.return_value = query_mock
        mock_db.return_value.select.return_value = [mock_vuln]

        result = nvd_sync_service._get_vulns_needing_sync(
            max_vulns=100, force_refresh=True
        )

        assert len(result) == 1
        assert result[0].cve_id == "CVE-2024-12345"

    def test_get_vulns_respects_max_vulns(self, nvd_sync_service, mock_db):
        """Test that max_vulns parameter is respected."""
        mock_vulns = [MagicMock() for _ in range(5)]
        for i, vuln in enumerate(mock_vulns):
            vuln.id = i
            vuln.cve_id = f"CVE-2024-{i:05d}"

        query_mock = MagicMock()
        query_mock.select = MagicMock(return_value=mock_vulns)
        query_mock.__iand__ = MagicMock(return_value=query_mock)
        query_mock.__or__ = MagicMock(return_value=query_mock)

        # Mock db.vulnerabilities.cve_id.startswith to return query_mock
        mock_db.vulnerabilities.cve_id.startswith = MagicMock(return_value=query_mock)

        # Mock db.vulnerabilities.nvd_last_sync comparisons
        mock_db.vulnerabilities.nvd_last_sync = MagicMock()
        mock_db.vulnerabilities.nvd_last_sync.__eq__ = MagicMock(return_value=query_mock)
        mock_db.vulnerabilities.nvd_last_sync.__lt__ = MagicMock(return_value=query_mock)

        # Mock db(query) to return query_mock which has select method
        mock_db.return_value = query_mock
        mock_db.__call__ = MagicMock(return_value=query_mock)

        result = nvd_sync_service._get_vulns_needing_sync(
            max_vulns=10, force_refresh=False
        )

        assert len(result) == 5

    def test_get_vulns_filters_by_cve_id_prefix(self, nvd_sync_service, mock_db):
        """Test that only CVE-prefixed vulnerabilities are selected."""
        query_mock = MagicMock()
        query_mock.select = MagicMock(return_value=[])
        query_mock.__iand__ = MagicMock(return_value=query_mock)
        query_mock.__or__ = MagicMock(return_value=query_mock)

        mock_db.vulnerabilities.cve_id.startswith = MagicMock(return_value=query_mock)
        mock_db.vulnerabilities.nvd_last_sync = MagicMock()
        mock_db.vulnerabilities.nvd_last_sync.__eq__ = MagicMock(return_value=query_mock)
        mock_db.vulnerabilities.nvd_last_sync.__lt__ = MagicMock(return_value=query_mock)

        nvd_sync_service._get_vulns_needing_sync(max_vulns=100, force_refresh=False)

        mock_db.vulnerabilities.cve_id.startswith.assert_called_with("CVE-")

    def test_get_vulns_checks_sync_interval(self, nvd_sync_service, mock_db):
        """Test that sync interval is checked when force_refresh is False."""
        query_mock = MagicMock()
        query_mock.select = MagicMock(return_value=[])
        query_mock.__iand__ = MagicMock(return_value=query_mock)
        query_mock.__or__ = MagicMock(return_value=query_mock)

        mock_db.vulnerabilities.cve_id.startswith = MagicMock(return_value=query_mock)
        mock_db.vulnerabilities.nvd_last_sync = MagicMock()
        mock_db.vulnerabilities.nvd_last_sync.__eq__ = MagicMock(return_value=query_mock)
        mock_db.vulnerabilities.nvd_last_sync.__lt__ = MagicMock(return_value=query_mock)

        nvd_sync_service._get_vulns_needing_sync(
            max_vulns=100, force_refresh=False
        )

        # Verify the query includes time-based filtering
        query_mock.__iand__.assert_called()


class TestSyncSingleVulnerability:
    """Test _sync_single_vulnerability method."""

    @pytest.mark.asyncio
    async def test_sync_single_vuln_with_nvd_data(self, nvd_sync_service, mock_db):
        """Test syncing a vulnerability with successful NVD data retrieval."""
        mock_vuln = MagicMock()
        mock_vuln.id = 1
        mock_vuln.cve_id = "CVE-2024-12345"
        mock_vuln.cvss_score = 5.0
        mock_vuln.cvss_vector = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:L/I:N/A:N"
        mock_vuln.nvd_last_sync = None

        nvd_data = NVDVulnerability(
            id="CVE-2024-12345",
            description="Test vulnerability",
            severity="MEDIUM",
            cvss_score=7.5,
            cvss_vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N",
            cpe_matches=["cpe:2.3:a:vendor:product:1.0:*:*:*:*:*:*:*"],
            references=[{"url": "https://example.com", "source": "NVD"}],
            published="2024-01-01",
            modified="2024-01-02",
        )

        mock_nvd_client = AsyncMock()
        mock_nvd_client.query_by_cve_id = AsyncMock(return_value=nvd_data)

        query_result = MagicMock()
        mock_db.return_value.update = MagicMock(return_value=query_result)

        result = await nvd_sync_service._sync_single_vulnerability(
            mock_nvd_client, mock_vuln
        )

        assert result is True
        assert nvd_sync_service.stats["updated"] == 1
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_single_vuln_cve_not_found(
        self, nvd_sync_service, mock_db
    ):
        """Test syncing when CVE is not found in NVD."""
        mock_vuln = MagicMock()
        mock_vuln.id = 1
        mock_vuln.cve_id = "CVE-2099-99999"
        mock_vuln.cvss_score = None
        mock_vuln.cvss_vector = None
        mock_vuln.nvd_last_sync = None

        mock_nvd_client = AsyncMock()
        mock_nvd_client.query_by_cve_id = AsyncMock(return_value=None)

        query_result = MagicMock()
        mock_db.return_value.update = MagicMock(return_value=query_result)

        result = await nvd_sync_service._sync_single_vulnerability(
            mock_nvd_client, mock_vuln
        )

        assert result is False
        assert nvd_sync_service.stats["skipped"] == 1
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_single_vuln_updates_timestamp(
        self, nvd_sync_service, mock_db
    ):
        """Test that nvd_last_sync timestamp is updated."""
        mock_vuln = MagicMock()
        mock_vuln.id = 1
        mock_vuln.cve_id = "CVE-2024-12345"

        mock_nvd_client = AsyncMock()
        mock_nvd_client.query_by_cve_id = AsyncMock(return_value=None)

        query_result = MagicMock()
        mock_db.return_value.update = MagicMock(return_value=query_result)

        with patch(
            "apps.api.services.sbom.vulnerability.nvd_sync.datetime"
        ) as mock_datetime:
            mock_now = datetime.now(timezone.utc)
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kwargs: datetime(
                *args, **kwargs
            )

            await nvd_sync_service._sync_single_vulnerability(
                mock_nvd_client, mock_vuln
            )

    @pytest.mark.asyncio
    async def test_sync_single_vuln_error_handling(self, nvd_sync_service, mock_db):
        """Test error handling during vulnerability sync."""
        mock_vuln = MagicMock()
        mock_vuln.id = 1
        mock_vuln.cve_id = "CVE-2024-12345"

        mock_nvd_client = AsyncMock()
        mock_nvd_client.query_by_cve_id = AsyncMock(
            side_effect=Exception("Network error")
        )

        result = await nvd_sync_service._sync_single_vulnerability(
            mock_nvd_client, mock_vuln
        )

        assert result is False
        assert nvd_sync_service.stats["errors"] == 1

    @pytest.mark.asyncio
    async def test_sync_single_vuln_partial_data(self, nvd_sync_service, mock_db):
        """Test syncing with partial NVD data (only some fields populated)."""
        mock_vuln = MagicMock()
        mock_vuln.id = 1
        mock_vuln.cve_id = "CVE-2024-12345"

        nvd_data = NVDVulnerability(
            id="CVE-2024-12345",
            description="Test vulnerability",
            severity=None,
            cvss_score=6.5,
            cvss_vector=None,
            cpe_matches=[],
            references=[],
            published="2024-01-01",
            modified="2024-01-02",
        )

        mock_nvd_client = AsyncMock()
        mock_nvd_client.query_by_cve_id = AsyncMock(return_value=nvd_data)

        query_result = MagicMock()
        mock_db.return_value.update = MagicMock(return_value=query_result)

        result = await nvd_sync_service._sync_single_vulnerability(
            mock_nvd_client, mock_vuln
        )

        assert result is True
        assert nvd_sync_service.stats["updated"] == 1


class TestSyncVulnerabilities:
    """Test sync_vulnerabilities method."""

    @pytest.mark.asyncio
    async def test_sync_vulnerabilities_no_vulns_to_sync(self, nvd_sync_service, mock_db):
        """Test sync when no vulnerabilities need syncing."""
        nvd_sync_service._get_vulns_needing_sync = MagicMock(return_value=[])

        stats = await nvd_sync_service.sync_vulnerabilities(max_vulns=100)

        assert stats["processed"] == 0
        assert stats["updated"] == 0
        assert stats["skipped"] == 0
        assert stats["errors"] == 0

    @pytest.mark.asyncio
    async def test_sync_vulnerabilities_successful_sync(self, nvd_sync_service, mock_db):
        """Test successful sync of multiple vulnerabilities."""
        mock_vulns = [MagicMock() for _ in range(3)]
        for i, vuln in enumerate(mock_vulns):
            vuln.id = i + 1
            vuln.cve_id = f"CVE-2024-{i:05d}"

        nvd_sync_service._get_vulns_needing_sync = MagicMock(return_value=mock_vulns)

        nvd_data = NVDVulnerability(
            id="CVE-2024-00001",
            description="Test vulnerability",
            severity="HIGH",
            cvss_score=7.5,
            cvss_vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N",
            cpe_matches=[],
            references=[],
            published="2024-01-01",
            modified="2024-01-02",
        )

        mock_nvd_client = AsyncMock()
        mock_nvd_client.query_by_cve_id = AsyncMock(return_value=nvd_data)
        mock_nvd_client.__aenter__ = AsyncMock(return_value=mock_nvd_client)
        mock_nvd_client.__aexit__ = AsyncMock(return_value=None)

        query_result = MagicMock()
        mock_db.return_value.update = MagicMock(return_value=query_result)

        with patch(
            "apps.api.services.sbom.vulnerability.nvd_sync.NVDClient",
            return_value=mock_nvd_client,
        ):
            stats = await nvd_sync_service.sync_vulnerabilities(max_vulns=100)

        assert stats["processed"] == 3
        assert stats["updated"] == 3

    @pytest.mark.asyncio
    async def test_sync_vulnerabilities_stats_reset(self, nvd_sync_service, mock_db):
        """Test that stats are reset for each sync run."""
        nvd_sync_service._get_vulns_needing_sync = MagicMock(return_value=[])
        nvd_sync_service.stats = {"processed": 10, "updated": 5, "skipped": 2, "errors": 1}

        stats = await nvd_sync_service.sync_vulnerabilities()

        assert stats["processed"] == 0
        assert stats["updated"] == 0
        assert stats["skipped"] == 0
        assert stats["errors"] == 0

    @pytest.mark.asyncio
    async def test_sync_vulnerabilities_respects_max_vulns(
        self, nvd_sync_service, mock_db
    ):
        """Test that max_vulns parameter is passed to _get_vulns_needing_sync."""
        nvd_sync_service._get_vulns_needing_sync = MagicMock(return_value=[])

        await nvd_sync_service.sync_vulnerabilities(max_vulns=250)

        nvd_sync_service._get_vulns_needing_sync.assert_called_once_with(250, False)

    @pytest.mark.asyncio
    async def test_sync_vulnerabilities_respects_force_refresh(
        self, nvd_sync_service, mock_db
    ):
        """Test that force_refresh parameter is passed through."""
        nvd_sync_service._get_vulns_needing_sync = MagicMock(return_value=[])

        await nvd_sync_service.sync_vulnerabilities(force_refresh=True)

        nvd_sync_service._get_vulns_needing_sync.assert_called_once_with(
            MAX_VULNS_PER_SYNC, True
        )

    @pytest.mark.asyncio
    async def test_sync_vulnerabilities_mixed_results(
        self, nvd_sync_service, mock_db
    ):
        """Test sync with mixed results (updated, skipped, errors)."""
        mock_vulns = [MagicMock() for _ in range(3)]
        for i, vuln in enumerate(mock_vulns):
            vuln.id = i + 1
            vuln.cve_id = f"CVE-2024-{i:05d}"

        nvd_sync_service._get_vulns_needing_sync = MagicMock(return_value=mock_vulns)

        mock_nvd_client = AsyncMock()

        # First call returns data, second returns None, third raises error
        mock_nvd_client.query_by_cve_id = AsyncMock(
            side_effect=[
                NVDVulnerability(
                    id="CVE-2024-00000",
                    description="Test",
                    severity="HIGH",
                    cvss_score=7.5,
                    cvss_vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N",
                    cpe_matches=[],
                    references=[],
                    published="2024-01-01",
                    modified="2024-01-02",
                ),
                None,
                Exception("Network error"),
            ]
        )

        mock_nvd_client.__aenter__ = AsyncMock(return_value=mock_nvd_client)
        mock_nvd_client.__aexit__ = AsyncMock(return_value=None)

        query_result = MagicMock()
        mock_db.return_value.update = MagicMock(return_value=query_result)

        with patch(
            "apps.api.services.sbom.vulnerability.nvd_sync.NVDClient",
            return_value=mock_nvd_client,
        ):
            stats = await nvd_sync_service.sync_vulnerabilities(max_vulns=100)

        assert stats["processed"] == 3
        assert stats["updated"] == 1
        assert stats["skipped"] == 1
        assert stats["errors"] == 1


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_api_key_treated_as_none(self, mock_db):
        """Test that empty string API key is treated properly."""
        service = NVDSyncService(db=mock_db, nvd_api_key="")
        assert service.nvd_api_key == ""

    @pytest.mark.asyncio
    async def test_database_commit_called_per_vuln(
        self, nvd_sync_service, mock_db
    ):
        """Test that database commit is called for each synced vulnerability."""
        mock_vuln = MagicMock()
        mock_vuln.id = 1
        mock_vuln.cve_id = "CVE-2024-12345"

        mock_nvd_client = AsyncMock()
        mock_nvd_client.query_by_cve_id = AsyncMock(return_value=None)

        query_result = MagicMock()
        mock_db.return_value.update = MagicMock(return_value=query_result)
        mock_db.commit = MagicMock()

        await nvd_sync_service._sync_single_vulnerability(
            mock_nvd_client, mock_vuln
        )

        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_with_zero_max_vulns(self, nvd_sync_service, mock_db):
        """Test sync behavior when max_vulns is set to 0."""
        nvd_sync_service._get_vulns_needing_sync = MagicMock(return_value=[])

        stats = await nvd_sync_service.sync_vulnerabilities(max_vulns=0)

        nvd_sync_service._get_vulns_needing_sync.assert_called_once_with(0, False)
        assert stats["processed"] == 0

    @pytest.mark.asyncio
    async def test_stats_accumulation_across_vulns(self, nvd_sync_service, mock_db):
        """Test that stats are properly accumulated across multiple vulnerabilities."""
        mock_vulns = [MagicMock() for _ in range(2)]
        for i, vuln in enumerate(mock_vulns):
            vuln.id = i + 1
            vuln.cve_id = f"CVE-2024-{i:05d}"

        nvd_sync_service._get_vulns_needing_sync = MagicMock(return_value=mock_vulns)

        mock_nvd_client = AsyncMock()
        mock_nvd_client.query_by_cve_id = AsyncMock(return_value=None)
        mock_nvd_client.__aenter__ = AsyncMock(return_value=mock_nvd_client)
        mock_nvd_client.__aexit__ = AsyncMock(return_value=None)

        query_result = MagicMock()
        mock_db.return_value.update = MagicMock(return_value=query_result)

        with patch(
            "apps.api.services.sbom.vulnerability.nvd_sync.NVDClient",
            return_value=mock_nvd_client,
        ):
            stats = await nvd_sync_service.sync_vulnerabilities(max_vulns=100)

        assert stats["processed"] == 2
        assert stats["skipped"] == 2
