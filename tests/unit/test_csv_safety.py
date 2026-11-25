"""Tests for CSV formula-injection hardening (shared/csv_safety.py) and its writers."""

from __future__ import annotations

import csv
import io
import os
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from shared.csv_safety import (
    neutralize_cell,
    neutralize_mapping,
    safe_dict_writer,
    safe_writer,
)

PAYLOADS = [
    "=1+1",
    '=HYPERLINK("http://evil","x")',
    "+1+1",
    "-2+3",
    "@SUM(A1:A2)",
    "\t=1+1",
    "\r=1+1",
    "\tcmd",
    "\rcmd",
    " =1+1",
    "   @SUM(1)",
    "\n-1+1",
    "=cmd|' /C calc'!A0",
    '=1,"2";3',
]

SAFE_STRINGS = ["hello", "", "a=b", "1-2", "foo@bar.com", "'already", "10.0.0.1"]


def _parse(text: str) -> list[list[str]]:
    """Parse CSV text back to rows."""
    return list(csv.reader(io.StringIO(text)))


class TestNeutralizeCell:
    """Unit tests for the cell neutraliser."""

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_payloads_prefixed(self, payload):
        """Every formula trigger (incl. after leading whitespace) gets a quote prefix."""
        assert neutralize_cell(payload) == "'" + payload

    @pytest.mark.parametrize("value", SAFE_STRINGS)
    def test_safe_strings_unchanged(self, value):
        """Benign strings pass through untouched."""
        assert neutralize_cell(value) == value

    @pytest.mark.parametrize(
        "value", [-5, -3.25, 0, 42, Decimal("-1.50"), True, False, None]
    )
    def test_numbers_and_none_pass_through(self, value):
        """Numeric-typed values (incl. negatives) and None are not altered."""
        assert neutralize_cell(value) is value

    def test_non_string_objects_stringified(self):
        """Arbitrary objects are stringified before checking."""

        class Weird:
            def __str__(self) -> str:
                return "=evil()"

        assert neutralize_cell(Weird()) == "'=evil()"

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_writer_round_trip_quotes_properly(self, payload):
        """Written CSV parses back to exactly one neutralised cell, with -1 numeric kept."""
        buf = io.StringIO()
        safe_writer(buf).writerow([payload, -1, 'x,y"z'])
        rows = _parse(buf.getvalue())
        assert rows == [["'" + payload, "-1", 'x,y"z']]

    def test_dict_writer_neutralises_headers_and_values(self):
        """DictWriter wrapper neutralises both header and values; numbers kept."""
        buf = io.StringIO()
        w = safe_dict_writer(buf, fieldnames=["name", "=hdr", "amount"])
        w.writeheader()
        w.writerows([{"name": "@x", "=hdr": "ok", "amount": -12.5}])
        assert _parse(buf.getvalue()) == [
            ["name", "'=hdr", "amount"],
            ["'@x", "ok", "-12.5"],
        ]

    def test_neutralize_mapping(self):
        """Mapping helper neutralises values only."""
        assert neutralize_mapping({"a": "+1", "b": -1}) == {"a": "'+1", "b": -1}


class TestExportPaths:
    """Each CSV export path neutralises a malicious cell."""

    def test_backup_csv_export(self, tmp_path):
        """BackupService.export_data(csv) neutralises string cells, keeps numbers."""
        from apps.api.services.backup.service import BackupService

        row = MagicMock()
        row.as_dict.return_value = {"id": 7, "name": "=1+1", "score": -3}
        db = MagicMock()
        db.entities.id = 0
        db.return_value.select.return_value = [row]
        svc = BackupService.__new__(BackupService)
        svc.db = db
        with patch(
            "apps.api.services.backup.service.tempfile.gettempdir",
            return_value=str(tmp_path),
        ):
            try:
                svc.export_data("csv", ["entity"])
            except FileNotFoundError:
                pass  # pre-existing: csv mode writes per-resource files only
        files = [f for f in os.listdir(tmp_path) if f.startswith("export_entities_")]
        assert files, "csv export file not written"
        rows = _parse((tmp_path / files[0]).read_text())
        assert rows == [["id", "name", "score"], ["7", "'=1+1", "-3"]]

    @pytest.mark.asyncio
    async def test_audit_log_csv_export(self, app):
        """Audit-log CSV export neutralises attacker-controlled fields."""
        from apps.api.api.v1 import audit_enterprise

        logs = {
            "logs": [
                {
                    "id": 1,
                    "action": '=HYPERLINK("http://x")',
                    "resource_type": "@entity",
                    "resource_id": -1,
                    "success": True,
                    "ip_address": "+1.2.3.4",
                    "created_at": "2026-01-01",
                }
            ]
        }
        view = audit_enterprise.export_logs.__wrapped__
        with patch.object(
            audit_enterprise.AuditService, "query_logs", return_value=logs
        ):
            async with app.test_request_context(
                "/api/v1/audit-enterprise/export?format=csv&tenant_id=1"
            ):
                from quart import request

                request.portal_user = {"global_role": "admin", "tenant_id": 1}
                body, status, _headers = view()
        assert status == 200
        rows = _parse(body)
        assert rows[1] == [
            "1",
            '\'=HYPERLINK("http://x")',
            "'@entity",
            "-1",
            "True",
            "'+1.2.3.4",
            "2026-01-01",
        ]

    @pytest.mark.asyncio
    async def test_template_download_uses_safe_writer(self, app):
        """CSV template download neutralises any formula-like example cell."""
        from apps.api.api.v1 import csv_import

        spec = {"headers": ["name", "notes"], "example": ["svc", "=1+1"]}
        view = csv_import.download_csv_template
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__
        with patch.dict(csv_import.CSV_TEMPLATES, {"evil": spec}):
            async with app.test_request_context(
                "/api/v1/import/templates/evil/download"
            ):
                resp = view("evil")
                if hasattr(resp, "__await__"):
                    resp = await resp
                body = await resp.get_data(as_text=True)
        assert _parse(body) == [["name", "notes"], ["svc", "'=1+1"]]


class TestImportExportRoundTrip:
    """Imported values are stored raw and neutralised on export."""

    @pytest.mark.asyncio
    async def test_import_then_export(self, app, tmp_path):
        """A malicious entity name imported via CSV is stored raw, exported neutralised."""
        from datetime import UTC, datetime
        from uuid import uuid4

        from quart import current_app

        from apps.api.services.backup.service import BackupService
        from apps.api.services.csv_import.processor import process_csv_text

        tag = uuid4().hex[:6]
        payload = f"=cmd|' /C calc'!A0 {tag}"
        benign = f"a=b-c@{tag}"
        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            org_id = db.organizations.insert(
                tenant_id=1,
                name=f"Org-{tag}",
                slug=f"org-{uuid4().hex[:8]}",
                created_at=now,
                updated_at=now,
            )
            db.commit()

            def _csv(name: str) -> str:
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(["name", "type", "organization_id", "sub_type", "status"])
                w.writerow([name, "system", org_id, "", "active"])
                return buf.getvalue()

            # Importer rejects formula payloads (defence in depth) ...
            rejected = process_csv_text(db, 1, "entities", _csv("  " + payload))
            assert rejected.errors and rejected.imported_count == 0
            # ... and imports benign values containing trigger chars mid-string.
            ok = process_csv_text(db, 1, "entities", _csv(benign))
            assert not ok.errors
            # A payload stored by another route is kept raw in the DB.
            db.entities.insert(
                organization_id=org_id,
                name=payload,
                type="system",
                status="active",
                is_managed=False,
                tags=[],
                created_at=now,
                updated_at=now,
            )
            db.commit()
            stored = db(db.entities.name == payload).select().first()
            assert stored is not None and stored.name == payload

            with patch(
                "apps.api.services.backup.service.tempfile.gettempdir",
                return_value=str(tmp_path),
            ):
                try:
                    BackupService(db).export_data("csv", ["entity"])
                except FileNotFoundError:
                    pass  # pre-existing: csv mode writes per-resource files only
        files = [f for f in os.listdir(tmp_path) if f.startswith("export_entities_")]
        assert files
        rows = _parse((tmp_path / files[0]).read_text())
        header = rows[0]
        names = [r[header.index("name")] for r in rows[1:]]
        ids = [r[header.index("id")] for r in rows[1:]]
        assert "'" + payload in names
        assert payload not in names
        assert benign in names
        assert all(not i.startswith("'") for i in ids)
