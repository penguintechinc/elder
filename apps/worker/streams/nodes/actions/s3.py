"""S3 Fetch / Push Action Node for Streams Workflow.

Fetches JSON documents from or pushes JSON payloads to AWS S3 and
S3-compatible object storage (MinIO, Cloudflare R2, DigitalOcean Spaces).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlparse

import boto3

from ...executor.node_registry import register_node
from ..base import BaseNode
from ..template import interpolate_any, interpolate_value

MAX_DOWNLOAD_BYTES: int = 10 * 1024 * 1024  # 10 MiB safety cap
DEFAULT_REGION: str = "us-east-1"
_VALID_OPERATIONS = frozenset({"fetch", "push"})


def _validate_endpoint_url(endpoint_url: str) -> str:
    """Validate that an S3 endpoint URL uses http:// or https:// with a host."""
    cleaned = endpoint_url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(
            "Invalid S3 endpoint_url: must use http:// or https:// and include a host"
        )
    return cleaned


@register_node("action_s3", "actions", "S3 Fetch / Push")
class S3Action(BaseNode):
    """Fetch JSON objects from or push JSON payloads to an S3-compatible bucket."""

    node_type = "action_s3"
    name = "S3 Fetch / Push"
    description = (
        "Fetch a JSON file from S3 or push a JSON payload to an S3-compatible bucket"
    )
    category = "actions"

    @classmethod
    def inputs(cls) -> list[dict[str, Any]]:
        """Define input ports for the S3 action node."""
        return [
            {
                "name": "in",
                "description": (
                    "Optional upstream payload used for push body or "
                    "{{expression}} interpolation"
                ),
                "required": False,
                "data_type": "any",
            }
        ]

    @classmethod
    def outputs(cls) -> list[dict[str, Any]]:
        """Define output ports for the S3 action node."""
        return [
            {
                "name": "out",
                "description": (
                    "Fetched JSON document + metadata on fetch, or upload "
                    "confirmation ({bucket, key, etag, bytes_written}) on push"
                ),
                "data_type": "json",
            }
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate static S3 node configuration."""
        errors: list[str] = []
        operation = str(config.get("operation") or "fetch").strip().lower()
        if operation not in _VALID_OPERATIONS:
            errors.append(
                f"Invalid S3 operation {operation!r}; expected 'fetch' or 'push'"
            )

        bucket = str(config.get("bucket") or "").strip()
        if not bucket:
            errors.append("bucket is required")

        key = str(config.get("key") or "").strip()
        if not key:
            errors.append("key is required")

        endpoint_url = str(config.get("endpoint_url") or "").strip()
        if endpoint_url and "{{" not in endpoint_url:
            try:
                _validate_endpoint_url(endpoint_url)
            except ValueError as exc:
                errors.append(str(exc))

        return errors

    def _build_client(
        self,
        *,
        region: str,
        endpoint_url: str | None,
        access_key_id: str | None,
        secret_access_key: str | None,
    ) -> Any:
        """Create a boto3 S3 client from resolved config (or default credential chain)."""
        client_kwargs: dict[str, Any] = {"region_name": region}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        if access_key_id:
            client_kwargs["aws_access_key_id"] = access_key_id
        if secret_access_key:
            client_kwargs["aws_secret_access_key"] = secret_access_key
        return boto3.client("s3", **client_kwargs)

    def _fetch_sync(
        self,
        *,
        bucket: str,
        key: str,
        region: str,
        endpoint_url: str | None,
        access_key_id: str | None,
        secret_access_key: str | None,
    ) -> dict[str, Any]:
        """Download and parse a JSON object from S3 (blocking; run via to_thread)."""
        client = self._build_client(
            region=region,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )
        response = client.get_object(Bucket=bucket, Key=key)
        body_stream = response.get("Body")

        content_length = response.get("ContentLength")
        if isinstance(content_length, int) and content_length > MAX_DOWNLOAD_BYTES:
            if body_stream is not None and hasattr(body_stream, "close"):
                body_stream.close()
            raise ValueError(
                f"S3 object exceeds maximum allowed size of {MAX_DOWNLOAD_BYTES} bytes "
                f"(ContentLength={content_length})"
            )

        if body_stream is None:
            raise ValueError("S3 get_object response contained no Body stream")

        try:
            raw_bytes = body_stream.read(MAX_DOWNLOAD_BYTES + 1)
        finally:
            if hasattr(body_stream, "close"):
                body_stream.close()

        if len(raw_bytes) > MAX_DOWNLOAD_BYTES:
            raise ValueError(
                f"S3 object exceeds maximum allowed size of {MAX_DOWNLOAD_BYTES} bytes"
            )

        try:
            text = raw_bytes.decode("utf-8")
            parsed = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"S3 object is not valid JSON: {exc}") from exc

        etag = str(response.get("ETag") or "").strip('"')
        content_type = str(response.get("ContentType") or "application/json")
        return {
            "operation": "fetch",
            "bucket": bucket,
            "key": key,
            "etag": etag,
            "content_type": content_type,
            "bytes_read": len(raw_bytes),
            "data": parsed,
        }

    def _push_sync(
        self,
        *,
        bucket: str,
        key: str,
        body_bytes: bytes,
        region: str,
        endpoint_url: str | None,
        access_key_id: str | None,
        secret_access_key: str | None,
    ) -> dict[str, Any]:
        """Upload UTF-8 JSON bytes to S3 (blocking; run via to_thread)."""
        client = self._build_client(
            region=region,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )
        response = client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body_bytes,
            ContentType="application/json",
        )
        etag = str(response.get("ETag") or "").strip('"')
        return {
            "operation": "push",
            "bucket": bucket,
            "key": key,
            "etag": etag,
            "bytes_written": len(body_bytes),
        }

    def _resolve_push_payload(self, inputs: dict[str, Any]) -> Any:
        """Resolve push payload from config or fall back to incoming `in` port."""
        raw_payload = self.get_config_value("payload")
        if raw_payload is None or raw_payload == "":
            return inputs.get("in")
        if isinstance(raw_payload, str):
            interpolated = interpolate_value(
                raw_payload, self.context, inputs, preserve_type=True
            )
            if isinstance(interpolated, str):
                try:
                    return json.loads(interpolated)
                except json.JSONDecodeError:
                    return interpolated
            return interpolated
        return interpolate_any(raw_payload, self.context, inputs, preserve_type=True)

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute S3 fetch or push operation."""
        operation = (
            str(self.get_config_value("operation", "fetch") or "fetch").strip().lower()
        )
        if operation not in _VALID_OPERATIONS:
            raise ValueError(
                f"Invalid S3 operation {operation!r}; expected 'fetch' or 'push'"
            )

        raw_bucket = self.get_config_value("bucket", "")
        bucket = str(
            interpolate_value(raw_bucket, self.context, inputs, preserve_type=False)
            or ""
        ).strip()
        if not bucket:
            raise ValueError("S3 bucket is required")

        raw_key = self.get_config_value("key", "")
        key = str(
            interpolate_value(raw_key, self.context, inputs, preserve_type=False) or ""
        ).strip()
        if not key:
            raise ValueError("S3 key is required")

        raw_region = self.get_config_value("region", DEFAULT_REGION)
        region = (
            str(
                interpolate_value(raw_region, self.context, inputs, preserve_type=False)
                or DEFAULT_REGION
            ).strip()
            or DEFAULT_REGION
        )

        raw_endpoint = self.get_config_value("endpoint_url")
        endpoint_url: str | None = None
        if raw_endpoint is not None and str(raw_endpoint).strip():
            interpolated_endpoint = str(
                interpolate_value(
                    raw_endpoint, self.context, inputs, preserve_type=False
                )
                or ""
            ).strip()
            if interpolated_endpoint:
                endpoint_url = _validate_endpoint_url(interpolated_endpoint)

        raw_access_key = self.get_config_value("access_key_id")
        access_key_id: str | None = None
        if raw_access_key is not None and str(raw_access_key).strip():
            resolved_ak = str(
                interpolate_value(
                    raw_access_key, self.context, inputs, preserve_type=False
                )
                or ""
            ).strip()
            access_key_id = resolved_ak or None

        raw_secret_key = self.get_config_value("secret_access_key")
        secret_access_key: str | None = None
        if raw_secret_key is not None and str(raw_secret_key).strip():
            resolved_sk = str(
                interpolate_value(
                    raw_secret_key, self.context, inputs, preserve_type=False
                )
                or ""
            ).strip()
            secret_access_key = resolved_sk or None

        node_id = self.context.get("node_id", "")

        if operation == "fetch":
            if not key.lower().endswith(".json"):
                raise ValueError("S3 Fetch currently supports JSON files only (.json)")

            self.log_info(f"S3 fetch s3://{bucket}/{key} (region={region})")
            result = await asyncio.to_thread(
                self._fetch_sync,
                bucket=bucket,
                key=key,
                region=region,
                endpoint_url=endpoint_url,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
            )
            return {
                "out": {
                    "data": result,
                    "metadata": {
                        "operation": "fetch",
                        "bucket": bucket,
                        "key": key,
                        "etag": result["etag"],
                        "bytes_read": result["bytes_read"],
                    },
                    "source_node_id": node_id,
                }
            }

        payload = self._resolve_push_payload(inputs)
        body_bytes = json.dumps(payload, default=str).encode("utf-8")
        self.log_info(
            f"S3 push s3://{bucket}/{key} ({len(body_bytes)} bytes, region={region})"
        )
        result = await asyncio.to_thread(
            self._push_sync,
            bucket=bucket,
            key=key,
            body_bytes=body_bytes,
            region=region,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )
        return {
            "out": {
                "data": result,
                "metadata": {
                    "operation": "push",
                    "bucket": bucket,
                    "key": key,
                    "etag": result["etag"],
                    "bytes_written": result["bytes_written"],
                },
                "source_node_id": node_id,
            }
        }
