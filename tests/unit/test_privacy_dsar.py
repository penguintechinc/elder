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
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from apps.api.services.privacy.service import LegalHoldError, PrivacyService


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


class TestPrivacyServiceExport:
    """GDPR Art. 15 / CCPA right-to-know."""

    def test_export_returns_scoped_dict_not_raw_row(self):
        db = MagicMock()
        db.identities.__getitem__.return_value = _fake_identity()
        db.tables = []  # no roles/group_memberships wired for this fake db

        result = PrivacyService.export_identity(db, 42)

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
        db.identities.__getitem__.return_value = None

        with pytest.raises(LookupError):
            PrivacyService.export_identity(db, 999)


class TestPrivacyServiceErasure:
    """GDPR Art. 17 right-to-erasure: anonymize, never hard-delete."""

    def test_erase_anonymizes_without_deleting_row(self):
        db = MagicMock()
        db.identities.__getitem__.return_value = _fake_identity()

        result = PrivacyService.erase_identity(db, 42, tenant_legal_hold=False)

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

        with pytest.raises(LegalHoldError):
            PrivacyService.erase_identity(db, 42, tenant_legal_hold=True)

        # Must never touch the identity row once blocked.
        db.identities.__getitem__.assert_not_called()
        assert not db.return_value.update.called

    def test_erase_missing_identity_raises_lookup_error(self):
        db = MagicMock()
        db.identities.__getitem__.return_value = None

        with pytest.raises(LookupError):
            PrivacyService.erase_identity(db, 999, tenant_legal_hold=False)


class TestPrivacyServiceConsent:
    """CCPA/CPRA 'Do Not Sell or Share' opt-out + consent withdrawal."""

    def test_opt_out_sets_flag_and_timestamp(self):
        db = MagicMock()
        db.identities.__getitem__.return_value = _fake_identity()

        result = PrivacyService.set_do_not_sell(db, 42, opted_out=True)

        assert result == {"identity_id": 42, "do_not_sell_share": True}
        update_kwargs = db.return_value.update.call_args.kwargs
        assert update_kwargs["do_not_sell_share"] is True
        assert update_kwargs["consent_withdrawn_at"] is not None

    def test_opt_in_clears_withdrawal_timestamp(self):
        db = MagicMock()
        db.identities.__getitem__.return_value = _fake_identity()

        PrivacyService.set_do_not_sell(db, 42, opted_out=False)

        update_kwargs = db.return_value.update.call_args.kwargs
        assert update_kwargs["do_not_sell_share"] is False
        assert update_kwargs["consent_withdrawn_at"] is None

    def test_consent_missing_identity_raises_lookup_error(self):
        db = MagicMock()
        db.identities.__getitem__.return_value = None

        with pytest.raises(LookupError):
            PrivacyService.set_do_not_sell(db, 999, opted_out=True)


class TestPrivacyServiceBulkErase:
    """Enterprise admin convenience layer's underlying batch operation."""

    def test_bulk_erase_collects_per_identity_results(self):
        db = MagicMock()
        db.identities.__getitem__.side_effect = [_fake_identity(id=1), None]

        result = PrivacyService.bulk_erase(db, [1, 2], tenant_legal_hold=False)

        assert result["results"][0] == {"identity_id": 1, "anonymized": True}
        assert result["results"][1]["anonymized"] is False
        assert "error" in result["results"][1]

    def test_bulk_erase_blocked_entirely_by_legal_hold(self):
        db = MagicMock()

        result = PrivacyService.bulk_erase(db, [1, 2], tenant_legal_hold=True)

        assert all(not r["anonymized"] for r in result["results"])
        db.identities.__getitem__.assert_not_called()


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
        db.identities.__getitem__.return_value = _fake_identity()
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
        from apps.api.api.v1 import privacy_admin

        gated = await self._tier_gated(privacy_admin.bulk_erase)
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
                    db = MagicMock()
                    db.identities.__getitem__.return_value = _fake_identity(id=1)
                    db.tenants.__getitem__.return_value = SimpleNamespace(
                        legal_hold=False
                    )
                    app.db = db
                    from quart import g

                    g.current_user = _fake_identity()
                    response, status = await gated()

        # Enterprise tier clears the gate -- the real handler body runs.
        assert status == 200
