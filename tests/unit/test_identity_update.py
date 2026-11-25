"""Unit tests for update_identity (PUT/PATCH /api/v1/identities/<id>)."""

import inspect
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from werkzeug.security import check_password_hash


def _make_mock_db(identity_id: int = 1):
    """Build a minimal PyDAL-like mock DB for update_identity tests."""
    now = datetime.now(UTC)
    row = MagicMock()
    row.as_dict.return_value = {
        "id": identity_id,
        "username": "alice",
        "email": "alice@example.com",
        "identity_type": "human",
        "tenant_id": 1,
        "auth_provider": "local",
        "auth_provider_id": None,
        "full_name": "Alice",
        "is_active": True,
        "last_login_at": None,
        "created_at": now,
        "updated_at": now,
    }

    query_mock = MagicMock()
    db = MagicMock(return_value=query_mock)
    db.identities = MagicMock()
    db.identities.id = "identities.id"
    db.identities.__getitem__.return_value = row
    return db, query_mock, row


async def _run_sync(fn):
    return fn()


@pytest.mark.asyncio
async def test_update_identity_password_hashes_and_updates():
    """Updating password sets password_hash (hashed, not plaintext) and succeeds."""
    from apps.api.api.v1.identities import update_identity
    from apps.api.models.pydantic.identity import UpdateIdentityRequest

    raw_fn = inspect.unwrap(update_identity)
    db, query_mock, existing_row = _make_mock_db(identity_id=1)
    mock_app = MagicMock(db=db)
    body = UpdateIdentityRequest(password="NewSecurePass123!")

    with (
        patch("apps.api.api.v1.identities.current_app", mock_app),
        patch("apps.api.api.v1.identities.get_current_tenant_id", return_value=1),
        patch(
            "apps.api.api.v1.identities.get_tenant_scoped",
            return_value=existing_row,
        ),
        patch(
            "apps.api.api.v1.identities.run_in_threadpool",
            side_effect=_run_sync,
        ),
    ):
        dto, status = await raw_fn(1, body)

    assert status == 200
    assert dto.id == 1
    query_mock.update.assert_called_once()
    update_kwargs = query_mock.update.call_args.kwargs
    assert "password_hash" in update_kwargs
    assert update_kwargs["password_hash"] != "NewSecurePass123!"
    assert check_password_hash(update_kwargs["password_hash"], "NewSecurePass123!")
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_update_identity_empty_payload_skips_db_update():
    """Empty update payload does NOT execute .update() with empty kwargs (no 500)."""
    from apps.api.api.v1.identities import update_identity
    from apps.api.models.pydantic.identity import UpdateIdentityRequest

    raw_fn = inspect.unwrap(update_identity)
    db, query_mock, existing_row = _make_mock_db(identity_id=1)
    mock_app = MagicMock(db=db)
    body = UpdateIdentityRequest()

    with (
        patch("apps.api.api.v1.identities.current_app", mock_app),
        patch("apps.api.api.v1.identities.get_current_tenant_id", return_value=1),
        patch(
            "apps.api.api.v1.identities.get_tenant_scoped",
            return_value=existing_row,
        ),
        patch(
            "apps.api.api.v1.identities.run_in_threadpool",
            side_effect=_run_sync,
        ),
    ):
        dto, status = await raw_fn(1, body)

    assert status == 200
    assert dto.id == 1
    query_mock.update.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_update_identity_mfa_org_and_portal_role():
    """mfa_enabled, organization_id, and portal_role are updated when provided."""
    from apps.api.api.v1.identities import update_identity
    from apps.api.models.pydantic.identity import UpdateIdentityRequest

    raw_fn = inspect.unwrap(update_identity)
    db, query_mock, existing_row = _make_mock_db(identity_id=1)
    mock_app = MagicMock(db=db)
    body = UpdateIdentityRequest(
        mfa_enabled=True,
        organization_id=42,
        portal_role="editor",
    )

    with (
        patch("apps.api.api.v1.identities.current_app", mock_app),
        patch("apps.api.api.v1.identities.get_current_tenant_id", return_value=1),
        patch(
            "apps.api.api.v1.identities.get_tenant_scoped",
            return_value=existing_row,
        ),
        patch(
            "apps.api.api.v1.identities.run_in_threadpool",
            side_effect=_run_sync,
        ),
    ):
        dto, status = await raw_fn(1, body)

    assert status == 200
    assert dto.id == 1
    query_mock.update.assert_called_once_with(
        mfa_enabled=True,
        organization_id=42,
        portal_role="editor",
    )
    db.commit.assert_called_once()
