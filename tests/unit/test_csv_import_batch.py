"""Unit tests for S3-backed async batch CSV import pipeline, routes, worker, and migration 049."""

from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import sqlalchemy as sa
from penguin_dal import DAL
from quart import Quart, g
from quart.datastructures import FileStorage

from alembic.migration import MigrationContext
from alembic.operations import Operations
from apps.api.api.v1 import csv_import as csv_import_routes
from apps.api.models.base import Base
from apps.api.models.csv_import import CsvImportJob  # noqa: F401
from apps.api.models.identity import Identity  # noqa: F401
from apps.api.models.security import Vulnerability  # noqa: F401
from apps.api.models.tenant import Tenant  # noqa: F401
from apps.api.modules.infrastructure.models.organization import (  # noqa: F401
    Organization,
)
from apps.api.services.csv_import.storage import (
    CSV_IMPORT_QUEUE_KEY,
    CsvImportStorage,
    build_csv_import_key,
    compute_csv_sha256,
)
from apps.worker.csv_import_batch import (
    claim_job_by_id,
    claim_pending_jobs,
    process_claimed_job,
    process_job_by_id,
    reset_unconfigured_log_state,
    run_batch_once,
    run_once,
)
from apps.worker.csv_import_batch import (
    main as csv_import_batch_main,
)


class _InMemoryS3Client:
    """Minimal in-memory boto3 S3 client double for upload/download/delete tests."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.metadata: dict[tuple[str, str], dict[str, str]] = {}
        self.deleted_keys: list[str] = []

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str = "",
        Metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.objects[(Bucket, Key)] = bytes(Body)
        self.metadata[(Bucket, Key)] = dict(Metadata or {})
        return {"ETag": "test-etag"}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        if (Bucket, Key) not in self.objects:
            raise KeyError(f"Missing S3 object: {Bucket}/{Key}")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.objects.pop((Bucket, Key), None)
        self.deleted_keys.append(Key)
        return {}


@pytest.fixture
def sqlite_db(tmp_path: Path):
    """Create a fresh SQLite DB with core + csv_import_jobs + vulnerabilities tables."""
    db_file = tmp_path / "csv_import_batch.db"
    db_url = f"sqlite:///{db_file}"
    engine = sa.create_engine(db_url)
    tables_to_create = [
        Base.metadata.tables["tenants"],
        Base.metadata.tables["organizations"],
        Base.metadata.tables["identity_groups"],
        Base.metadata.tables["identities"],
        Base.metadata.tables["vulnerabilities"],
        Base.metadata.tables["csv_import_jobs"],
    ]
    Base.metadata.create_all(engine, tables=tables_to_create)
    engine.dispose()

    db = DAL(db_url, pool_size=1, migrate=False)
    now = datetime.now(UTC)
    db.tenants.insert(
        id=1,
        name="Tenant One",
        slug="tenant-one",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.tenants.insert(
        id=2,
        name="Tenant Two",
        slug="tenant-two",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.identities.insert(
        id=10,
        tenant_id=1,
        username="importer",
        email="importer@example.com",
        identity_type="human",
        auth_provider="local",
        portal_role="admin",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    yield db
    db.close()


def _make_test_app(
    db: Any,
    *,
    tenant_id: int = 1,
    scopes: list[str] | None = None,
    testing: bool = False,
) -> Quart:
    app = Quart(__name__)
    app.config["TESTING"] = testing
    app.db = db
    active_scopes = (
        scopes if scopes is not None else ["sbom:write", "infrastructure:write"]
    )

    @app.before_request
    async def _inject_claims() -> None:
        g.claims = {"tenant": str(tenant_id), "scope": active_scopes}

    app.register_blueprint(csv_import_routes.bp, url_prefix="/api/v1/import")
    return app


class TestCsvImportStorage:
    """Verify S3 upload/download, SHA-256 verification, delete, and env fallback."""

    def test_build_key_and_upload_download_delete(self) -> None:
        fake_client = _InMemoryS3Client()
        storage = CsvImportStorage(
            bucket="elder-imports",
            endpoint_url="http://minio.storage:9000",
            region="us-east-1",
            prefix="/csv-imports/",
            s3_client=fake_client,
        )
        assert storage.is_configured
        assert storage.path_style is True

        csv_text = "cve_id,title,severity,cvss_score,description,published_at\nCVE-2026-1001,Test,high,7.5,Desc,2026-10-01\n"
        expected_sha = compute_csv_sha256(csv_text)

        stored = storage.put_csv(
            tenant_id=1,
            resource="vulnerabilities",
            job_id="csvimp-abc123",
            csv_text=csv_text,
        )
        assert stored.storage_backend == "s3"
        assert (
            stored.storage_key
            == "csv-imports/tenant-1/vulnerabilities/csvimp-abc123.csv"
        )
        assert stored.file_sha256 == expected_sha
        assert stored.file_size_bytes == len(csv_text.encode("utf-8"))

        downloaded = storage.get_csv(stored.storage_key, expected_sha256=expected_sha)
        assert downloaded == csv_text

        storage.delete_csv(stored.storage_key)
        assert stored.storage_key in fake_client.deleted_keys
        assert ("elder-imports", stored.storage_key) not in fake_client.objects

    def test_get_csv_fails_loudly_on_sha256_tamper(self) -> None:
        fake_client = _InMemoryS3Client()
        key = build_csv_import_key(
            prefix="csv-imports",
            tenant_id=1,
            resource="vulnerabilities",
            job_id="csvimp-tamper",
        )
        fake_client.put_object(
            Bucket="elder-imports",
            Key=key,
            Body=b"tampered,content\n1,2\n",
        )
        storage = CsvImportStorage(bucket="elder-imports", s3_client=fake_client)

        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            storage.get_csv(key, expected_sha256="0" * 64)

    def test_from_env_fallback_chain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "CSV_IMPORT_S3_BUCKET",
            "ELDER_S3_BUCKET",
            "RUNS_S3_BUCKET",
            "CSV_IMPORT_S3_ENDPOINT",
            "ELDER_S3_ENDPOINT",
            "RUNS_S3_ENDPOINT",
            "CSV_IMPORT_S3_PATH_STYLE",
        ):
            monkeypatch.delenv(var, raising=False)

        # Fallback to RUNS_S3_BUCKET
        monkeypatch.setenv("RUNS_S3_BUCKET", "runs-bucket")
        monkeypatch.setenv("RUNS_S3_ENDPOINT", "http://minio:9000")
        s1 = CsvImportStorage.from_env()
        assert s1.bucket == "runs-bucket"
        assert s1.endpoint_url == "http://minio:9000"
        assert s1.prefix == "csv-imports"
        assert s1.path_style is True

        # ELDER_S3_BUCKET overrides RUNS_S3_BUCKET
        monkeypatch.setenv("ELDER_S3_BUCKET", "elder-bucket")
        assert CsvImportStorage.from_env().bucket == "elder-bucket"

        # CSV_IMPORT_S3_BUCKET overrides ELDER_S3_BUCKET
        monkeypatch.setenv("CSV_IMPORT_S3_BUCKET", "csv-bucket")
        assert CsvImportStorage.from_env().bucket == "csv-bucket"


class TestCsvImportRoutesAsyncAndJobs:
    """Verify 202 queueing, 503 when S3 unconfigured, and GET /jobs endpoints."""

    @pytest.mark.asyncio
    async def test_post_queues_to_s3_returns_202_and_jobs_endpoints(
        self, sqlite_db: Any
    ) -> None:
        fake_client = _InMemoryS3Client()
        storage = CsvImportStorage(bucket="elder-imports", s3_client=fake_client)
        app = _make_test_app(sqlite_db, tenant_id=1, testing=False)
        mock_user = MagicMock(id=10, is_superuser=False, username="importer")

        cve1 = f"CVE-2026-{uuid4().hex[:6].upper()}"
        cve2 = f"CVE-2026-{uuid4().hex[:6].upper()}"
        csv_bytes = (
            "cve_id,title,severity,cvss_score,description,published_at\n"
            f"{cve1},First vuln,high,8.1,First desc,2026-10-01\n"
            f"{cve2},Second vuln,low,3.2,Second desc,2026-10-02\n"
        ).encode("utf-8")

        with (
            patch("apps.api.auth.decorators.get_current_user", return_value=mock_user),
            patch(
                "apps.api.api.v1.csv_import.get_csv_import_storage",
                return_value=storage,
            ),
        ):
            client = app.test_client()
            res = await client.post(
                "/api/v1/import/vulnerabilities",
                files={
                    "file": FileStorage(
                        stream=io.BytesIO(csv_bytes),
                        filename="vulns.csv",
                        content_type="text/csv",
                    )
                },
            )
            assert res.status_code == 202
            body = await res.get_json()
            assert body["status"] == "pending"
            assert body["resource"] == "vulnerabilities"
            assert body["storage_backend"] == "s3"
            assert body["total_rows"] == 2
            assert body["file_sha256"] == compute_csv_sha256(csv_bytes)
            assert body["job_id"].startswith("csvimp-")
            assert ("elder-imports", body["storage_key"]) in fake_client.objects

            # GET /api/v1/import/jobs with filters
            list_res = await client.get(
                "/api/v1/import/jobs?resource=vulnerabilities&status=pending&page=1&per_page=10"
            )
            assert list_res.status_code == 200
            list_body = await list_res.get_json()
            assert list_body["total"] == 1
            assert list_body["items"][0]["job_id"] == body["job_id"]
            assert list_body["items"][0]["filename"] == "vulns.csv"

            # Filter mismatch returns empty list
            empty_res = await client.get("/api/v1/import/jobs?status=completed")
            assert empty_res.status_code == 200
            assert (await empty_res.get_json())["total"] == 0

            # GET /api/v1/import/jobs/<job_id>
            detail_res = await client.get(f"/api/v1/import/jobs/{body['job_id']}")
            assert detail_res.status_code == 200
            detail = await detail_res.get_json()
            assert detail["job_id"] == body["job_id"]
            assert detail["created_by_identity_id"] == 10

            # Cross-tenant isolation: tenant 2 gets 404 on tenant 1's job
            app_t2 = _make_test_app(sqlite_db, tenant_id=2, testing=False)
            t2_res = await app_t2.test_client().get(
                f"/api/v1/import/jobs/{body['job_id']}"
            )
            assert t2_res.status_code == 404

    @pytest.mark.asyncio
    async def test_post_fails_loudly_with_503_when_s3_unconfigured(
        self, sqlite_db: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CSV_IMPORT_ALLOW_SYNC_FALLBACK", raising=False)
        unconfigured = CsvImportStorage(bucket="")
        app = _make_test_app(sqlite_db, tenant_id=1, testing=False)
        mock_user = MagicMock(id=10, is_superuser=False, username="importer")

        with (
            patch("apps.api.auth.decorators.get_current_user", return_value=mock_user),
            patch(
                "apps.api.api.v1.csv_import.get_csv_import_storage",
                return_value=unconfigured,
            ),
        ):
            res = await app.test_client().post(
                "/api/v1/import/vulnerabilities",
                json={
                    "csv_text": (
                        "cve_id,title,severity,cvss_score,description,published_at\n"
                        "CVE-2026-5030,Unconfigured S3,high,7.0,Desc,2026-10-01\n"
                    )
                },
            )
            assert res.status_code == 503
            body = await res.get_json()
            assert "CSV_IMPORT_S3_BUCKET" in body["error"]

    @pytest.mark.asyncio
    async def test_post_sync_fallback_in_testing_mode(
        self, sqlite_db: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CSV_IMPORT_ALLOW_SYNC_FALLBACK", raising=False)
        unconfigured = CsvImportStorage(bucket="")
        app = _make_test_app(sqlite_db, tenant_id=1, testing=True)
        mock_user = MagicMock(id=10, is_superuser=False, username="importer")
        cve_sync = f"CVE-2026-{uuid4().hex[:6].upper()}"

        with (
            patch("apps.api.auth.decorators.get_current_user", return_value=mock_user),
            patch(
                "apps.api.api.v1.csv_import.get_csv_import_storage",
                return_value=unconfigured,
            ),
        ):
            res = await app.test_client().post(
                "/api/v1/import/vulnerabilities",
                json={
                    "csv_text": (
                        "cve_id,title,severity,cvss_score,description,published_at\n"
                        f"{cve_sync},Sync Fallback Vuln,medium,5.5,Desc,2026-10-01\n"
                    )
                },
            )
            assert res.status_code == 201
            body = await res.get_json()
            assert body == {"resource": "vulnerabilities", "imported": 1}

    @pytest.mark.asyncio
    async def test_post_pushes_job_id_to_valkey_queue_and_tolerates_redis_failure(
        self, sqlite_db: Any
    ) -> None:
        fake_client = _InMemoryS3Client()
        storage = CsvImportStorage(bucket="elder-imports", s3_client=fake_client)
        app = _make_test_app(sqlite_db, tenant_id=1, testing=False)
        mock_redis = MagicMock()
        app.redis_client = mock_redis
        mock_user = MagicMock(id=10, is_superuser=False, username="importer")

        cve1 = f"CVE-2026-{uuid4().hex[:6].upper()}"
        csv_payload = (
            "cve_id,title,severity,cvss_score,description,published_at\n"
            f"{cve1},Queue Push Vuln,high,8.0,Desc,2026-10-01\n"
        )

        with (
            patch("apps.api.auth.decorators.get_current_user", return_value=mock_user),
            patch(
                "apps.api.api.v1.csv_import.get_csv_import_storage",
                return_value=storage,
            ),
        ):
            client = app.test_client()
            res = await client.post(
                "/api/v1/import/vulnerabilities",
                json={"csv_text": csv_payload},
            )
            assert res.status_code == 202
            body = await res.get_json()
            mock_redis.lpush.assert_called_once_with(
                CSV_IMPORT_QUEUE_KEY, body["job_id"]
            )

            # Best-effort: Valkey hiccup must not fail the persisted 202 job
            mock_redis.lpush.side_effect = RuntimeError("valkey unavailable")
            res2 = await client.post(
                "/api/v1/import/vulnerabilities",
                json={"csv_text": csv_payload},
            )
            assert res2.status_code == 202
            body2 = await res2.get_json()
            row = (
                sqlite_db(sqlite_db.csv_import_jobs.job_id == body2["job_id"])
                .select()
                .first()
            )
            assert row is not None
            assert row.status == "pending"


class TestCsvImportBatchWorker:
    """Verify CronJob claim, batched DB insert, SHA-256 tamper handling, and S3 cleanup."""

    def test_run_once_claims_imports_in_batches_and_deletes_s3_object(
        self, sqlite_db: Any
    ) -> None:
        fake_client = _InMemoryS3Client()
        storage = CsvImportStorage(bucket="elder-imports", s3_client=fake_client)

        cves = [f"CVE-2026-B{i:04d}" for i in range(5)]
        lines = ["cve_id,title,severity,cvss_score,description,published_at"]
        for i, cve in enumerate(cves):
            lines.append(f"{cve},Batch Vuln {i},high,7.{i},Batch desc,2026-10-01")
        csv_text = "\n".join(lines) + "\n"

        job_id = "csvimp-batch001"
        stored = storage.put_csv(
            tenant_id=1,
            resource="vulnerabilities",
            job_id=job_id,
            csv_text=csv_text,
        )
        sqlite_db.csv_import_jobs.insert(
            job_id=job_id,
            tenant_id=1,
            resource="vulnerabilities",
            filename="batch.csv",
            storage_backend=stored.storage_backend,
            storage_key=stored.storage_key,
            file_sha256=stored.file_sha256,
            file_size_bytes=stored.file_size_bytes,
            status="pending",
            total_rows=5,
            imported_count=0,
            skipped_count=0,
            created_at=datetime.now(UTC),
        )
        sqlite_db.commit()

        summary = run_once(
            sqlite_db,
            storage,
            limit=10,
            batch_size=2,
            worker_id="cronjob-pod-1",
        )
        assert summary == {"claimed": 1, "completed": 1, "failed": 0}

        # Job row updated cleanly
        job_row = sqlite_db(sqlite_db.csv_import_jobs.job_id == job_id).select().first()
        assert job_row.status == "completed"
        assert job_row.worker_id == "cronjob-pod-1"
        assert job_row.total_rows == 5
        assert job_row.imported_count == 5
        assert job_row.skipped_count == 0
        assert job_row.started_at is not None
        assert job_row.completed_at is not None

        # All 5 rows inserted into vulnerabilities table
        inserted_cves = {
            r.cve_id for r in sqlite_db(sqlite_db.vulnerabilities.id > 0).select()
        }
        assert set(cves).issubset(inserted_cves)

        # S3 object deleted after processing
        assert stored.storage_key in fake_client.deleted_keys
        assert ("elder-imports", stored.storage_key) not in fake_client.objects

        # Subsequent claim finds no pending jobs
        assert claim_pending_jobs(sqlite_db, limit=10, worker_id="cronjob-pod-2") == []

    def test_batch_worker_marks_failed_on_tampered_sha256_and_cleans_up_s3(
        self, sqlite_db: Any
    ) -> None:
        fake_client = _InMemoryS3Client()
        storage = CsvImportStorage(bucket="elder-imports", s3_client=fake_client)

        job_id = "csvimp-tampered01"
        stored = storage.put_csv(
            tenant_id=1,
            resource="vulnerabilities",
            job_id=job_id,
            csv_text=(
                "cve_id,title,severity,cvss_score,description,published_at\n"
                "CVE-2026-9001,Orig,high,7.5,Desc,2026-10-01\n"
            ),
        )
        # Tamper with S3 bytes after upload
        fake_client.objects[("elder-imports", stored.storage_key)] = b"tampered bytes"

        row_id = sqlite_db.csv_import_jobs.insert(
            job_id=job_id,
            tenant_id=1,
            resource="vulnerabilities",
            filename="tampered.csv",
            storage_backend="s3",
            storage_key=stored.storage_key,
            file_sha256=stored.file_sha256,
            file_size_bytes=stored.file_size_bytes,
            status="pending",
            total_rows=1,
            imported_count=0,
            skipped_count=0,
            created_at=datetime.now(UTC),
        )
        sqlite_db.commit()

        claimed = claim_pending_jobs(sqlite_db, limit=5, worker_id="cronjob-pod-2")
        assert len(claimed) == 1
        assert claimed[0]["id"] == row_id

        status = process_claimed_job(sqlite_db, storage, claimed[0], batch_size=250)
        assert status == "failed"

        job_row = sqlite_db(sqlite_db.csv_import_jobs.job_id == job_id).select().first()
        assert job_row.status == "failed"
        assert "SHA-256 mismatch" in (job_row.error_message or "")
        assert job_row.completed_at is not None
        assert stored.storage_key in fake_client.deleted_keys

    def test_batch_worker_marks_failed_on_row_validation_errors(
        self, sqlite_db: Any
    ) -> None:
        fake_client = _InMemoryS3Client()
        storage = CsvImportStorage(bucket="elder-imports", s3_client=fake_client)

        job_id = "csvimp-badrow01"
        stored = storage.put_csv(
            tenant_id=1,
            resource="vulnerabilities",
            job_id=job_id,
            csv_text=(
                "cve_id,title,severity,cvss_score,description,published_at\n"
                "CVE-2026-8801,=FORMULA(),invalid_sev,15.0,Desc,not-a-date\n"
            ),
        )
        sqlite_db.csv_import_jobs.insert(
            job_id=job_id,
            tenant_id=1,
            resource="vulnerabilities",
            filename="bad.csv",
            storage_backend="s3",
            storage_key=stored.storage_key,
            file_sha256=stored.file_sha256,
            file_size_bytes=stored.file_size_bytes,
            status="pending",
            total_rows=1,
            imported_count=0,
            skipped_count=0,
            created_at=datetime.now(UTC),
        )
        sqlite_db.commit()

        summary = run_once(sqlite_db, storage, limit=5, worker_id="cronjob-pod-3")
        assert summary == {"claimed": 1, "completed": 0, "failed": 1}

        job_row = sqlite_db(sqlite_db.csv_import_jobs.job_id == job_id).select().first()
        assert job_row.status == "failed"
        assert job_row.imported_count == 0
        assert job_row.skipped_count == 1
        assert job_row.error_message == "CSV validation failed"
        assert isinstance(job_row.errors_json, list) and len(job_row.errors_json) >= 3
        assert stored.storage_key in fake_client.deleted_keys

    def test_run_batch_once_skips_cleanly_and_logs_once_when_s3_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in (
            "CSV_IMPORT_S3_BUCKET",
            "ELDER_S3_BUCKET",
            "RUNS_S3_BUCKET",
            "DATABASE_URL",
        ):
            monkeypatch.delenv(var, raising=False)
        reset_unconfigured_log_state()

        with patch("apps.worker.csv_import_batch.logger") as mock_logger:
            rc1 = run_batch_once()
            rc2 = run_batch_once()

        assert rc1 == 0
        assert rc2 == 0
        info_events = [call.args[0] for call in mock_logger.info.call_args_list]
        assert info_events.count("csv_import_batch_s3_unconfigured_skip") == 1
        mock_logger.error.assert_not_called()

    def test_cli_once_is_noop_without_s3_unless_require_s3_passed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in (
            "CSV_IMPORT_S3_BUCKET",
            "ELDER_S3_BUCKET",
            "RUNS_S3_BUCKET",
            "DATABASE_URL",
        ):
            monkeypatch.delenv(var, raising=False)
        reset_unconfigured_log_state()

        fake_logger_mod = SimpleNamespace(
            configure_logging=lambda: None,
            get_logger=lambda name=None: MagicMock(),
        )
        with patch.dict(
            sys.modules,
            {
                "apps.worker.utils": SimpleNamespace(logger=fake_logger_mod),
                "apps.worker.utils.logger": fake_logger_mod,
            },
        ):
            assert csv_import_batch_main(["--once"]) == 0
            assert csv_import_batch_main(["--once", "--require-s3"]) == 1

    def test_run_batch_once_uses_caller_db_without_closing_it(
        self, sqlite_db: Any
    ) -> None:
        fake_client = _InMemoryS3Client()
        storage = CsvImportStorage(bucket="elder-imports", s3_client=fake_client)
        reset_unconfigured_log_state()

        rc = run_batch_once(
            db=sqlite_db,
            storage=storage,
            limit=5,
            batch_size=50,
            worker_id="svc-worker-test",
        )
        assert rc == 0
        # Caller-owned DB remains usable (not closed by run_batch_once)
        assert sqlite_db(sqlite_db.tenants.id == 1).select().first() is not None

    def test_claim_and_process_job_by_id_is_atomic_and_idempotent(
        self, sqlite_db: Any
    ) -> None:
        fake_client = _InMemoryS3Client()
        storage = CsvImportStorage(bucket="elder-imports", s3_client=fake_client)
        reset_unconfigured_log_state()

        job_id = "csvimp-byid001"
        cve = f"CVE-2026-{uuid4().hex[:6].upper()}"
        stored = storage.put_csv(
            tenant_id=1,
            resource="vulnerabilities",
            job_id=job_id,
            csv_text=(
                "cve_id,title,severity,cvss_score,description,published_at\n"
                f"{cve},ById Vuln,medium,6.2,Desc,2026-10-01\n"
            ),
        )
        sqlite_db.csv_import_jobs.insert(
            job_id=job_id,
            tenant_id=1,
            resource="vulnerabilities",
            filename="byid.csv",
            storage_backend="s3",
            storage_key=stored.storage_key,
            file_sha256=stored.file_sha256,
            file_size_bytes=stored.file_size_bytes,
            status="pending",
            total_rows=1,
            imported_count=0,
            skipped_count=0,
            created_at=datetime.now(UTC),
        )
        sqlite_db.commit()

        status = process_job_by_id(
            job_id,
            db=sqlite_db,
            storage=storage,
            worker_id="svc-worker-q1",
        )
        assert status == "completed"
        assert stored.storage_key in fake_client.deleted_keys

        # Second claim/process for the same job_id is a clean no-op
        assert claim_job_by_id(sqlite_db, job_id, worker_id="svc-worker-q2") is None
        assert (
            process_job_by_id(
                job_id,
                db=sqlite_db,
                storage=storage,
                worker_id="svc-worker-q2",
            )
            is None
        )


@contextmanager
def _import_worker_service():
    """Import ``WorkerService``, stubbing worker-only deps if absent in API env."""
    stub_modules: dict[str, Any] = {}
    if importlib.util.find_spec("aiocron") is None:
        stub_modules["aiocron"] = MagicMock()
    if importlib.util.find_spec("pydantic_settings") is None:
        fake_settings = SimpleNamespace(
            csv_import_poll_interval_seconds=60,
            database_url=None,
            database_read_url=None,
            db_pool_size=10,
            redis_url="redis://localhost:6379/0",
            health_check_port=8000,
            log_level="INFO",
            log_format="json",
        )
        fake_logger_mod = SimpleNamespace(
            configure_logging=lambda: None,
            get_logger=lambda name=None: MagicMock(),
        )
        stub_modules["apps.worker.config"] = SimpleNamespace(
            settings=SimpleNamespace(settings=fake_settings)
        )
        stub_modules["apps.worker.config.settings"] = SimpleNamespace(
            settings=fake_settings
        )
        stub_modules["apps.worker.utils"] = SimpleNamespace(logger=fake_logger_mod)
        stub_modules["apps.worker.utils.logger"] = fake_logger_mod
    with patch.dict(sys.modules, stub_modules):
        from apps.worker.main import WorkerService

        yield WorkerService


class TestSvcWorkerCsvImportRegistration:
    """Verify svc-worker registers and runs the periodic CSV batch importer loop."""

    def test_poll_interval_defaults_to_60_and_reads_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _import_worker_service() as WorkerService:
            monkeypatch.delenv("CSV_IMPORT_POLL_INTERVAL_SECONDS", raising=False)
            assert WorkerService._get_csv_import_poll_interval() == 60

            monkeypatch.setenv("CSV_IMPORT_POLL_INTERVAL_SECONDS", "15")
            assert WorkerService._get_csv_import_poll_interval() == 15

            monkeypatch.setenv("CSV_IMPORT_POLL_INTERVAL_SECONDS", "not-an-int")
            assert WorkerService._get_csv_import_poll_interval() == 60

    @pytest.mark.asyncio
    async def test_worker_service_starts_and_stops_csv_import_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio
        from unittest.mock import AsyncMock

        monkeypatch.setenv("CSV_IMPORT_POLL_INTERVAL_SECONDS", "60")
        called = asyncio.Event()

        def _fake_run_batch_once(**kwargs: Any) -> int:
            called.set()
            return 0

        with _import_worker_service() as WorkerService:
            with (
                patch.object(WorkerService, "_init_database", return_value=None),
                patch.object(WorkerService, "_init_jobbus", new_callable=AsyncMock),
                patch.object(
                    WorkerService, "_service_node_heartbeat", new_callable=AsyncMock
                ),
                patch.object(
                    WorkerService, "_setup_scheduled_syncs", return_value=None
                ),
                patch(
                    "apps.worker.main.run_batch_once",
                    side_effect=_fake_run_batch_once,
                ) as mock_batch,
            ):
                svc = WorkerService()
                await svc.start()
                try:
                    assert svc.csv_import_task is not None
                    await asyncio.wait_for(called.wait(), timeout=2.0)
                    assert mock_batch.call_count >= 1
                finally:
                    await svc.stop()

                assert svc.csv_import_task.done()

    @pytest.mark.asyncio
    async def test_worker_service_consumes_valkey_queue_via_brpop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio
        from unittest.mock import AsyncMock

        monkeypatch.setenv("CSV_IMPORT_S3_BUCKET", "elder-imports")
        monkeypatch.setenv("CSV_IMPORT_POLL_INTERVAL_SECONDS", "60")

        popped_event = asyncio.Event()
        processed_ids: list[str] = []

        async def _fake_brpop(key: str, timeout: int = 5):
            assert key == CSV_IMPORT_QUEUE_KEY
            if not popped_event.is_set():
                return (key.encode("utf-8"), b"csvimp-queued-42")
            await asyncio.sleep(0.05)
            return None

        def _fake_process_job_by_id(job_id: str, **kwargs: Any) -> str:
            processed_ids.append(job_id)
            popped_event.set()
            return "completed"

        fake_redis = MagicMock()
        fake_redis.brpop = AsyncMock(side_effect=_fake_brpop)
        fake_redis.close = AsyncMock()

        with _import_worker_service() as WorkerService:

            async def _fake_init_jobbus(self: Any) -> None:
                self.redis = fake_redis

            with (
                patch.object(WorkerService, "_init_database", return_value=None),
                patch.object(WorkerService, "_init_jobbus", _fake_init_jobbus),
                patch.object(
                    WorkerService, "_service_node_heartbeat", new_callable=AsyncMock
                ),
                patch.object(
                    WorkerService, "_setup_scheduled_syncs", return_value=None
                ),
                patch("apps.worker.main.run_batch_once", return_value=0),
                patch(
                    "apps.worker.main.process_job_by_id",
                    side_effect=_fake_process_job_by_id,
                ),
            ):
                svc = WorkerService()
                await svc.start()
                try:
                    assert svc.csv_import_queue_task is not None
                    await asyncio.wait_for(popped_event.wait(), timeout=2.0)
                    assert processed_ids == ["csvimp-queued-42"]
                finally:
                    await svc.stop()

                assert svc.csv_import_queue_task.done()
                assert svc.csv_import_task is not None and svc.csv_import_task.done()


class TestMigration049CsvImportJobs:
    """Verify Alembic migration 049 upgrades and downgrades cleanly on SQLite."""

    def test_upgrade_and_downgrade_sqlite(self, tmp_path: Path) -> None:
        mig_path = (
            Path(__file__).resolve().parents[2]
            / "alembic"
            / "versions"
            / "049_csv_import_jobs.py"
        )
        spec = importlib.util.spec_from_file_location("mig_049", mig_path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        assert mod.revision == "049"
        assert mod.down_revision == "048"

        engine = sa.create_engine(f"sqlite:///{tmp_path / 'mig049.db'}")
        with engine.begin() as conn:
            # Create minimal parent tables referenced by foreign keys
            conn.execute(sa.text("CREATE TABLE tenants (id INTEGER PRIMARY KEY)"))
            conn.execute(sa.text("CREATE TABLE identities (id INTEGER PRIMARY KEY)"))

            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.upgrade()

            inspector = sa.inspect(conn)
            assert "csv_import_jobs" in inspector.get_table_names()
            cols = {c["name"] for c in inspector.get_columns("csv_import_jobs")}
            expected_cols = {
                "id",
                "job_id",
                "tenant_id",
                "resource",
                "filename",
                "storage_backend",
                "storage_key",
                "file_sha256",
                "file_size_bytes",
                "status",
                "total_rows",
                "imported_count",
                "skipped_count",
                "errors_json",
                "error_message",
                "created_by_identity_id",
                "worker_id",
                "started_at",
                "completed_at",
                "created_at",
            }
            assert expected_cols.issubset(cols)

            # Idempotent re-run of upgrade() does not fail
            with Operations.context(ctx):
                mod.upgrade()

            with Operations.context(ctx):
                mod.downgrade()

            inspector_after = sa.inspect(conn)
            assert "csv_import_jobs" not in inspector_after.get_table_names()
        engine.dispose()
