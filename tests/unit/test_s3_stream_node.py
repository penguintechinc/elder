"""Unit tests for the S3 Fetch / Push playbook action node (`action_s3`)."""

from __future__ import annotations

import io
import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from apps.api.modules.streams.node_catalog import BUILTIN_UI_META
from apps.worker.streams.executor.node_registry import NodeRegistry, discover_nodes
from apps.worker.streams.nodes.actions.s3 import MAX_DOWNLOAD_BYTES, S3Action


def _make_context(config: dict, **extra) -> dict:
    ctx = {
        "execution_id": "exec-s3-1",
        "playbook_id": "pb-s3-1",
        "node_id": "node-s3-1",
        "config": config,
        "global_config": extra.get("global_config", {}),
    }
    ctx.update(extra)
    return ctx


class TestS3ActionRegistrationAndCatalog:
    """Verify node registry wiring and UI catalog metadata."""

    def test_action_s3_registered_in_node_registry(self):
        discover_nodes()
        cls = NodeRegistry.get("action_s3")
        assert cls is S3Action
        assert cls.node_type == "action_s3"
        assert cls.category == "actions"
        assert cls.name == "S3 Fetch / Push"

        inputs = cls.inputs()
        assert len(inputs) == 1
        assert inputs[0]["name"] == "in"
        assert inputs[0]["required"] is False

        outputs = cls.outputs()
        assert len(outputs) == 1
        assert outputs[0]["name"] == "out"
        assert outputs[0]["data_type"] == "json"

    def test_catalog_ui_spec_present_and_complete(self):
        assert "action_s3" in BUILTIN_UI_META
        spec = BUILTIN_UI_META["action_s3"]
        assert spec["stage"] == "actions"
        assert spec["subcategory"] == "Storage & Files"
        assert spec["icon"] == "HardDrive"
        assert spec["color"] == "#10b981"

        fields = {f["field"]: f for f in spec["config_schema"]}
        assert {
            "operation",
            "bucket",
            "key",
            "region",
            "endpoint_url",
            "access_key_id",
            "secret_access_key",
            "payload",
        }.issubset(fields.keys())

        assert fields["operation"]["default"] == "fetch"
        assert fields["bucket"]["required"] is True
        assert fields["bucket"]["supports_expressions"] is True
        assert fields["key"]["required"] is True
        assert fields["key"]["supports_expressions"] is True
        assert fields["secret_access_key"]["type"] == "password"
        assert fields["payload"]["show_when"] == {
            "field": "operation",
            "equals": "push",
        }

        out_names = {o["name"] for o in spec["output_schema"]}
        assert {"operation", "bucket", "key", "etag", "data", "bytes_written"}.issubset(
            out_names
        )


class TestS3ActionFetch:
    """Verify S3 JSON fetch behavior, interpolation, and safety guards."""

    @pytest.mark.asyncio
    async def test_fetch_json_success_with_key_interpolation(self):
        doc = {"items": [{"id": 1, "name": "alpha"}], "count": 1}
        raw = json.dumps(doc).encode("utf-8")

        fake_s3 = MagicMock()
        fake_s3.get_object.return_value = {
            "Body": io.BytesIO(raw),
            "ContentLength": len(raw),
            "ContentType": "application/json",
            "ETag": '"abc123etag"',
        }

        node = S3Action(
            _make_context(
                {
                    "operation": "fetch",
                    "bucket": "elder-{{$json.env}}",
                    "key": "exports/{{$json.report_id}}.json",
                    "region": "us-west-2",
                }
            )
        )

        with patch("apps.worker.streams.nodes.actions.s3.boto3.client") as mock_client:
            mock_client.return_value = fake_s3
            out = await node.execute({"in": {"env": "prod", "report_id": "2026-daily"}})

        mock_client.assert_called_once_with("s3", region_name="us-west-2")
        fake_s3.get_object.assert_called_once_with(
            Bucket="elder-prod", Key="exports/2026-daily.json"
        )

        result = out["out"]["data"]
        assert result["operation"] == "fetch"
        assert result["bucket"] == "elder-prod"
        assert result["key"] == "exports/2026-daily.json"
        assert result["etag"] == "abc123etag"
        assert result["bytes_read"] == len(raw)
        assert result["data"] == doc
        assert out["out"]["source_node_id"] == "node-s3-1"

    @pytest.mark.asyncio
    async def test_fetch_rejects_non_json_extension(self):
        node = S3Action(
            _make_context(
                {
                    "operation": "fetch",
                    "bucket": "my-bucket",
                    "key": "reports/summary.csv",
                }
            )
        )

        with patch("apps.worker.streams.nodes.actions.s3.boto3.client") as mock_client:
            with pytest.raises(
                ValueError,
                match=r"S3 Fetch currently supports JSON files only \(\.json\)",
            ):
                await node.execute({})
            mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_rejects_invalid_json_content(self):
        bad_bytes = b"{not valid json"
        fake_s3 = MagicMock()
        fake_s3.get_object.return_value = {
            "Body": io.BytesIO(bad_bytes),
            "ContentLength": len(bad_bytes),
            "ETag": '"bad1"',
        }

        node = S3Action(
            _make_context(
                {
                    "operation": "fetch",
                    "bucket": "my-bucket",
                    "key": "broken.JSON",
                }
            )
        )

        with patch(
            "apps.worker.streams.nodes.actions.s3.boto3.client", return_value=fake_s3
        ):
            with pytest.raises(ValueError, match="S3 object is not valid JSON:"):
                await node.execute({})

    @pytest.mark.asyncio
    async def test_fetch_rejects_oversized_object_by_content_length(self):
        fake_body = MagicMock()
        fake_s3 = MagicMock()
        fake_s3.get_object.return_value = {
            "Body": fake_body,
            "ContentLength": MAX_DOWNLOAD_BYTES + 1,
        }

        node = S3Action(
            _make_context(
                {
                    "operation": "fetch",
                    "bucket": "my-bucket",
                    "key": "huge.json",
                }
            )
        )

        with patch(
            "apps.worker.streams.nodes.actions.s3.boto3.client", return_value=fake_s3
        ):
            with pytest.raises(ValueError, match="exceeds maximum allowed size"):
                await node.execute({})
        fake_body.close.assert_called_once()
        fake_body.read.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_rejects_oversized_object_by_stream_read(self):
        oversized = b"x" * (MAX_DOWNLOAD_BYTES + 10)
        fake_s3 = MagicMock()
        fake_s3.get_object.return_value = {
            "Body": io.BytesIO(oversized),
        }

        node = S3Action(
            _make_context(
                {
                    "operation": "fetch",
                    "bucket": "my-bucket",
                    "key": "huge-stream.json",
                }
            )
        )

        with patch(
            "apps.worker.streams.nodes.actions.s3.boto3.client", return_value=fake_s3
        ):
            with pytest.raises(ValueError, match="exceeds maximum allowed size"):
                await node.execute({})


class TestS3ActionPushAndEndpoint:
    """Verify S3 JSON push behavior, custom endpoints, and credential hygiene."""

    @pytest.mark.asyncio
    async def test_push_json_success_with_explicit_payload(self):
        fake_s3 = MagicMock()
        fake_s3.put_object.return_value = {"ETag": '"put-etag-1"'}

        node = S3Action(
            _make_context(
                {
                    "operation": "push",
                    "bucket": "exports-bucket",
                    "key": "alerts/{{$json.id}}.json",
                    "payload": {"alert_id": "{{$json.id}}", "status": "resolved"},
                }
            )
        )

        with patch(
            "apps.worker.streams.nodes.actions.s3.boto3.client", return_value=fake_s3
        ):
            out = await node.execute({"in": {"id": "inc-42"}})

        fake_s3.put_object.assert_called_once()
        kwargs = fake_s3.put_object.call_args.kwargs
        assert kwargs["Bucket"] == "exports-bucket"
        assert kwargs["Key"] == "alerts/inc-42.json"
        assert kwargs["ContentType"] == "application/json"
        assert json.loads(kwargs["Body"].decode("utf-8")) == {
            "alert_id": "inc-42",
            "status": "resolved",
        }

        result = out["out"]["data"]
        assert result == {
            "operation": "push",
            "bucket": "exports-bucket",
            "key": "alerts/inc-42.json",
            "etag": "put-etag-1",
            "bytes_written": len(kwargs["Body"]),
        }

    @pytest.mark.asyncio
    async def test_push_json_falls_back_to_input_port(self):
        fake_s3 = MagicMock()
        fake_s3.put_object.return_value = {"ETag": '"put-etag-2"'}

        upstream = {"host": "db-01", "cpu": 92.5}
        node = S3Action(
            _make_context(
                {
                    "operation": "push",
                    "bucket": "metrics-bucket",
                    "key": "snapshots/latest.json",
                }
            )
        )

        with patch(
            "apps.worker.streams.nodes.actions.s3.boto3.client", return_value=fake_s3
        ):
            out = await node.execute({"in": upstream})

        kwargs = fake_s3.put_object.call_args.kwargs
        assert json.loads(kwargs["Body"].decode("utf-8")) == upstream
        assert out["out"]["data"]["operation"] == "push"
        assert out["out"]["data"]["bytes_written"] == len(kwargs["Body"])

    @pytest.mark.asyncio
    async def test_custom_endpoint_url_and_secret_not_logged(self, caplog):
        fake_s3 = MagicMock()
        fake_s3.put_object.return_value = {"ETag": '"minio-etag"'}
        secret = "super-secret-s3-key-never-log"

        node = S3Action(
            _make_context(
                {
                    "operation": "push",
                    "bucket": "r2-bucket",
                    "key": "out.json",
                    "region": "auto",
                    "endpoint_url": "https://minio.internal:9000",
                    "access_key_id": "AKIAEXAMPLE",
                    "secret_access_key": secret,
                    "payload": {"ok": True},
                }
            )
        )

        with (
            caplog.at_level(logging.DEBUG),
            patch("apps.worker.streams.nodes.actions.s3.boto3.client") as mock_client,
        ):
            mock_client.return_value = fake_s3
            await node.execute({})

        mock_client.assert_called_once_with(
            "s3",
            region_name="auto",
            endpoint_url="https://minio.internal:9000",
            aws_access_key_id="AKIAEXAMPLE",
            aws_secret_access_key=secret,
        )
        assert secret not in caplog.text

    @pytest.mark.asyncio
    async def test_invalid_endpoint_url_scheme_rejected(self):
        node = S3Action(
            _make_context(
                {
                    "operation": "fetch",
                    "bucket": "b",
                    "key": "a.json",
                    "endpoint_url": "file:///etc/passwd",
                }
            )
        )
        with pytest.raises(ValueError, match="Invalid S3 endpoint_url"):
            await node.execute({})

    def test_validate_config_checks_required_fields_and_endpoint(self):
        node = S3Action(_make_context({}))
        errs = node.validate_config(
            {"operation": "delete", "endpoint_url": "ftp://bad"}
        )
        assert any("Invalid S3 operation" in e for e in errs)
        assert any("bucket is required" in e for e in errs)
        assert any("key is required" in e for e in errs)
        assert any("Invalid S3 endpoint_url" in e for e in errs)

        ok_errs = node.validate_config(
            {
                "operation": "fetch",
                "bucket": "my-bucket",
                "key": "data.json",
                "endpoint_url": "https://s3.example.com",
            }
        )
        assert ok_errs == []
