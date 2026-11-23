"""Unit tests for Elder Runs S3-compatible artifact storage."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest

from apps.api.modules.runs.storage import (
    RunArtifactStorage,
    build_artifact_key,
    compute_code_sha256,
)


class TestBuildArtifactKey:
    def test_deterministic_key_per_runtime(self) -> None:
        key = build_artifact_key(
            prefix="/runs/",
            tenant_id=7,
            function_ref="eld-fn-abc123",
            version_number=3,
            runtime="python",
        )
        assert key == "runs/tenant-7/eld-fn-abc123/v3/main.py"

    @pytest.mark.parametrize(
        ("runtime", "expected_file"),
        [
            ("nodejs", "main.js"),
            ("rust", "main.rs"),
            ("python", "main.py"),
            ("golang", "main.go"),
            ("ruby", "main.rb"),
        ],
    )
    def test_runtime_filenames(self, runtime: str, expected_file: str) -> None:
        key = build_artifact_key(
            prefix="runs",
            tenant_id=1,
            function_ref="fn-1",
            version_number=1,
            runtime=runtime,
        )
        assert key.endswith(f"/v1/{expected_file}")


class TestRunArtifactStorage:
    def test_unconfigured_falls_back_to_database_with_sha256(self) -> None:
        storage = RunArtifactStorage(bucket="")
        assert not storage.is_configured

        code = "print('hello runs')"
        result = storage.put_code(
            tenant_id=1,
            function_ref="eld-1",
            version_number=1,
            runtime="python",
            code=code,
        )
        assert result.storage_backend == "database"
        assert result.storage_key is None
        assert result.code_sha256 == compute_code_sha256(code)

    def test_configured_uploads_fetches_verifies_and_presigns(self) -> None:
        code = "console.log(JSON.stringify({ok: true}));\n"
        expected_sha = compute_code_sha256(code)

        fake_client = MagicMock()
        fake_client.get_object.return_value = {"Body": io.BytesIO(code.encode("utf-8"))}
        fake_client.generate_presigned_url.return_value = "https://s3.internal.test/elder-runs/runs/tenant-2/eld-42/v1/main.js?X-Amz-Signature=abc"

        storage = RunArtifactStorage(
            bucket="elder-runs",
            endpoint_url="https://s3.internal.test",
            region="us-east-1",
            prefix="runs",
            presign_ttl_seconds=180,
            s3_client=fake_client,
        )
        assert storage.is_configured
        assert storage.path_style is True

        stored = storage.put_code(
            tenant_id=2,
            function_ref="eld-42",
            version_number=1,
            runtime="nodejs",
            code=code,
        )
        assert stored.storage_backend == "s3"
        assert stored.storage_key == "runs/tenant-2/eld-42/v1/main.js"
        assert stored.code_sha256 == expected_sha
        fake_client.put_object.assert_called_once()
        put_kwargs = fake_client.put_object.call_args.kwargs
        assert put_kwargs["Bucket"] == "elder-runs"
        assert put_kwargs["Key"] == stored.storage_key
        assert put_kwargs["Body"] == code.encode("utf-8")
        assert put_kwargs["Metadata"]["sha256"] == expected_sha

        fetched = storage.get_code(stored.storage_key, expected_sha256=expected_sha)
        assert fetched == code

        url = storage.generate_presigned_get_url(stored.storage_key)
        assert "X-Amz-Signature=" in url
        fake_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "elder-runs", "Key": stored.storage_key},
            ExpiresIn=180,
        )

    def test_get_code_fails_loudly_on_sha256_mismatch(self) -> None:
        fake_client = MagicMock()
        fake_client.get_object.return_value = {"Body": io.BytesIO(b"tampered code")}
        storage = RunArtifactStorage(bucket="elder-runs", s3_client=fake_client)

        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            storage.get_code(
                "runs/tenant-1/fn/v1/main.py",
                expected_sha256="0" * 64,
            )

    def test_from_env_clamps_ttl_and_enables_path_style_for_custom_endpoint(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("RUNS_S3_BUCKET", "my-runs-bucket")
        monkeypatch.setenv("RUNS_S3_ENDPOINT", "http://minio.storage:9000")
        monkeypatch.setenv("RUNS_S3_PRESIGN_TTL_SECONDS", "99999")
        monkeypatch.delenv("RUNS_S3_PATH_STYLE", raising=False)

        storage = RunArtifactStorage.from_env()
        assert storage.is_configured
        assert storage.bucket == "my-runs-bucket"
        assert storage.endpoint_url == "http://minio.storage:9000"
        assert storage.presign_ttl_seconds == 900
        assert storage.path_style is True
