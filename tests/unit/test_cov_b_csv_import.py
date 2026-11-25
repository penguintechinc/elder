"""Unit tests for the CSV import API blueprint helpers and endpoints."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash

BASE = "/api/v1/import"
MOD = "apps.api.api.v1.csv_import"


def _ident(db, role: str) -> int:
    """Insert a tenant and an identity with ``role``; return the identity id."""
    tid = db.tenants.insert(
        name="cov-b", slug=f"covb-{uuid4().hex[:10]}", is_active=True
    )
    name = f"covb-{uuid4().hex[:8]}"
    iid = db.identities.insert(
        username=name,
        email=f"{name}@example.test",
        identity_type="human",
        auth_provider="local",
        password_hash=generate_password_hash(uuid4().hex),
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role=role,
        tenant_id=tid,
    )
    db.commit()
    return iid


async def _h(app, iid: int) -> dict[str, str]:
    """Bearer headers for ``iid``."""
    from apps.api.auth.jwt_handler import generate_token

    async with app.app_context():
        tok = generate_token(app.db.identities[iid], "access")
    return {"Authorization": f"Bearer {tok}"}


def _template_csv(resource: str, with_row: bool = True) -> str:
    """Build a CSV with the exact template header (and example row)."""
    from apps.api.services.csv_import.processor import CSV_TEMPLATES

    spec = CSV_TEMPLATES[resource]
    lines = [",".join(spec["headers"])]
    if with_row:
        lines.append(",".join(spec["example"]))
    return "\n".join(lines) + "\n"


def test_small_helpers():
    """Helpers normalise dates and error payloads."""
    from apps.api.api.v1.csv_import import (
        _allow_sync_fallback,
        _iso_or_none,
        _parse_errors_json,
    )

    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert _iso_or_none(None) is None
    assert _iso_or_none(now) == now.isoformat()
    assert _iso_or_none(5) == "5"
    assert _parse_errors_json(None) == []
    assert _parse_errors_json([1, "a"]) == ["1", "a"]
    assert _parse_errors_json('["x", 2]') == ["x", "2"]
    assert _parse_errors_json('{"a": 1}') == []
    assert _parse_errors_json("not json") == []
    assert _parse_errors_json(42) == []


@pytest.mark.asyncio
async def test_allow_sync_fallback_env(app, monkeypatch):
    """The fallback flag honours the env var, else TESTING."""
    from apps.api.api.v1.csv_import import _allow_sync_fallback

    async with app.app_context():
        monkeypatch.setenv("CSV_IMPORT_ALLOW_SYNC_FALLBACK", "yes")
        assert _allow_sync_fallback() is True
        monkeypatch.setenv("CSV_IMPORT_ALLOW_SYNC_FALLBACK", "no")
        assert _allow_sync_fallback() is False
        monkeypatch.setenv("CSV_IMPORT_ALLOW_SYNC_FALLBACK", " ")
        assert _allow_sync_fallback() == bool(app.config.get("TESTING"))
        monkeypatch.delenv("CSV_IMPORT_ALLOW_SYNC_FALLBACK")
        assert _allow_sync_fallback() == bool(app.config.get("TESTING"))


@pytest.mark.asyncio
async def test_templates_endpoints(app, async_client):
    """Templates can be listed and downloaded; unknown ones are 404."""
    h = await _h(app, _ident(app.db, "admin"))
    r = await async_client.get(f"{BASE}/templates", headers=h)
    assert r.status_code == 200 and (await r.get_json())["templates"]
    r = await async_client.get(f"{BASE}/templates/entities/download", headers=h)
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    assert (
        await async_client.get(f"{BASE}/templates/zzz/download", headers=h)
    ).status_code == 404


@pytest.mark.asyncio
async def test_jobs_listing_and_detail(app, async_client):
    """Job listing paginates and filters; detail is tenant scoped."""
    iid = _ident(app.db, "admin")
    tid = app.db.identities[iid].tenant_id
    h = await _h(app, iid)
    job = f"csvimp-{uuid4().hex[:12]}"
    app.db.csv_import_jobs.insert(
        job_id=job,
        tenant_id=tid,
        resource="entities",
        filename="",
        status="pending",
        total_rows=3,
        errors_json=json.dumps(["bad"]),
        created_at=datetime.now(UTC),
    )
    app.db.commit()

    r = await async_client.get(
        f"{BASE}/jobs?resource=entities&status=pending", headers=h
    )
    body = await r.get_json()
    assert r.status_code == 200 and body["total"] == 1
    assert (
        body["items"][0]["errors"] == ["bad"]
        and body["items"][0]["storage_backend"] == "s3"
    )
    r = await async_client.get(f"{BASE}/jobs?status=done", headers=h)
    assert (await r.get_json())["pages"] == 0
    assert (await async_client.get(f"{BASE}/jobs?page=x", headers=h)).status_code == 400
    assert (
        await async_client.get(f"{BASE}/jobs?per_page=x", headers=h)
    ).status_code == 400

    assert (await async_client.get(f"{BASE}/jobs/{job}", headers=h)).status_code == 200
    assert (
        await async_client.get(f"{BASE}/jobs/missing", headers=h)
    ).status_code == 404
    other = await _h(app, _ident(app.db, "admin"))
    assert (
        await async_client.get(f"{BASE}/jobs/{job}", headers=other)
    ).status_code == 404

    with patch(f"{MOD}.get_current_tenant_id", return_value=None):
        assert (await async_client.get(f"{BASE}/jobs", headers=h)).status_code == 403
        assert (
            await async_client.get(f"{BASE}/jobs/{job}", headers=h)
        ).status_code == 403
        r = await async_client.post(
            f"{BASE}/entities", json={"csv_text": "x"}, headers=h
        )
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_import_validation(app, async_client):
    """Imports validate resource, scope, payload shape, size and headers."""
    h = await _h(app, _ident(app.db, "admin"))
    assert (
        await async_client.post(f"{BASE}/zzz", json={}, headers=h)
    ).status_code == 404
    viewer = await _h(app, _ident(app.db, "observer"))
    r = await async_client.post(
        f"{BASE}/entities", json={"csv_text": "a"}, headers=viewer
    )
    assert r.status_code == 403

    assert (
        await async_client.post(f"{BASE}/entities", json=[1], headers=h)
    ).status_code == 400
    r = await async_client.post(f"{BASE}/entities", json={"csv_text": 5}, headers=h)
    assert r.status_code == 400
    with patch(f"{MOD}.MAX_CSV_BYTES", 10):
        r = await async_client.post(
            f"{BASE}/entities", json={"csv_text": "x" * 50}, headers=h
        )
        assert r.status_code == 400 and "5 MB" in (await r.get_json())["error"]
    r = await async_client.post(
        f"{BASE}/entities", json={"csv_text": "wrong,headers\n1,2\n"}, headers=h
    )
    assert r.status_code == 400 and "expected_headers" in await r.get_json()
    r = await async_client.post(
        f"{BASE}/entities",
        json={
            "csv_text": _template_csv("entities", with_row=False),
            "filename": " my.csv ",
        },
        headers=h,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_import_file_upload_branches(app, async_client):
    """Multipart uploads handle size, encoding and success paths."""
    from quart.datastructures import FileStorage

    h = await _h(app, _ident(app.db, "admin"))
    good = _template_csv("entities").encode()

    def files(data: bytes, name: str = "e.csv"):
        """Build a multipart file mapping."""
        return {"file": FileStorage(io.BytesIO(data), filename=name)}

    with patch(f"{MOD}.MAX_CSV_BYTES", 10):
        r = await async_client.post(f"{BASE}/entities", files=files(good), headers=h)
        assert r.status_code == 400 and "size limit" in (await r.get_json())["error"]
    r = await async_client.post(
        f"{BASE}/entities", files=files(b"\xff\xfe\x00bad"), headers=h
    )
    assert r.status_code == 400 and "UTF-8" in (await r.get_json())["error"]

    outcome = SimpleNamespace(errors=[], imported_count=1)
    with patch(f"{MOD}.process_csv_text", return_value=outcome):
        r = await async_client.post(f"{BASE}/entities", files=files(good), headers=h)
        assert r.status_code == 201 and (await r.get_json())["imported"] == 1
        r = await async_client.post(
            f"{BASE}/entities", files=files(good, ""), headers=h
        )
        assert r.status_code == 201

    bad = SimpleNamespace(errors=["Row 2: nope"], imported_count=0)
    with patch(f"{MOD}.process_csv_text", return_value=bad):
        r = await async_client.post(f"{BASE}/entities", files=files(good), headers=h)
        assert r.status_code == 400 and (await r.get_json())["details"] == [
            "Row 2: nope"
        ]


@pytest.mark.asyncio
async def test_import_sync_fallback_disabled(app, async_client, monkeypatch):
    """Without S3 storage and without fallback, imports are refused (503)."""
    h = await _h(app, _ident(app.db, "admin"))
    monkeypatch.setenv("CSV_IMPORT_ALLOW_SYNC_FALLBACK", "false")
    with patch(
        f"{MOD}.get_csv_import_storage",
        return_value=SimpleNamespace(is_configured=False),
    ):
        r = await async_client.post(
            f"{BASE}/entities", json={"csv_text": _template_csv("entities")}, headers=h
        )
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_import_queues_batch_job(app, async_client):
    """With storage configured, imports are stored, recorded and enqueued (202)."""
    iid = _ident(app.db, "admin")
    h = await _h(app, iid)
    stored = SimpleNamespace(
        storage_backend="s3",
        storage_key="k/1.csv",
        file_sha256="ab" * 32,
        file_size_bytes=10,
    )
    storage = MagicMock(is_configured=True)
    storage.put_csv.return_value = stored
    redis = MagicMock()
    body = {"csv_text": _template_csv("entities")}
    with (
        patch(f"{MOD}.get_csv_import_storage", return_value=storage),
        patch.object(app, "redis_client", redis, create=True),
    ):
        r = await async_client.post(f"{BASE}/entities", json=body, headers=h)
        assert r.status_code == 202, await r.get_data()
        job = (await r.get_json())["job_id"]
        assert redis.lpush.call_args.args[1] == job
        row = app.db(app.db.csv_import_jobs.job_id == job).select().first()
        assert row.created_by_identity_id == iid

        redis.lpush.side_effect = RuntimeError("redis down")
        r = await async_client.post(f"{BASE}/entities", json=body, headers=h)
        assert r.status_code == 202

        storage.put_csv.side_effect = RuntimeError("s3 down")
        r = await async_client.post(f"{BASE}/entities", json=body, headers=h)
        assert r.status_code == 502
