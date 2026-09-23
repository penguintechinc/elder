"""Unit tests for the gh-237 tenant-scoping helper and its regression gate.

`apps.api.utils.tenant_scoping.get_tenant_scoped()` is the shared
replacement for a bare `db.<table>[<id>]` bracket lookup. This module also
proves `scripts/check_tenant_scoping.py` (wired into `make lint`) actually
catches a newly introduced unscoped lookup rather than always reporting
clean -- a gate that cannot fail is not a gate.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from quart import g

from apps.api.utils.tenant_scoping import get_current_tenant_id, get_tenant_scoped

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNER = REPO_ROOT / "scripts" / "check_tenant_scoping.py"


def _run_scanner(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCANNER), "--root", str(root)],
        capture_output=True,
        text=True,
    )


class TestGetCurrentTenantId:
    """get_current_tenant_id() reads g.claims -- requires an app context."""

    @pytest.mark.asyncio
    async def test_missing_claims_returns_none(self, app):
        """No g.claims at all (unauthenticated request) -> None, not 0."""
        async with app.app_context():
            assert get_current_tenant_id() is None

    @pytest.mark.asyncio
    async def test_empty_tenant_claim_returns_none(self, app):
        async with app.app_context():
            g.claims = {"tenant": ""}
            assert get_current_tenant_id() is None

    @pytest.mark.asyncio
    async def test_non_numeric_tenant_claim_returns_none(self, app):
        async with app.app_context():
            g.claims = {"tenant": "not-a-number"}
            assert get_current_tenant_id() is None

    @pytest.mark.asyncio
    async def test_valid_tenant_claim_returns_int(self, app):
        async with app.app_context():
            g.claims = {"tenant": "42"}
            assert get_current_tenant_id() == 42


class TestGetTenantScopedDirectMode:
    """Direct mode: table carries its own tenant_id column."""

    def _make_db(self, rows):
        """MagicMock db where db(query).select().first() returns rows[0] or None."""
        db = MagicMock()
        select_result = MagicMock()
        select_result.first.return_value = rows[0] if rows else None
        db.return_value.select.return_value = select_result
        table = MagicMock()
        table.id = "id_field"
        table.tenant_id = "tenant_id_field"
        return db, table

    def test_none_tenant_id_returns_none_without_querying(self):
        db, table = self._make_db([MagicMock()])
        assert get_tenant_scoped(db, table, record_id=1, tenant_id=None) is None
        db.assert_not_called()

    def test_falsy_record_id_returns_none(self):
        db, table = self._make_db([MagicMock()])
        assert get_tenant_scoped(db, table, record_id=0, tenant_id=1) is None

    def test_matching_row_returned(self):
        row = MagicMock()
        db, table = self._make_db([row])
        result = get_tenant_scoped(db, table, record_id=5, tenant_id=1)
        assert result is row

    def test_no_matching_row_returns_none(self):
        db, table = self._make_db([])
        assert get_tenant_scoped(db, table, record_id=5, tenant_id=1) is None


class TestGetTenantScopedJoinedMode:
    """Joined mode (org_fk=...): table has no tenant_id of its own."""

    def test_row_not_found_returns_none(self):
        db = MagicMock()
        table = MagicMock()
        table.__getitem__.return_value = None
        assert (
            get_tenant_scoped(db, table, 5, tenant_id=1, org_fk="organization_id")
            is None
        )

    def test_row_missing_org_fk_returns_none(self):
        db = MagicMock()
        row = MagicMock(organization_id=None)
        table = MagicMock()
        table.__getitem__.return_value = row
        assert (
            get_tenant_scoped(db, table, 5, tenant_id=1, org_fk="organization_id")
            is None
        )

    def test_org_not_owned_by_tenant_returns_none(self):
        db = MagicMock()
        row = MagicMock(organization_id=9)
        table = MagicMock()
        table.__getitem__.return_value = row
        select_result = MagicMock()
        select_result.first.return_value = None  # org query finds nothing
        db.return_value.select.return_value = select_result
        assert (
            get_tenant_scoped(db, table, 5, tenant_id=1, org_fk="organization_id")
            is None
        )

    def test_org_owned_by_tenant_returns_row(self):
        db = MagicMock()
        row = MagicMock(organization_id=9)
        table = MagicMock()
        table.__getitem__.return_value = row
        select_result = MagicMock()
        select_result.first.return_value = MagicMock()  # org exists for tenant
        db.return_value.select.return_value = select_result
        result = get_tenant_scoped(db, table, 5, tenant_id=1, org_fk="organization_id")
        assert result is row


class TestTenantScopingGateCatchesRegressions:
    """Prove the scanner backing `make lint`'s gate actually fails on a
    newly introduced unscoped lookup, and passes once it's fixed --
    regression: gh-237. A gate that cannot fail is not a gate.
    """

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_scanner_runs_clean_on_the_real_repo(self):
        """Sanity check the checked-in baseline actually matches the repo
        as it stands today (proves a non-zero denominator was examined)."""
        result = _run_scanner(REPO_ROOT)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "unscoped bracket lookups" in result.stdout
        assert "0 files examined" not in result.stdout, "scan found no files"

    def test_scanner_fails_on_new_unscoped_lookup(self, tmp_path):
        baseline = tmp_path / ".tenant-scoping-baseline"
        baseline.write_text("count=0\n")
        route_file = tmp_path / "apps/api/modules/fake/routes/thing.py"
        self._write(
            route_file,
            "def get_thing(thing_id):\n    thing = db.things[thing_id]\n    return thing\n",
        )

        failing = _run_scanner(tmp_path)
        assert failing.returncode == 1, failing.stdout + failing.stderr
        assert "REGRESSION" in failing.stderr

        # Fix it: replace the bare bracket lookup with the shared helper.
        self._write(
            route_file,
            "from apps.api.utils.tenant_scoping import get_tenant_scoped\n\n\n"
            "def get_thing(thing_id, tenant_id):\n"
            "    thing = get_tenant_scoped(db, db.things, thing_id, tenant_id)\n"
            "    return thing\n",
        )
        passing = _run_scanner(tmp_path)
        assert passing.returncode == 0, passing.stdout + passing.stderr

    def test_scanner_refuses_to_report_clean_with_no_files(self, tmp_path):
        (tmp_path / ".tenant-scoping-baseline").write_text("count=0\n")
        result = _run_scanner(tmp_path)
        assert result.returncode == 1
        assert "pointed at nothing" in result.stdout + result.stderr


class TestGh237ReportedSitesFixed:
    """The two sites originally reported in gh-237 must no longer use a
    bare bracket lookup. Checks non-comment lines for the exact original
    buggy line text (not a whole-file bracket-lookup sweep) -- both files
    have OTHER pre-existing unscoped lookups elsewhere that are part of the
    separate, larger gh-237 sweep and intentionally out of scope for this
    fix (e.g. graph.py's find_path/_get_entity_subgraph helpers).
    """

    @staticmethod
    def _code_lines(path: Path) -> list[str]:
        return [
            line
            for line in path.read_text().splitlines()
            if not line.strip().startswith("#")
        ]

    def test_organization_tree_no_longer_bare_lookup(self):
        path = REPO_ROOT / "apps/api/modules/infrastructure/routes/organization_tree.py"
        code = "\n".join(self._code_lines(path))
        assert "db.organizations[org_id]" not in code
        assert "get_tenant_scoped(" in code

    def test_graph_no_longer_bare_lookup(self):
        path = REPO_ROOT / "apps/api/modules/infrastructure/routes/graph.py"
        code = "\n".join(self._code_lines(path))
        assert "entity = db.entities[entity_id]" not in code
        assert "get_tenant_scoped(" in code
