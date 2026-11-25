"""Gap-filling coverage tests for the S3 stream action node.

Complements test_s3_stream_node.py; the boto3 client (a true external
boundary) is replaced with an in-memory double.
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.streams.nodes.actions import s3 as s3mod
from apps.worker.streams.nodes.actions.s3 import S3Action


def _ctx(config=None):
    """Build a node context."""
    return {
        "execution_id": "e",
        "playbook_id": "p",
        "node_id": "n",
        "config": config or {},
        "global_config": {},
    }


def _secret(name):
    """Create a credential-looking value at runtime."""
    return "-".join(("cove", name, uuid4().hex[:6]))


class TestBuildClient:
    """Client construction options."""

    def test_kwargs(self):
        """Endpoint and credentials are forwarded only when present."""
        node = S3Action(_ctx())
        ak, sk = _secret("a"), _secret("s")
        with patch.object(s3mod.boto3, "client") as client:
            node._build_client(
                region="r",
                endpoint_url="http://e.example.com",
                access_key_id=ak,
                secret_access_key=sk,
            )
            kwargs = client.call_args.kwargs
            assert kwargs["endpoint_url"] == "http://e.example.com"
            assert (
                kwargs["aws_access_key_id"] == ak
                and kwargs["aws_secret_access_key"] == sk
            )
            node._build_client(
                region="r",
                endpoint_url=None,
                access_key_id=None,
                secret_access_key=None,
            )
            assert client.call_args.kwargs == {"region_name": "r"}


def _fetch(node, response):
    """Run _fetch_sync against a client returning the given response."""
    with patch.object(s3mod.boto3, "client") as client:
        client.return_value.get_object.return_value = response
        return node._fetch_sync(
            bucket="b",
            key="k.json",
            region="r",
            endpoint_url=None,
            access_key_id=None,
            secret_access_key=None,
        )


class TestFetchSync:
    """_fetch_sync size and parse guards."""

    def test_content_length_too_large(self):
        """ContentLength above the cap closes the stream and raises."""
        body = MagicMock()
        resp = {"Body": body, "ContentLength": s3mod.MAX_DOWNLOAD_BYTES + 1}
        with pytest.raises(ValueError, match="exceeds maximum"):
            _fetch(S3Action(_ctx()), resp)
        body.close.assert_called_once()

    def test_content_length_too_large_no_body(self):
        """The oversize check tolerates a missing Body."""
        resp = {"ContentLength": s3mod.MAX_DOWNLOAD_BYTES + 1}
        with pytest.raises(ValueError, match="exceeds maximum"):
            _fetch(S3Action(_ctx()), resp)

    def test_no_body(self):
        """A response without a Body raises."""
        with pytest.raises(ValueError, match="no Body"):
            _fetch(S3Action(_ctx()), {})

    def test_stream_over_cap(self):
        """A stream returning more than the cap raises even without ContentLength."""
        body = MagicMock()
        body.read.return_value = b"x" * (s3mod.MAX_DOWNLOAD_BYTES + 1)
        with pytest.raises(ValueError, match="exceeds maximum"):
            _fetch(S3Action(_ctx()), {"Body": body})
        body.close.assert_called_once()

    def test_invalid_json_and_utf8(self):
        """Non-JSON and non-UTF-8 bodies raise a clear error."""
        for raw in (b"{bad", b"\xff\xfe"):
            with pytest.raises(ValueError, match="not valid JSON"):
                _fetch(S3Action(_ctx()), {"Body": io.BytesIO(raw)})

    def test_success_defaults(self):
        """Missing ETag/ContentType fall back to defaults."""
        out = _fetch(
            S3Action(_ctx()), {"Body": io.BytesIO(b'{"a": 1}'), "ETag": '"abc"'}
        )
        assert out["data"] == {"a": 1} and out["etag"] == "abc"
        assert out["content_type"] == "application/json"

    def test_stream_without_close(self):
        """A body object lacking close() is still read."""

        class Body:
            """Body without close."""

            def read(self, n):
                """Return a tiny JSON payload."""
                return b"[1]"

        assert _fetch(S3Action(_ctx()), {"Body": Body()})["data"] == [1]


class TestPushAndResolve:
    """_push_sync and payload resolution."""

    def test_push_sync(self):
        """Upload returns the stripped ETag and byte count."""
        node = S3Action(_ctx())
        with patch.object(s3mod.boto3, "client") as client:
            client.return_value.put_object.return_value = {"ETag": '"e1"'}
            out = node._push_sync(
                bucket="b",
                key="k",
                body_bytes=b"{}",
                region="r",
                endpoint_url=None,
                access_key_id=None,
                secret_access_key=None,
            )
        assert out == {
            "operation": "push",
            "bucket": "b",
            "key": "k",
            "etag": "e1",
            "bytes_written": 2,
        }

    def test_resolve_payload(self):
        """Payload from input, JSON string, plain string, object and dict config."""
        r = lambda cfg, inp: S3Action(_ctx(cfg))._resolve_push_payload(inp)  # noqa: E731
        assert r({}, {"in": {"a": 1}}) == {"a": 1}
        assert r({"payload": ""}, {"in": 2}) == 2
        assert r({"payload": '{"k": "{{v}}"}'}, {"in": {"v": 1}}) == {"k": "1"}
        assert r({"payload": "text {{v}}"}, {"in": {"v": 1}}) == "text 1"
        assert r({"payload": "{{o}}"}, {"in": {"o": [1]}}) == [1]
        assert r({"payload": {"x": "{{v}}"}}, {"in": {"v": 3}}) == {"x": 3}


class TestExecute:
    """execute() option handling with the client faked."""

    @pytest.mark.asyncio
    async def test_fetch_with_all_options(self):
        """Region, endpoint and credentials are interpolated and forwarded."""
        ak, sk = _secret("a"), _secret("s")
        cfg = {
            "operation": "FETCH",
            "bucket": "{{b}}",
            "key": "d.JSON",
            "region": "{{r}}",
            "endpoint_url": "{{e}}",
            "access_key_id": ak,
            "secret_access_key": sk,
        }
        inputs = {"in": {"b": "bk", "r": "eu-west-1", "e": "https://s3.example.com"}}
        with patch.object(s3mod.boto3, "client") as client:
            client.return_value.get_object.return_value = {
                "Body": io.BytesIO(b'{"z": 1}')
            }
            res = await S3Action(_ctx(cfg)).execute(inputs)
            kw = client.call_args.kwargs
        assert (
            kw["region_name"] == "eu-west-1"
            and kw["endpoint_url"] == "https://s3.example.com"
        )
        assert kw["aws_access_key_id"] == ak
        assert (
            res["out"]["data"]["data"] == {"z": 1}
            and res["out"]["metadata"]["bucket"] == "bk"
        )

    @pytest.mark.asyncio
    async def test_blank_options_ignored(self):
        """Blank region/endpoint/credentials fall back to defaults."""
        cfg = {
            "operation": "push",
            "bucket": "b",
            "key": "k",
            "region": "  ",
            "endpoint_url": " ",
            "access_key_id": " ",
            "secret_access_key": "  ",
        }
        with patch.object(s3mod.boto3, "client") as client:
            client.return_value.put_object.return_value = {}
            res = await S3Action(_ctx(cfg)).execute({"in": {"a": 1}})
            assert client.call_args.kwargs["region_name"] == "us-east-1"
        assert res["out"]["metadata"]["operation"] == "push"
        assert json.loads(client.return_value.put_object.call_args.kwargs["Body"]) == {
            "a": 1
        }

    @pytest.mark.asyncio
    async def test_validation_errors(self):
        """Bad operation, bucket, key and non-JSON fetch key raise."""
        cases = [
            ({"operation": "zap"}, "Invalid S3 operation"),
            ({"bucket": ""}, "bucket is required"),
            ({"bucket": "b", "key": ""}, "key is required"),
            ({"bucket": "b", "key": "x.txt"}, "JSON files only"),
        ]
        for cfg, msg in cases:
            with pytest.raises(ValueError, match=msg):
                await S3Action(_ctx(cfg)).execute({})
