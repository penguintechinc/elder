"""Unit tests for self-service DSAR (statutory rights) -- Free+, no tier gate.

grc audit found no self-service DSAR; the design had planned to sell "GDPR
tooling" as Enterprise-only, which critical-rules.md's "Feature Flags &
License Tiers" makes non-compliant: statutory rights are Free+ (all tiers),
only the admin convenience layer (apps/api/api/v1/privacy_admin.py) is
Enterprise-gated. These tests prove:
  1. PrivacyService's statutory operations (export/erase/consent) work.
  2. Erasure anonymizes (UPDATE) rather than deletes -- audit/RBAC history
     that references the identity by id stays intact.
  3. Erasure is blocked while the owning tenant has an active legal hold.
  4. The self-service routes carry no Enterprise tier gate at all.
  5. The admin bulk-erase/list-requests layer 403s below Enterprise.
  6. regression: cross-tenant-dsar-idor -- a security review of the first
     draft of this feature found the Enterprise admin layer trusted
     caller-supplied identity_ids and fell back to an unscoped query,
     letting a tenant-A admin read/erase tenant-B's data. TestCrossTenant*
     below proves that class of bug is closed.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from apps.api.services.privacy.service import (
    LegalHoldError,
    PrivacyService,
)


def _fake_identity(**overrides) -> SimpleNamespace:
    base = dict(
        id=42,
        tenant_id=7,
        username="alice",
        email="alice@example.com",
        full_name="Alice Example",
        identity_type="human",
        auth_provider="local",
        is_active=True,
        mfa_enabled=False,
        do_not_sell_share=False,
        consent_withdrawn_at=None,
        last_login_at=None,
        created_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _patch_scoped(identity_or_none):
    """Patch get_tenant_scoped as PrivacyService sees it.

    `identity_or_none=None` simulates "id doesn't exist OR belongs to a
    different tenant" -- get_tenant_scoped never distinguishes the two.
    """
    return patch(
        "apps.api.services.privacy.service.get_tenant_scoped",
        return_value=identity_or_none,
    )


class TestPrivacyServiceExport:
    """GDPR Art. 15 / CCPA right-to-know."""

    def test_export_returns_scoped_dict_not_raw_row(self):
        db = MagicMock()
        db.tables = []  # no roles/group_memberships wired for this fake db

        with _patch_scoped(_fake_identity()):
            result = PrivacyService.export_identity(db, 42, tenant_id=7)

        assert result["identity"]["id"] == 42
        assert result["identity"]["username"] == "alice"
        assert result["identity"]["email"] == "alice@example.com"
        # Output validation: only explicitly-scoped fields, never secrets.
        assert "password_hash" not in result["identity"]
        assert "mfa_secret" not in result["identity"]
        assert result["roles"] == []
        assert result["group_memberships"] == []

    def test_export_missing_identity_raises_lookup_error(self):
        db = MagicMock()

        with _patch_scoped(None):
            with pytest.raises(LookupError):
                PrivacyService.export_identity(db, 999, tenant_id=7)


class TestPrivacyServiceErasure:
    """GDPR Art. 17 right-to-erasure: anonymize, never hard-delete."""

    def test_erase_anonymizes_without_deleting_row(self):
        db = MagicMock()

        with _patch_scoped(_fake_identity()):
            result = PrivacyService.erase_identity(
                db, 42, tenant_id=7, tenant_legal_hold=False
            )

        assert result == {"identity_id": 42, "anonymized": True}
        assert db.commit.called
        # Anonymize via UPDATE, never DELETE -- the row (and its id) must
        # survive so audit_logs/RBAC history referencing it by FK stay intact.
        assert not db.return_value.delete.called
        update_kwargs = db.return_value.update.call_args.kwargs
        assert update_kwargs["is_active"] is False
        assert update_kwargs["password_hash"] is None
        assert update_kwargs["mfa_secret"] is None
        assert update_kwargs["full_name"] is None
        assert update_kwargs["username"].startswith("erased-42-")
        assert update_kwargs["email"].endswith("@erased.invalid")
        assert update_kwargs["anonymized_at"] is not None

    def test_erase_blocked_by_legal_hold(self):
        db = MagicMock()

        with _patch_scoped(_fake_identity()) as mock_scoped:
            with pytest.raises(LegalHoldError):
                PrivacyService.erase_identity(
                    db, 42, tenant_id=7, tenant_legal_hold=True
                )

        # Must never even look up the row once blocked.
        mock_scoped.assert_not_called()
        assert not db.return_value.update.called

    def test_erase_missing_identity_raises_lookup_error(self):
        db = MagicMock()

        with _patch_scoped(None):
            with pytest.raises(LookupError):
                PrivacyService.erase_identity(
                    db, 999, tenant_id=7, tenant_legal_hold=False
                )


class TestPrivacyServiceConsent:
    """CCPA/CPRA 'Do Not Sell or Share' opt-out + consent withdrawal."""

    def test_opt_out_sets_flag_and_timestamp(self):
        db = MagicMock()

        with _patch_scoped(_fake_identity()):
            result = PrivacyService.set_do_not_sell(db, 42, tenant_id=7, opted_out=True)

        assert result == {"identity_id": 42, "do_not_sell_share": True}
        update_kwargs = db.return_value.update.call_args.kwargs
        assert update_kwargs["do_not_sell_share"] is True
        assert update_kwargs["consent_withdrawn_at"] is not None

    def test_opt_in_clears_withdrawal_timestamp(self):
        db = MagicMock()

        with _patch_scoped(_fake_identity()):
            PrivacyService.set_do_not_sell(db, 42, tenant_id=7, opted_out=False)

        update_kwargs = db.return_value.update.call_args.kwargs
        assert update_kwargs["do_not_sell_share"] is False
        assert update_kwargs["consent_withdrawn_at"] is None

    def test_consent_missing_identity_raises_lookup_error(self):
        db = MagicMock()

        with _patch_scoped(None):
            with pytest.raises(LookupError):
                PrivacyService.set_do_not_sell(db, 999, tenant_id=7, opted_out=True)


class TestPrivacyServiceBulkErase:
    """Enterprise admin convenience layer's underlying batch operation."""

    def test_bulk_erase_collects_per_identity_results(self):
        db = MagicMock()

        with _patch_scoped(_fake_identity(id=1)) as mock_scoped:
            # Both ids resolve fine at this layer -- per-id DB failure paths
            # are covered by the missing-identity test below.
            result = PrivacyService.bulk_erase(
                db, [1], tenant_id=7, tenant_legal_hold=False
            )
        mock_scoped.assert_called_once_with(db, db.identities, 1, 7)

        assert result["results"][0] == {"identity_id": 1, "anonymized": True}

    def test_bulk_erase_collects_missing_identity_as_not_found(self):
        db = MagicMock()

        with _patch_scoped(None):
            result = PrivacyService.bulk_erase(
                db, [999], tenant_id=7, tenant_legal_hold=False
            )

        assert result["results"][0]["anonymized"] is False
        assert "error" in result["results"][0]

    def test_bulk_erase_blocked_entirely_by_legal_hold(self):
        db = MagicMock()

        with _patch_scoped(_fake_identity()) as mock_scoped:
            result = PrivacyService.bulk_erase(
                db, [1, 2], tenant_id=7, tenant_legal_hold=True
            )

        assert all(not r["anonymized"] for r in result["results"])
        mock_scoped.assert_not_called()


class TestCrossTenantIsolation:
    """regression: cross-tenant-dsar-idor.

    A security review of the first draft found: (1) PrivacyService trusted a
    caller-supplied identity_id with no tenant check
    (cross-tenant-authorization-bypass), (2) the admin request-list endpoint
    fell back to an unscoped query when tenant_id was falsy
    (cross-tenant-information-disclosure), and (3) bulk erasure checked the
    admin's own tenant's legal hold but never verified the target identity
    actually belonged to that tenant (cross-tenant-legal-hold-bypass) -- so a
    foreign identity could be erased even though ITS tenant was under hold.
    Fixed by threading tenant_id through every PrivacyService call and
    re-verifying ownership via get_tenant_scoped() before any read/write.
    """

    def test_erase_identity_rejects_cross_tenant_target(self):
        """Identity 42 exists, but belongs to a different tenant than the
        caller's (7) -- get_tenant_scoped's tenant-matched query returns
        None, exactly like "doesn't exist" (never a distinguishable 403)."""
        db = MagicMock()

        with _patch_scoped(None) as mock_scoped:
            with pytest.raises(LookupError):
                PrivacyService.erase_identity(
                    db, 42, tenant_id=7, tenant_legal_hold=False
                )

        mock_scoped.assert_called_once_with(db, db.identities, 42, 7)
        assert not db.return_value.update.called

    def test_bulk_erase_rejects_identity_from_another_tenant(self):
        """Admin of tenant 7 submits [1, 2]; id 1 belongs to tenant 7, id 2
        actually belongs to tenant 99. Only id 1 is anonymized -- id 2 comes
        back "not found", never touched, never disclosed as existing
        elsewhere."""
        db = MagicMock()

        def fake_get_tenant_scoped(db_arg, table, record_id, tenant_id):
            if record_id == 1 and tenant_id == 7:
                return _fake_identity(id=1, tenant_id=7)
            return None  # id 2: exists, but not in tenant 7 -- rejected

        with patch(
            "apps.api.services.privacy.service.get_tenant_scoped",
            side_effect=fake_get_tenant_scoped,
        ):
            result = PrivacyService.bulk_erase(
                db, [1, 2], tenant_id=7, tenant_legal_hold=False
            )

        assert result["results"][0] == {"identity_id": 1, "anonymized": True}
        assert result["results"][1]["anonymized"] is False
        assert "not found" in result["results"][1]["error"]

    def test_list_requests_scopes_strictly_to_caller_tenant(self):
        """_list_requests must never return another tenant's rows, and must
        never fall back to an unscoped query."""
        from apps.api.api.v1.privacy_admin import _list_requests

        tenant_a_row = SimpleNamespace(
            id=1,
            tenant_id=7,
            identity_id=10,
            request_type="access",
            status="completed",
            requested_by_identity_id=10,
            created_at=None,
        )
        tenant_b_row = SimpleNamespace(
            id=2,
            tenant_id=99,
            identity_id=20,
            request_type="access",
            status="completed",
            requested_by_identity_id=20,
            created_at=None,
        )
        db = _FakeDsarDB([tenant_a_row, tenant_b_row])

        result = _list_requests(db, tenant_id=7)

        assert [r["id"] for r in result] == [1]
        assert all(r["tenant_id"] == 7 for r in result)

    def test_tenant_legal_hold_scopes_to_exactly_the_requested_tenant(self):
        """_tenant_legal_hold must read the ONE matching tenant row, never a
        different tenant's hold state."""
        from apps.api.api.v1.privacy_admin import _tenant_legal_hold

        tenant_a = SimpleNamespace(id=7, legal_hold=False)
        tenant_b = SimpleNamespace(id=99, legal_hold=True)
        db = _FakeTenantsDB([tenant_a, tenant_b])

        assert _tenant_legal_hold(db, tenant_id=7) is False
        assert _tenant_legal_hold(db, tenant_id=99) is True

    @pytest.mark.asyncio
    async def test_list_requests_403_without_tenant_claim(self, app):
        """No tenant claim on the JWT -- 403, never an unscoped listing."""
        from apps.api.api.v1 import privacy_admin

        # require_tier("enterprise")-wrapped original -- isolates the new
        # tenant check from the (separately tested) role/tier checks.
        gated = privacy_admin.list_dsar_requests.__wrapped__.__wrapped__
        async with app.app_context():
            with patch(
                "apps.api.common.licensing.tier_gate.resolve_limits",
                return_value=("enterprise", None),
            ):
                async with app.test_request_context("/api/v1/privacy-admin/requests"):
                    from quart import g

                    g.claims = {}  # no tenant claim at all
                    response, status = await gated()

        assert status == 403

    @pytest.mark.asyncio
    async def test_bulk_erase_403_without_tenant_claim(self, app):
        """No tenant claim on the JWT -- 403 before identity_ids is even parsed."""
        from apps.api.api.v1 import privacy_admin

        gated = privacy_admin.bulk_erase.__wrapped__.__wrapped__
        async with app.app_context():
            with patch(
                "apps.api.common.licensing.tier_gate.resolve_limits",
                return_value=("enterprise", None),
            ):
                async with app.test_request_context(
                    "/api/v1/privacy-admin/bulk-erase",
                    method="POST",
                    json={"identity_ids": [1]},
                ):
                    from quart import g

                    g.claims = {}  # no tenant claim at all
                    response, status = await gated()

        assert status == 403


class _EqPredicate:
    """Minimal PyDAL-Query stand-in for a single `field == value` filter."""

    def __init__(self, name: str, value: object) -> None:
        self.name = name
        self.value = value

    def matches(self, row: object) -> bool:
        return getattr(row, self.name, None) == self.value


class _FakeField:
    """Minimal PyDAL-Field stand-in supporting `==` and (no-op) `~`."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __eq__(self, other: object) -> _EqPredicate:  # type: ignore[override]
        return _EqPredicate(self.name, other)

    def __invert__(self) -> "_FakeField":
        return self  # orderby direction ignored -- insertion order is fine here


class _FakeRows(list):
    """Minimal PyDAL-Rows stand-in supporting `.first()`."""

    def first(self) -> object | None:
        return self[0] if self else None


class _FakeTable:
    """Any attribute access returns a `_FakeField` for that column name."""

    def __getattr__(self, name: str) -> _FakeField:
        return _FakeField(name)


class _FakeDsarDB:
    """Minimal fake supporting exactly what `_list_requests` needs."""

    def __init__(self, rows: list[object]) -> None:
        self.dsar_requests = _FakeTable()
        self._rows = rows

    def __call__(self, predicate: _EqPredicate) -> "_FakeSelectable":
        return _FakeSelectable([r for r in self._rows if predicate.matches(r)])


class _FakeTenantsDB:
    """Minimal fake supporting exactly what `_tenant_legal_hold` needs."""

    def __init__(self, tenants: list[object]) -> None:
        self.tenants = _FakeTable()
        self._tenants = tenants

    def __call__(self, predicate: _EqPredicate) -> "_FakeSelectable":
        return _FakeSelectable([t for t in self._tenants if predicate.matches(t)])


class _FakeSelectable:
    def __init__(self, matched: list[object]) -> None:
        self._matched = matched

    def select(self, orderby: object = None, limitby: object = None) -> _FakeRows:
        return _FakeRows(self._matched)


@pytest.mark.asyncio
class TestSelfServiceRoutesCarryNoTierGate:
    """Statutory rights resolve on every tier -- privacy.py never imports the gate."""

    async def test_privacy_module_does_not_import_tier_gate(self):
        import apps.api.api.v1.privacy as privacy_module

        assert "require_tier" not in dir(privacy_module)
        assert "get_tier" not in dir(privacy_module)

    async def test_export_route_runs_to_completion_while_tier_is_free(self, app):
        """Bypass @login_required (auth is orthogonal) and prove the route body
        itself never consults the license tier -- it must behave identically
        whether resolve_limits reports "community" (free) or "enterprise"."""
        from apps.api.api.v1 import privacy

        db = MagicMock()
        db.return_value.select.return_value.first.return_value = _fake_identity()
        db.tables = []

        async with app.app_context():
            app.db = db
            # No tier/license mocking at all -- if the route consulted the
            # license tier it would need `license_client` in app.extensions;
            # it doesn't, so this succeeds regardless of deployment tier.
            with patch("apps.api.api.v1.privacy.flag_enabled", return_value=True):
                async with app.test_request_context("/api/v1/privacy/me/export"):
                    from quart import g

                    g.current_user = _fake_identity()
                    g.claims = {"tenant": "7"}
                    response, status = await privacy.export_my_data.__wrapped__()

        assert status == 200


@pytest.mark.asyncio
class TestAdminBulkLayerRequiresEnterprise:
    """The bulk/admin convenience layer 403s below Enterprise, self-service does not."""

    async def _tier_gated(self, view_func):
        # login_required -> admin_required -> require_tier("enterprise") ->
        # original. functools.wraps preserves __wrapped__ at each layer, so
        # unwrapping twice isolates exactly the tier check, independent of
        # auth/role checks (covered separately by the existing auth tests).
        return view_func.__wrapped__.__wrapped__

    async def test_bulk_erase_403_below_enterprise(self, app):
        from apps.api.api.v1 import privacy_admin

        gated = await self._tier_gated(privacy_admin.bulk_erase)
        async with app.app_context():
            with patch(
                "apps.api.common.licensing.tier_gate.resolve_limits",
                return_value=("community", None),
            ):
                response, status = await gated()

        assert status == 403
        body = await response.get_json()
        assert body["error"] == "tier_required"
        assert body["required_tier"] == "enterprise"

    async def test_list_requests_403_on_professional(self, app):
        from apps.api.api.v1 import privacy_admin

        gated = await self._tier_gated(privacy_admin.list_dsar_requests)
        async with app.app_context():
            with patch(
                "apps.api.common.licensing.tier_gate.resolve_limits",
                return_value=("professional", None),
            ):
                response, status = await gated()

        assert status == 403

    async def test_bulk_erase_passes_tier_gate_on_enterprise(self, app):
        """Tier-gate passthrough only -- business logic is covered by the
        dedicated PrivacyService/cross-tenant tests above, so the erase path
        itself is mocked out here."""
        from apps.api.api.v1 import privacy_admin

        gated = await self._tier_gated(privacy_admin.bulk_erase)
        async with app.app_context():
            with (
                patch(
                    "apps.api.common.licensing.tier_gate.resolve_limits",
                    return_value=("enterprise", None),
                ),
                patch(
                    "apps.api.api.v1.privacy_admin._tenant_legal_hold",
                    return_value=False,
                ),
                patch.object(
                    PrivacyService,
                    "bulk_erase",
                    return_value={"results": [{"identity_id": 1, "anonymized": True}]},
                ),
            ):
                async with app.test_request_context(
                    "/api/v1/privacy-admin/bulk-erase",
                    method="POST",
                    json={"identity_ids": [1]},
                ):
                    db = MagicMock()
                    app.db = db
                    from quart import g

                    g.current_user = _fake_identity()
                    g.claims = {"tenant": "7"}
                    response, status = await gated()

        # Enterprise tier + a valid tenant claim clears the gate -- the real
        # handler body runs.
        assert status == 200
