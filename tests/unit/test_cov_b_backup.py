"""Unit tests for the backup and data-management API blueprint.

The real Quart app, auth and admin gate run; ``BackupService`` is replaced
with a mock so each endpoint's success, validation and error mapping can be
asserted deterministically.
"""

from __future__ import annotations

import io
import os
import tempfile
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash

BASE = "/api/v1/backup"


def _seed(app, role: str) -> int:
    """Insert a tenant plus identity with ``role`` and return the identity id."""
    db = app.db
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


@pytest.fixture
def admin(app):
    """Return headers factory state for an admin identity."""
    return _seed(app, "admin")


@pytest.fixture
def svc():
    """Patch the service factory and yield the mock service."""
    service = MagicMock()
    with patch("apps.api.api.v1.backup.get_backup_service", return_value=service):
        yield service


@pytest.mark.asyncio
async def test_backup_requires_admin(app, async_client, svc):
    """Non-admins are refused and anonymous callers get 401."""
    viewer = await _h(app, _seed(app, "observer"))
    assert (await async_client.get(f"{BASE}/jobs", headers=viewer)).status_code == 403
    assert (await async_client.get(f"{BASE}/jobs")).status_code == 401
    svc.list_backup_jobs.assert_not_called()


def test_get_backup_service_wraps_current_db(app):
    """The factory builds a BackupService around the app database."""
    import asyncio

    from apps.api.api.v1 import backup

    async def run():
        """Call the factory inside an app context."""
        async with app.app_context():
            assert backup.get_backup_service().db is app.db

    asyncio.run(run())


@pytest.mark.asyncio
async def test_job_endpoints_success(app, async_client, admin, svc):
    """Job CRUD and run endpoints return the service payloads."""
    h = await _h(app, admin)
    svc.list_backup_jobs.return_value = [{"id": 1}]
    r = await async_client.get(f"{BASE}/jobs?enabled=TRUE", headers=h)
    assert r.status_code == 200 and (await r.get_json())["count"] == 1
    svc.list_backup_jobs.assert_called_with(enabled=True)
    await async_client.get(f"{BASE}/jobs?enabled=no", headers=h)
    svc.list_backup_jobs.assert_called_with(enabled=False)
    await async_client.get(f"{BASE}/jobs", headers=h)
    svc.list_backup_jobs.assert_called_with(enabled=None)

    svc.create_backup_job.return_value = {"id": 2}
    r = await async_client.post(f"{BASE}/jobs", json={"name": "n"}, headers=h)
    assert r.status_code == 201
    svc.get_backup_job.return_value = {"id": 2}
    assert (await async_client.get(f"{BASE}/jobs/2", headers=h)).status_code == 200
    svc.update_backup_job.return_value = {"id": 2, "name": "x"}
    r = await async_client.put(f"{BASE}/jobs/2", json={"name": "x"}, headers=h)
    assert r.status_code == 200
    svc.delete_backup_job.return_value = {"deleted": True}
    assert (await async_client.delete(f"{BASE}/jobs/2", headers=h)).status_code == 200
    svc.run_backup_job.return_value = {"success": True}
    assert (await async_client.post(f"{BASE}/jobs/2/run", headers=h)).status_code == 202
    svc.run_backup_job.return_value = {"success": False}
    assert (await async_client.post(f"{BASE}/jobs/2/run", headers=h)).status_code == 500


@pytest.mark.asyncio
async def test_job_endpoint_validation(app, async_client, admin, svc):
    """Missing bodies and fields are rejected before the service is called."""
    h = await _h(app, admin)
    assert (
        await async_client.post(f"{BASE}/jobs", json={}, headers=h)
    ).status_code == 400
    r = await async_client.post(f"{BASE}/jobs", json={"x": 1}, headers=h)
    assert r.status_code == 400
    assert (
        await async_client.put(f"{BASE}/jobs/2", json={}, headers=h)
    ).status_code == 400
    svc.create_backup_job.assert_not_called()
    svc.update_backup_job.assert_not_called()


@pytest.mark.parametrize(
    ("method", "path", "attr", "body", "missing_status", "other_status"),
    [
        ("get", "/jobs", "list_backup_jobs", None, 500, 500),
        ("post", "/jobs", "create_backup_job", {"name": "n"}, 400, 400),
        ("get", "/jobs/3", "get_backup_job", None, 404, 500),
        ("put", "/jobs/3", "update_backup_job", {"name": "n"}, 404, 400),
        ("delete", "/jobs/3", "delete_backup_job", None, 404, 500),
        ("post", "/jobs/3/run", "run_backup_job", None, 404, 500),
        ("get", "", "list_backups", None, 500, 500),
        ("get", "/3", "get_backup", None, 404, 500),
        ("get", "/3/download", "get_backup_file_path", None, 404, 500),
        ("delete", "/3", "delete_backup", None, 404, 500),
        ("post", "/3/restore", "restore_backup", {}, 404, 400),
        (
            "post",
            "/export",
            "export_data",
            {"format": "json", "resource_types": ["a"]},
            400,
            400,
        ),
        ("get", "/stats", "get_backup_stats", None, 500, 500),
    ],
)
@pytest.mark.asyncio
async def test_service_errors_map_to_status(
    app,
    async_client,
    admin,
    svc,
    method,
    path,
    attr,
    body,
    missing_status,
    other_status,
):
    """A 'not found' error maps to 404 where supported, else to the default status."""
    h = await _h(app, admin)
    call = getattr(async_client, method)
    kwargs = {"headers": h}
    if body is not None:
        kwargs["json"] = body
    url = f"{BASE}{path}"

    getattr(svc, attr).side_effect = RuntimeError("Thing not found")
    assert (await call(url, **kwargs)).status_code == missing_status
    getattr(svc, attr).side_effect = RuntimeError("kaboom")
    assert (await call(url, **kwargs)).status_code == other_status


@pytest.mark.asyncio
async def test_backup_listing_and_detail(app, async_client, admin, svc):
    """List, detail and stats endpoints pass filters to the service."""
    h = await _h(app, admin)
    svc.list_backups.return_value = [{"id": 1}, {"id": 2}]
    r = await async_client.get(f"{BASE}?job_id=4&limit=7", headers=h)
    assert (await r.get_json())["count"] == 2
    assert svc.list_backups.call_args.kwargs["job_id"] == 4
    svc.get_backup.return_value = {"id": 9}
    assert (await async_client.get(f"{BASE}/9", headers=h)).status_code == 200
    svc.delete_backup.return_value = {"ok": True}
    assert (await async_client.delete(f"{BASE}/9", headers=h)).status_code == 200
    svc.get_backup_stats.return_value = {"total": 1}
    assert (await async_client.get(f"{BASE}/stats", headers=h)).status_code == 200


@pytest.mark.xfail(
    strict=True, reason="bug: sync handler returns un-awaited quart send_file coroutine"
)
@pytest.mark.asyncio
async def test_backup_download(app, async_client, admin, svc, tmp_path):
    """Downloads stream the file path returned by the service."""
    h = await _h(app, admin)
    f = tmp_path / "b.json.gz"
    f.write_bytes(b"data")
    svc.get_backup_file_path.return_value = str(f)
    r = await async_client.get(f"{BASE}/5/download", headers=h)
    assert r.status_code == 200 and await r.get_data() == b"data"


@pytest.mark.asyncio
async def test_restore_and_export(app, async_client, admin, svc):
    """Restore and export forward their options and report 202."""
    h = await _h(app, admin)
    svc.restore_backup.return_value = {"restored": 1}
    r = await async_client.post(
        f"{BASE}/5/restore",
        json={"dry_run": True, "restore_options": {"tables": ["a"]}},
        headers=h,
    )
    assert (
        r.status_code == 202 and svc.restore_backup.call_args.kwargs["dry_run"] is True
    )
    r = await async_client.post(f"{BASE}/5/restore", headers=h)
    assert r.status_code in (202, 400)

    assert (
        await async_client.post(f"{BASE}/export", json={}, headers=h)
    ).status_code == 400
    r = await async_client.post(f"{BASE}/export", json={"format": "json"}, headers=h)
    assert r.status_code == 400
    assert "resource_types" in (await r.get_json())["error"]
    svc.export_data.return_value = {"file": "x"}
    r = await async_client.post(
        f"{BASE}/export",
        json={"format": "json", "resource_types": ["entity"], "filters": {}},
        headers=h,
    )
    assert r.status_code == 202


@pytest.mark.asyncio
async def test_export_download_missing(app, async_client, admin):
    """Export downloads of unknown files are a 404."""
    h = await _h(app, admin)
    r = await async_client.get(f"{BASE}/export/does-not-exist.json/download", headers=h)
    assert r.status_code == 404


@pytest.mark.xfail(
    strict=True, reason="bug: sync handler returns un-awaited quart send_file coroutine"
)
@pytest.mark.asyncio
async def test_export_download(app, async_client, admin):
    """Export downloads serve existing sanitised temp files."""
    h = await _h(app, admin)
    name = f"covb-{uuid4().hex[:8]}.json"
    path = os.path.join(tempfile.gettempdir(), name)
    with open(path, "wb") as fh:
        fh.write(b"{}")
    try:
        r = await async_client.get(f"{BASE}/export/{name}/download", headers=h)
        assert r.status_code == 200 and await r.get_data() == b"{}"
    finally:
        os.remove(path)


@pytest.mark.xfail(
    strict=True,
    reason="bug: sync import handler reads request.files (a coroutine in Quart)",
)
@pytest.mark.asyncio
async def test_import_data(app, async_client, admin, svc):
    """Import validates the upload and cleans the temp file unless dry-run."""
    from quart.datastructures import FileStorage

    h = await _h(app, admin)
    r = await async_client.post(f"{BASE}/import", headers=h)
    assert r.status_code == 400
    r = await async_client.post(
        f"{BASE}/import",
        files={"file": FileStorage(io.BytesIO(b"{}"), filename="")},
        headers=h,
    )
    assert r.status_code == 400

    svc.import_data.return_value = {"imported": 1}
    name = f"covb-{uuid4().hex[:8]}.json"
    tmp = os.path.join(tempfile.gettempdir(), name)
    r = await async_client.post(
        f"{BASE}/import",
        files={"file": FileStorage(io.BytesIO(b"{}"), filename=name)},
        headers=h,
    )
    assert r.status_code == 202
    assert not os.path.exists(tmp)

    r = await async_client.post(
        f"{BASE}/import",
        files={"file": FileStorage(io.BytesIO(b"{}"), filename=name)},
        form={"dry_run": "true"},
        headers=h,
    )
    assert r.status_code == 202
    assert os.path.exists(tmp)
    os.remove(tmp)

    svc.import_data.side_effect = RuntimeError("bad")
    r = await async_client.post(
        f"{BASE}/import",
        files={"file": FileStorage(io.BytesIO(b"{}"), filename=name)},
        headers=h,
    )
    assert r.status_code == 400
    if os.path.exists(tmp):
        os.remove(tmp)


@pytest.mark.asyncio
async def test_download_export_unexpected_error(app, async_client, admin):
    """An unexpected filesystem error is reported as a generic 500."""
    h = await _h(app, admin)
    with patch("apps.api.api.v1.backup.os.path.exists", side_effect=OSError("io")):
        r = await async_client.get(f"{BASE}/export/a.json/download", headers=h)
    assert r.status_code == 500
