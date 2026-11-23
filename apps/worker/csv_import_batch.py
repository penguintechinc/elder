"""Batch Kubernetes CronJob runner for S3-backed async CSV imports.

Usage:
    python3 -m apps.worker.csv_import_batch --once [--limit 25] [--batch-size 250]

Claims pending rows from ``csv_import_jobs`` (``FOR UPDATE SKIP LOCKED`` on
PostgreSQL, conditional update on SQLite), downloads and SHA-256-verifies each
CSV from S3, inserts rows in DB batches via ``process_csv_text``, updates job
status/counts/errors, and deletes the processed CSV object from S3.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from datetime import UTC, datetime
from typing import Any

import structlog
from penguin_dal import DAL
from sqlalchemy import text

from apps.api.services.csv_import.processor import process_csv_text
from apps.api.services.csv_import.storage import (
    CsvImportStorage,
    get_csv_import_storage,
)

logger = structlog.get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgres://") and not database_url.startswith(
        "postgresql://"
    ):
        return database_url.replace("postgres://", "postgresql://", 1)
    return database_url


def claim_pending_jobs(
    db: Any,
    *,
    limit: int = 25,
    worker_id: str,
) -> list[dict[str, Any]]:
    """Atomically claim up to ``limit`` pending CSV import jobs."""
    now = _utc_now()
    safe_limit = max(1, int(limit))
    engine = getattr(db, "engine", None)
    dialect_name = getattr(getattr(engine, "dialect", None), "name", "")

    if engine is not None and dialect_name == "postgresql":
        claim_sql = text(
            """
            WITH next_jobs AS (
                SELECT id
                FROM csv_import_jobs
                WHERE status = 'pending'
                ORDER BY created_at ASC, id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            UPDATE csv_import_jobs j
            SET status = 'processing',
                worker_id = :worker_id,
                started_at = :started_at
            FROM next_jobs nj
            WHERE j.id = nj.id
            RETURNING
                j.id,
                j.job_id,
                j.tenant_id,
                j.resource,
                j.filename,
                j.storage_backend,
                j.storage_key,
                j.file_sha256,
                j.file_size_bytes
            """
        )
        with engine.begin() as conn:
            rows = (
                conn.execute(
                    claim_sql,
                    {"limit": safe_limit, "worker_id": worker_id, "started_at": now},
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    # SQLite / generic dialect fallback: conditional update per candidate row
    candidates = db(db.csv_import_jobs.status == "pending").select(
        orderby=db.csv_import_jobs.id,
        limitby=(0, safe_limit * 2),
    )
    claimed: list[dict[str, Any]] = []
    for row in candidates:
        if len(claimed) >= safe_limit:
            break
        updated = db(
            (db.csv_import_jobs.id == row.id) & (db.csv_import_jobs.status == "pending")
        ).update(
            status="processing",
            worker_id=worker_id,
            started_at=now,
        )
        db.commit()
        if updated:
            claimed.append(
                {
                    "id": row.id,
                    "job_id": row.job_id,
                    "tenant_id": row.tenant_id,
                    "resource": row.resource,
                    "filename": row.filename,
                    "storage_backend": row.storage_backend,
                    "storage_key": row.storage_key,
                    "file_sha256": row.file_sha256,
                    "file_size_bytes": row.file_size_bytes,
                }
            )
    return claimed


def process_claimed_job(
    db: Any,
    storage: CsvImportStorage,
    job: dict[str, Any],
    *,
    batch_size: int = 250,
) -> str:
    """Download, verify, import, update DB, and delete S3 object for one job.

    Returns the terminal status (``"completed"`` or ``"failed"``).
    """
    job_pk = job["id"]
    job_id = job["job_id"]
    tenant_id = int(job["tenant_id"])
    resource = str(job["resource"])
    storage_key = job.get("storage_key")
    file_sha256 = job.get("file_sha256")

    try:
        if not storage_key:
            raise ValueError(f"CSV import job {job_id} has no storage_key")

        csv_text = storage.get_csv(storage_key, expected_sha256=file_sha256)
        result = process_csv_text(
            db,
            tenant_id=tenant_id,
            resource=resource,
            csv_text=csv_text,
            batch_size=batch_size,
        )
        completed_at = _utc_now()
        if result.errors:
            db(db.csv_import_jobs.id == job_pk).update(
                status="failed",
                total_rows=result.total_rows,
                imported_count=result.imported_count,
                skipped_count=result.skipped_count,
                errors_json=result.errors,
                error_message="CSV validation failed",
                completed_at=completed_at,
            )
            db.commit()
            logger.warning(
                "csv_import_job_validation_failed",
                job_id=job_id,
                tenant_id=tenant_id,
                resource=resource,
                total_rows=result.total_rows,
                error_count=len(result.errors),
            )
            return "failed"

        db(db.csv_import_jobs.id == job_pk).update(
            status="completed",
            total_rows=result.total_rows,
            imported_count=result.imported_count,
            skipped_count=result.skipped_count,
            errors_json=[],
            error_message=None,
            completed_at=completed_at,
        )
        db.commit()
        logger.info(
            "csv_import_job_completed",
            job_id=job_id,
            tenant_id=tenant_id,
            resource=resource,
            total_rows=result.total_rows,
            imported_count=result.imported_count,
        )
        return "completed"
    except Exception as exc:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        db(db.csv_import_jobs.id == job_pk).update(
            status="failed",
            error_message=str(exc)[:2000],
            completed_at=_utc_now(),
        )
        db.commit()
        logger.error(
            "csv_import_job_failed",
            job_id=job_id,
            tenant_id=tenant_id,
            resource=resource,
            error=str(exc),
        )
        return "failed"
    finally:
        if storage_key:
            storage.delete_csv(storage_key)


def run_once(
    db: Any,
    storage: CsvImportStorage,
    *,
    limit: int = 25,
    batch_size: int = 250,
    worker_id: str | None = None,
) -> dict[str, int]:
    """Claim and process one batch of pending CSV import jobs."""
    resolved_worker = (
        worker_id or os.getenv("POD_NAME") or socket.gethostname() or "svc-csv-importer"
    )
    claimed_jobs = claim_pending_jobs(db, limit=limit, worker_id=resolved_worker)
    completed = 0
    failed = 0
    for job in claimed_jobs:
        status = process_claimed_job(db, storage, job, batch_size=batch_size)
        if status == "completed":
            completed += 1
        else:
            failed += 1
    summary = {
        "claimed": len(claimed_jobs),
        "completed": completed,
        "failed": failed,
    }
    logger.info("csv_import_batch_pass_finished", worker_id=resolved_worker, **summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for ``python3 -m apps.worker.csv_import_batch --once``."""
    from apps.worker.utils.logger import configure_logging

    configure_logging()
    parser = argparse.ArgumentParser(
        description="Process queued S3-backed CSV import jobs."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=True,
        help="Drain up to --limit pending jobs and exit (default for CronJob).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum number of pending jobs to claim in one run (default: 25).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=250,
        help="Number of rows to insert per DB commit batch (default: 250).",
    )
    parser.add_argument(
        "--worker-id",
        type=str,
        default=None,
        help="Explicit worker identifier (defaults to POD_NAME or hostname).",
    )
    args = parser.parse_args(argv)

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.error("csv_import_batch_missing_database_url")
        return 1

    storage = get_csv_import_storage()
    if not storage.is_configured:
        logger.error(
            "csv_import_batch_s3_not_configured",
            hint="Set CSV_IMPORT_S3_BUCKET or ELDER_S3_BUCKET",
        )
        return 1

    db = DAL(_normalize_database_url(database_url), pool_size=2, migrate=False)
    try:
        run_once(
            db,
            storage,
            limit=args.limit,
            batch_size=args.batch_size,
            worker_id=args.worker_id,
        )
        return 0
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
