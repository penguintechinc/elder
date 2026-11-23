"""S3-compatible object storage backend for asynchronous CSV bulk imports.

When ``CSV_IMPORT_S3_BUCKET`` (falling back to ``ELDER_S3_BUCKET``, then
``RUNS_S3_BUCKET``) is configured, ``hub-api`` streams uploaded CSV payloads
directly to the S3-compatible bucket (MinIO, Garage, Ceph RGW, Cloudflare R2,
Wasabi, or AWS S3) without writing to local disk or storing blobs in Postgres,
records the ``storage_key`` and ``file_sha256`` on ``csv_import_jobs``, and
returns ``202 Accepted``.

The ``svc-csv-importer`` batch CronJob later downloads the object, verifies
its SHA-256 digest, imports rows in batches, and deletes the S3 object on
completion.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class StoredCsvArtifact:
    """Metadata returned after uploading a CSV payload to S3."""

    storage_backend: str  # "s3"
    storage_key: str
    file_sha256: str
    file_size_bytes: int


def compute_csv_sha256(data: bytes | str) -> str:
    """Compute hex SHA-256 digest of a CSV payload."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def build_csv_import_key(
    *,
    prefix: str,
    tenant_id: int,
    resource: str,
    job_id: str,
) -> str:
    """Build a deterministic S3 object key for a queued CSV import job."""
    clean_prefix = (prefix or "csv-imports").strip("/")
    safe_resource = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in str(resource)
    ).strip("-")
    safe_job_id = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in str(job_id)
    ).strip("-")
    parts = [
        p
        for p in (
            clean_prefix,
            f"tenant-{tenant_id}",
            safe_resource or "resource",
            f"{safe_job_id or 'job'}.csv",
        )
        if p
    ]
    return "/".join(parts)


class CsvImportStorage:
    """S3-compatible object store for async CSV import payloads."""

    def __init__(
        self,
        *,
        bucket: str | None = None,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        prefix: str = "csv-imports",
        path_style: bool | None = None,
        s3_client: Any | None = None,
    ) -> None:
        self.bucket = (bucket or "").strip()
        self.endpoint_url = (endpoint_url or "").strip() or None
        self.region = (region or "us-east-1").strip()
        self._access_key_id = (access_key_id or "").strip() or None
        self._secret_access_key = (secret_access_key or "").strip() or None
        self.prefix = (prefix or "csv-imports").strip("/")
        # Default to path-style addressing whenever a custom S3 endpoint is set
        # (MinIO, Garage, Ceph RGW) unless explicitly overridden.
        self.path_style = (
            bool(self.endpoint_url) if path_style is None else bool(path_style)
        )
        self._client = s3_client

    @classmethod
    def from_env(cls) -> CsvImportStorage:
        """Construct CSV import storage configuration from environment variables."""
        bucket = (
            os.getenv("CSV_IMPORT_S3_BUCKET")
            or os.getenv("ELDER_S3_BUCKET")
            or os.getenv("RUNS_S3_BUCKET")
            or ""
        )
        endpoint = (
            os.getenv("CSV_IMPORT_S3_ENDPOINT")
            or os.getenv("ELDER_S3_ENDPOINT")
            or os.getenv("RUNS_S3_ENDPOINT")
            or ""
        )
        region = (
            os.getenv("CSV_IMPORT_S3_REGION")
            or os.getenv("ELDER_S3_REGION")
            or os.getenv("RUNS_S3_REGION")
            or os.getenv("AWS_REGION")
            or "us-east-1"
        )
        access_key = (
            os.getenv("CSV_IMPORT_S3_ACCESS_KEY_ID")
            or os.getenv("ELDER_S3_ACCESS_KEY_ID")
            or os.getenv("RUNS_S3_ACCESS_KEY_ID")
            or os.getenv("AWS_ACCESS_KEY_ID")
        )
        secret_key = (
            os.getenv("CSV_IMPORT_S3_SECRET_ACCESS_KEY")
            or os.getenv("ELDER_S3_SECRET_ACCESS_KEY")
            or os.getenv("RUNS_S3_SECRET_ACCESS_KEY")
            or os.getenv("AWS_SECRET_ACCESS_KEY")
        )
        prefix = os.getenv("CSV_IMPORT_S3_PREFIX") or "csv-imports"
        path_style_raw = os.getenv("CSV_IMPORT_S3_PATH_STYLE")
        path_style: bool | None = None
        if path_style_raw is not None and path_style_raw.strip() != "":
            path_style = path_style_raw.strip().lower() in ("1", "true", "yes")
        return cls(
            bucket=bucket,
            endpoint_url=endpoint,
            region=region,
            access_key_id=access_key,
            secret_access_key=secret_key,
            prefix=prefix,
            path_style=path_style,
        )

    @property
    def is_configured(self) -> bool:
        """Return True when an S3-compatible bucket is configured."""
        return bool(self.bucket)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        import boto3
        from botocore.config import Config

        cfg_kwargs: dict[str, Any] = {"signature_version": "s3v4"}
        if self.path_style:
            cfg_kwargs["s3"] = {"addressing_style": "path"}
        client_kwargs: dict[str, Any] = {
            "region_name": self.region,
            "config": Config(**cfg_kwargs),
        }
        if self.endpoint_url:
            client_kwargs["endpoint_url"] = self.endpoint_url
        if self._access_key_id and self._secret_access_key:
            client_kwargs["aws_access_key_id"] = self._access_key_id
            client_kwargs["aws_secret_access_key"] = self._secret_access_key
        self._client = boto3.client("s3", **client_kwargs)
        return self._client

    def put_csv(
        self,
        *,
        tenant_id: int,
        resource: str,
        job_id: str,
        csv_text: str,
    ) -> StoredCsvArtifact:
        """Upload CSV text directly to S3 and return storage metadata + SHA-256."""
        if not self.is_configured:
            raise RuntimeError("CSV import S3 storage is not configured")

        raw_bytes = csv_text.encode("utf-8")
        digest = compute_csv_sha256(raw_bytes)
        key = build_csv_import_key(
            prefix=self.prefix,
            tenant_id=tenant_id,
            resource=resource,
            job_id=job_id,
        )
        client = self._get_client()
        client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=raw_bytes,
            ContentType="text/csv; charset=utf-8",
            Metadata={
                "sha256": digest,
                "resource": resource,
                "job_id": job_id,
            },
        )
        logger.info(
            "csv_import_uploaded",
            bucket=self.bucket,
            storage_key=key,
            file_sha256=digest,
            file_size_bytes=len(raw_bytes),
            resource=resource,
            job_id=job_id,
        )
        return StoredCsvArtifact(
            storage_backend="s3",
            storage_key=key,
            file_sha256=digest,
            file_size_bytes=len(raw_bytes),
        )

    def get_csv(
        self,
        storage_key: str,
        *,
        expected_sha256: str | None = None,
    ) -> str:
        """Download CSV bytes from S3 and verify SHA-256 integrity."""
        if not self.is_configured:
            raise RuntimeError("CSV import S3 storage is not configured")
        client = self._get_client()
        resp = client.get_object(Bucket=self.bucket, Key=storage_key)
        raw_bytes: bytes = resp["Body"].read()
        actual_sha256 = compute_csv_sha256(raw_bytes)
        if expected_sha256 and actual_sha256 != expected_sha256:
            raise ValueError(
                f"CSV import SHA-256 mismatch for {storage_key}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
        return raw_bytes.decode("utf-8-sig")

    def delete_csv(self, storage_key: str | None) -> None:
        """Best-effort delete of a processed CSV object from S3."""
        if not self.is_configured or not storage_key:
            return
        try:
            client = self._get_client()
            client.delete_object(Bucket=self.bucket, Key=storage_key)
            logger.info(
                "csv_import_deleted",
                bucket=self.bucket,
                storage_key=storage_key,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "csv_import_delete_failed",
                bucket=self.bucket,
                storage_key=storage_key,
                error=str(exc),
            )


def get_csv_import_storage() -> CsvImportStorage:
    """Return a configured ``CsvImportStorage`` instance from environment."""
    return CsvImportStorage.from_env()
