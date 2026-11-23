"""S3-compatible object storage backend for Elder Runs function code artifacts.

When ``RUNS_S3_BUCKET`` (or ``ELDER_S3_BUCKET``) is configured, ``hub-api``
uploads immutable versioned function scripts to the S3-compatible bucket
(MinIO, Garage, Ceph RGW, Cloudflare R2, Wasabi, or AWS S3) and records the
``storage_key`` + ``code_sha256`` digest on ``run_functions`` and
``run_function_versions``.

When ``svc-runworker`` claims a pending execution, ``hub-api`` mints a
short-lived presigned GET URL so neither ``svc-runworker`` nor the isolated
execution pods ever hold S3 credentials.

When no bucket is configured, Elder falls back cleanly to ``storage_backend="database"``
while still recording ``code_sha256`` for integrity.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

RUNTIME_FILENAMES: dict[str, str] = {
    "nodejs": "main.js",
    "rust": "main.rs",
    "python": "main.py",
    "golang": "main.go",
    "ruby": "main.rb",
}

DEFAULT_PRESIGN_TTL_SECONDS = 300
MIN_PRESIGN_TTL_SECONDS = 30
MAX_PRESIGN_TTL_SECONDS = 900


@dataclass(frozen=True)
class StoredArtifact:
    """Result of storing a function script artifact."""

    storage_backend: str  # "s3" | "database"
    storage_key: str | None
    code_sha256: str


def compute_code_sha256(code: str) -> str:
    """Compute hex SHA-256 digest of a UTF-8 function script."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def build_artifact_key(
    *,
    prefix: str,
    tenant_id: int,
    function_ref: str,
    version_number: int,
    runtime: str,
) -> str:
    """Build a deterministic, immutable S3 object key for a function version."""
    clean_prefix = prefix.strip("/")
    filename = RUNTIME_FILENAMES.get(runtime.strip().lower(), "main.txt")
    safe_ref = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in str(function_ref)
    ).strip("-")
    parts = [
        p
        for p in (
            clean_prefix,
            f"tenant-{tenant_id}",
            safe_ref or "fn",
            f"v{int(version_number)}",
            filename,
        )
        if p
    ]
    return "/".join(parts)


class RunArtifactStorage:
    """S3-compatible artifact store with transparent database fallback."""

    def __init__(
        self,
        *,
        bucket: str | None = None,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        prefix: str = "runs",
        presign_ttl_seconds: int = DEFAULT_PRESIGN_TTL_SECONDS,
        path_style: bool | None = None,
        s3_client: Any | None = None,
    ) -> None:
        self.bucket = (bucket or "").strip()
        self.endpoint_url = (endpoint_url or "").strip() or None
        self.region = (region or "us-east-1").strip()
        self._access_key_id = (access_key_id or "").strip() or None
        self._secret_access_key = (secret_access_key or "").strip() or None
        self.prefix = (prefix or "runs").strip("/")
        self.presign_ttl_seconds = max(
            MIN_PRESIGN_TTL_SECONDS,
            min(MAX_PRESIGN_TTL_SECONDS, int(presign_ttl_seconds)),
        )
        # Default to path-style addressing whenever a custom S3 endpoint is set
        # (MinIO, Garage, Ceph RGW) unless explicitly overridden.
        self.path_style = (
            bool(self.endpoint_url) if path_style is None else bool(path_style)
        )
        self._client = s3_client

    @classmethod
    def from_env(cls) -> RunArtifactStorage:
        """Construct storage configuration from process environment variables."""
        bucket = os.getenv("RUNS_S3_BUCKET") or os.getenv("ELDER_S3_BUCKET") or ""
        endpoint = os.getenv("RUNS_S3_ENDPOINT") or os.getenv("ELDER_S3_ENDPOINT") or ""
        region = (
            os.getenv("RUNS_S3_REGION")
            or os.getenv("ELDER_S3_REGION")
            or os.getenv("AWS_REGION")
            or "us-east-1"
        )
        access_key = (
            os.getenv("RUNS_S3_ACCESS_KEY_ID")
            or os.getenv("ELDER_S3_ACCESS_KEY_ID")
            or os.getenv("AWS_ACCESS_KEY_ID")
        )
        secret_key = (
            os.getenv("RUNS_S3_SECRET_ACCESS_KEY")
            or os.getenv("ELDER_S3_SECRET_ACCESS_KEY")
            or os.getenv("AWS_SECRET_ACCESS_KEY")
        )
        prefix = os.getenv("RUNS_S3_PREFIX") or "runs"
        ttl_raw = os.getenv("RUNS_S3_PRESIGN_TTL_SECONDS") or str(
            DEFAULT_PRESIGN_TTL_SECONDS
        )
        try:
            ttl = int(ttl_raw)
        except ValueError:
            ttl = DEFAULT_PRESIGN_TTL_SECONDS
        path_style_raw = os.getenv("RUNS_S3_PATH_STYLE")
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
            presign_ttl_seconds=ttl,
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

    def put_code(
        self,
        *,
        tenant_id: int,
        function_ref: str,
        version_number: int,
        runtime: str,
        code: str,
    ) -> StoredArtifact:
        """Upload versioned script to S3 when configured, else return DB metadata.

        Fails loudly if S3 is configured and the upload fails — never silently
        falls back to database storage when S3 is expected.
        """
        digest = compute_code_sha256(code)
        if not self.is_configured:
            return StoredArtifact(
                storage_backend="database",
                storage_key=None,
                code_sha256=digest,
            )

        key = build_artifact_key(
            prefix=self.prefix,
            tenant_id=tenant_id,
            function_ref=function_ref,
            version_number=version_number,
            runtime=runtime,
        )
        client = self._get_client()
        client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=code.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
            Metadata={
                "sha256": digest,
                "runtime": runtime,
                "version": str(version_number),
            },
        )
        logger.info(
            "run_artifact_uploaded",
            bucket=self.bucket,
            storage_key=key,
            code_sha256=digest,
            runtime=runtime,
        )
        return StoredArtifact(
            storage_backend="s3",
            storage_key=key,
            code_sha256=digest,
        )

    def get_code(
        self,
        storage_key: str,
        *,
        expected_sha256: str | None = None,
    ) -> str:
        """Download script bytes from S3 and verify SHA-256 integrity."""
        if not self.is_configured:
            raise RuntimeError("S3 artifact storage is not configured")
        client = self._get_client()
        resp = client.get_object(Bucket=self.bucket, Key=storage_key)
        raw_bytes: bytes = resp["Body"].read()
        actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        if expected_sha256 and actual_sha256 != expected_sha256:
            raise ValueError(
                f"Run artifact SHA-256 mismatch for {storage_key}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
        return raw_bytes.decode("utf-8")

    def generate_presigned_get_url(self, storage_key: str) -> str:
        """Mint a short-lived presigned GET URL for ``svc-runworker``."""
        if not self.is_configured:
            raise RuntimeError("S3 artifact storage is not configured")
        client = self._get_client()
        return str(
            client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": storage_key},
                ExpiresIn=self.presign_ttl_seconds,
            )
        )

    def delete_object(self, storage_key: str) -> None:
        """Best-effort delete of an artifact object (logs warning on failure)."""
        if not self.is_configured or not storage_key:
            return
        try:
            client = self._get_client()
            client.delete_object(Bucket=self.bucket, Key=storage_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "run_artifact_delete_failed",
                bucket=self.bucket,
                storage_key=storage_key,
                error=str(exc),
            )


def get_run_artifact_storage() -> RunArtifactStorage:
    """Return a configured ``RunArtifactStorage`` instance from environment."""
    return RunArtifactStorage.from_env()
