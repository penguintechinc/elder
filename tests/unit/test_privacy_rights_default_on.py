"""Statutory privacy rights are always on -- no flag, no tier (compliance audit H1).

The self-service DSAR routes used to return 404 unless the default-OFF
PostHog flag `elder.dsar-self-service` was ON, so a never-seen flag or an
unreachable flag server (every air-gapped install) denied a legal right.
These tests prove the routes now work with the flag server down, with the
flag never seen, and on the Free tier; that the subject is taken from the
authenticated principal (identity vs portal user, never a machine token);
that another tenant's subject and a legal hold are refused clearly; and that
the admin bulk layer is still Enterprise-gated.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from apps.api.api.v1 import privacy, privacy_admin
from apps.api.services.privacy.inventory import ConsentPurpose, SubjectType
from apps.api.services.privacy.service import (
    LastAdminError,
    LegalHoldError,
    PrivacyService,
)

pytestmark = pytest.mark.asyncio

# (handler, HTTP method, path, JSON body, PrivacyService method it calls)
ROUTES = [
    (privacy.export_my_data, "GET", "/me/export", None, "export_identity"),
    (
        privacy.erase_my_data,
        "POST",
        "/me/erase",
        {"confirm": "ERASE", "password": "pw"},
        "erase_identity",
    ),
    (privacy.get_my_consent, "GET", "/me/consent", None, "get_consent"),
    (
        privacy.set_my_consent,
        "POST",
        "/me/consent",
        {"do_not_sell_share": True},
        "record_consent",
    ),
    (
        privacy.withdraw_my_consent,
        "POST",
        "/me/consent/withdraw",
        None,
        "withdraw_all_consent",
    ),
]
ROUTE_IDS = [r[4] for r in ROUTES]


@pytest.fixture(autouse=True)
def _reauth_and_limiter(request):
    """Default every test to "re-auth succeeds, not rate limited".

    Classes setting ``REAL_GUARDS = True`` opt out and drive the guards themselves.
    """
    if getattr(request.cls, "REAL_GUARDS", False):
        yield
        return

    async def _allow(*args, **kwargs):
        return True, {}

    with (
        patch.object(PrivacyService, "has_local_credential", return_value=True),
        patch.object(PrivacyService, "verify_reauth", return_value=True),
        patch("apps.api.api.v1.privacy.check_subject_rate_limit", _allow),
    ):
        yield


def _identity(**overrides) -> SimpleNamespace:
    """A stand-in identities principal (tenant 7, id 42)."""
    base = {"id": 42, "tenant_id": 7, "username": "alice", "is_active": True}
    base.update(overrides)
    return SimpleNamespace(**base)


def _portal_principal() -> SimpleNamespace:
    """A stand-in for jwt_handler's synthetic PortalUserIdentity (portal id 42)."""
    return SimpleNamespace(id=42, tenant_id=7, _portal_user=SimpleNamespace(id=42))


async def _call(app, route, *, user, claims, service_result=None, side_effect=None):
    """Invoke a route body (past @login_required) with a mocked PrivacyService.

    Returns (status, parsed JSON body, the PrivacyService mock).
    """
    handler, method, path, body, service_method = route
    db = MagicMock()
    db.return_value.select.return_value.first.return_value = SimpleNamespace(
        legal_hold=False
    )
    kwargs = {"method": method}
    if body is not None:
        kwargs["json"] = body
    async with app.app_context():
        with (
            patch.object(app, "db", db),
            patch.object(
                PrivacyService,
                service_method,
                return_value=service_result or {"identity_id": user.id},
                side_effect=side_effect,
            ) as service,
        ):
            async with app.test_request_context(f"/api/v1/privacy{path}", **kwargs):
                from quart import g

                g.current_user = user
                g.claims = claims
                response, status = await handler.__wrapped__()
                payload = await response.get_json()
    return status, payload, service


class TestNoFlagGate:
    """H1: the statutory routes do not depend on PostHog in any way."""

    def test_privacy_module_has_no_flag_dependency(self):
        source = inspect.getsource(privacy)
        assert "flag_enabled" not in dir(privacy)
        assert "posthog_client" not in source
        assert "FLAG_KEY" not in dir(privacy)
        assert not hasattr(privacy, "_flag_on")
        assert "feature_disabled" not in source

    def test_no_kill_switch_can_disable_a_statutory_right(self):
        """Not inverted into an opt-out flag either: nothing remote can turn it off."""
        for module in (
            privacy,
            __import__("apps.api.services.privacy.service", fromlist=["x"]),
        ):
            source = inspect.getsource(module)
            assert "import flag_enabled" not in source
            assert "get_posthog_client" not in source

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
    async def test_route_works_when_flag_server_is_unreachable(self, app, route):
        """A flag backend that errors on every call must not affect the right."""
        boom = ConnectionError("flag server unreachable")
        with (
            patch(
                "apps.api.common.flags.posthog_client.flag_enabled", side_effect=boom
            ) as module_flag,
            patch(
                "apps.api.common.flags.posthog_client.PostHogClient.flag_enabled",
                side_effect=boom,
            ) as client_flag,
        ):
            status, _, service = await _call(
                app, route, user=_identity(), claims={"tenant": "7"}
            )
        assert status == 200
        assert service.call_count == 1
        assert module_flag.call_count == 0
        assert client_flag.call_count == 0

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
    async def test_route_works_when_flag_was_never_seen(self, app, route):
        """A never-seen flag evaluates OFF; the right must still be served."""
        with patch(
            "apps.api.common.flags.posthog_client.PostHogClient.flag_enabled",
            return_value=False,
        ):
            status, _, service = await _call(
                app, route, user=_identity(), claims={"tenant": "7"}
            )
        assert status == 200
        assert service.call_count == 1


class TestNoTierGate:
    """Statutory rights ship in every tier, including Free."""

    def test_privacy_module_does_not_import_tier_gate(self):
        assert "require_tier" not in dir(privacy)
        assert "get_tier" not in dir(privacy)
        assert "tier_gate" not in inspect.getsource(privacy)

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
    @pytest.mark.parametrize("tier", ["community", "professional", "enterprise"])
    async def test_route_allowed_on_every_tier(self, app, route, tier):
        with patch(
            "apps.api.common.licensing.tier_gate.resolve_limits",
            return_value=(tier, None),
        ) as limits:
            status, _, _ = await _call(
                app, route, user=_identity(), claims={"tenant": "7"}
            )
        assert status == 200
        assert limits.call_count == 0  # the tier is never even consulted


class TestSubjectResolution:
    """The subject is the authenticated principal -- never guessed from the id."""

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
    async def test_identity_principal_acts_on_identities(self, app, route):
        _, _, service = await _call(
            app, route, user=_identity(), claims={"tenant": "7"}
        )
        args = service.call_args.args
        assert args[1:3] == (42, 7)  # (subject id, tenant from JWT claims)
        # Identity (not ==) comparison: the IntEnum codes of different enums
        # compare equal, e.g. ConsentPurpose.SALE_OR_SHARING == 1.
        assert any(a is SubjectType.IDENTITY for a in args)
        assert not any(a is SubjectType.PORTAL_USER for a in args)

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
    async def test_portal_principal_acts_on_portal_users_not_identity_42(
        self, app, route
    ):
        """regression: a portal user's id was used as an identities.id."""
        _, _, service = await _call(
            app, route, user=_portal_principal(), claims={"tenant": "7"}
        )
        args = service.call_args.args
        assert args[1:3] == (42, 7)
        assert any(a is SubjectType.PORTAL_USER for a in args)
        assert not any(a is SubjectType.IDENTITY for a in args)

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
    @pytest.mark.parametrize("bad_id", [0, None, True, "42"])
    async def test_machine_principal_is_not_a_data_subject(self, app, route, bad_id):
        status, payload, service = await _call(
            app, route, user=_identity(id=bad_id), claims={"tenant": "7"}
        )
        assert status == 403
        assert "data_subject_required" in str(payload)
        assert service.call_count == 0

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
    @pytest.mark.parametrize("claims", [{}, {"tenant": ""}, {"tenant": "abc"}])
    async def test_missing_tenant_claim_is_refused(self, app, route, claims):
        status, payload, service = await _call(
            app, route, user=_identity(), claims=claims
        )
        assert status == 403
        assert "tenant_required" in str(payload)
        assert service.call_count == 0

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
    async def test_tenant_comes_from_claims_not_from_the_user_row(self, app, route):
        """A principal row claiming tenant 999 is still scoped to the JWT's tenant 7."""
        _, _, service = await _call(
            app, route, user=_identity(tenant_id=999), claims={"tenant": "7"}
        )
        assert service.call_args.args[2] == 7

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
    async def test_subject_of_another_tenant_is_not_found(self, app, route):
        """The service's tenant-scoped lookup fails closed -> 404, never 403/200."""
        status, _, _ = await _call(
            app,
            route,
            user=_identity(),
            claims={"tenant": "7"},
            side_effect=LookupError("subject 42 not found"),
        )
        assert status == 404


class TestErasureResponses:
    """Legal hold is reported clearly; a successful erase returns the receipt."""

    async def test_legal_hold_returns_409_with_clear_message(self, app):
        status, payload, _ = await _call(
            app,
            ROUTES[1],
            user=_identity(),
            claims={"tenant": "7"},
            side_effect=LegalHoldError(
                "erasure blocked: tenant is under an active legal hold"
            ),
        )
        assert status == 409
        assert "legal hold" in str(payload)
        assert payload.get("legal_hold") is True

    async def test_route_passes_the_tenants_hold_state_to_the_service(self, app):
        handler = privacy.erase_my_data
        db = MagicMock()
        db.return_value.select.return_value.first.return_value = SimpleNamespace(
            legal_hold=True
        )
        async with app.app_context():
            with (
                patch.object(app, "db", db),
                patch.object(
                    PrivacyService,
                    "erase_identity",
                    side_effect=LegalHoldError("erasure blocked: legal hold"),
                ) as service,
            ):
                async with app.test_request_context(
                    "/api/v1/privacy/me/erase",
                    method="POST",
                    json={"confirm": "ERASE", "password": "pw"},
                ):
                    from quart import g

                    g.current_user = _identity()
                    g.claims = {"tenant": "7"}
                    _, status = await handler.__wrapped__()
        assert status == 409
        assert service.call_args.args[3] is True

    async def test_successful_erase_returns_receipt_and_drops_session_cookie(self, app):
        receipt = {"receipt_id": "r-1", "tables": [], "not_erased_synchronously": []}
        handler = privacy.erase_my_data
        db = MagicMock()
        db.return_value.select.return_value.first.return_value = SimpleNamespace(
            legal_hold=False
        )
        async with app.app_context():
            with (
                patch.object(app, "db", db),
                patch.object(
                    PrivacyService,
                    "erase_identity",
                    return_value={
                        "identity_id": 42,
                        "anonymized": True,
                        "already_erased": False,
                        "receipt": receipt,
                    },
                ),
            ):
                async with app.test_request_context(
                    "/api/v1/privacy/me/erase",
                    method="POST",
                    json={"confirm": "ERASE", "password": "pw"},
                ):
                    from quart import g

                    g.current_user = _identity()
                    g.claims = {"tenant": "7"}
                    response, status = await handler.__wrapped__()
                    payload = await response.get_json()
        assert status == 200
        assert payload["receipt"] == receipt
        cookies = response.headers.getlist("Set-Cookie")
        assert any("elder_session=;" in c or "Max-Age=0" in c for c in cookies)
        # The erasure is written to the DSAR activity log for the subject.
        logged = db.dsar_requests.insert.call_args.kwargs
        assert logged["identity_id"] == 42
        assert logged["request_type"] == "erasure"
        assert "portal_user_id" not in logged


class TestConsentEndpoint:
    """Do-Not-Sell opt-out and per-purpose consent grant/withdrawal."""

    @pytest.mark.parametrize(
        ("body", "purpose", "granted"),
        [
            ({"do_not_sell_share": True}, ConsentPurpose.SALE_OR_SHARING, False),
            ({"do_not_sell_share": False}, ConsentPurpose.SALE_OR_SHARING, True),
            ({"purpose": 2, "granted": True}, ConsentPurpose.ANALYTICS, True),
            ({"purpose": 3, "granted": False}, ConsentPurpose.MARKETING, False),
        ],
    )
    async def test_valid_bodies_reach_the_ledger(self, app, body, purpose, granted):
        route = (privacy.set_my_consent, "POST", "/me/consent", body, "record_consent")
        status, _, service = await _call(
            app, route, user=_identity(), claims={"tenant": "7"}
        )
        assert status == 200
        assert service.call_args.args[3:5] == (purpose, granted)

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"do_not_sell_share": "yes"},
            {"do_not_sell_share": 1},
            {"purpose": 99, "granted": True},
            {"purpose": "analytics", "granted": True},
            {"purpose": True, "granted": True},
            {"purpose": 2},
            {"purpose": 2, "granted": "no"},
            {"granted": False},
        ],
    )
    async def test_invalid_bodies_are_rejected_before_any_write(self, app, body):
        route = (privacy.set_my_consent, "POST", "/me/consent", body, "record_consent")
        status, _, service = await _call(
            app, route, user=_identity(), claims={"tenant": "7"}
        )
        assert status == 400
        assert service.call_count == 0

    async def test_portal_users_consent_is_logged_against_the_portal_user(self, app):
        handler = privacy.set_my_consent
        db = MagicMock()
        async with app.app_context():
            with (
                patch.object(app, "db", db),
                patch.object(PrivacyService, "record_consent", return_value={}),
            ):
                async with app.test_request_context(
                    "/api/v1/privacy/me/consent",
                    method="POST",
                    json={"do_not_sell_share": True},
                ):
                    from quart import g

                    g.current_user = _portal_principal()
                    g.claims = {"tenant": "7"}
                    _, status = await handler.__wrapped__()
        assert status == 200
        logged = db.dsar_requests.insert.call_args.kwargs
        assert logged["portal_user_id"] == 42
        assert "identity_id" not in logged
        assert logged["request_type"] == "consent_opt_out"

    async def test_activity_log_failure_never_blocks_the_right(self, app):
        handler = privacy.withdraw_my_consent
        db = MagicMock()
        db.dsar_requests.insert.side_effect = RuntimeError("log table unavailable")
        async with app.app_context():
            with (
                patch.object(app, "db", db),
                patch.object(
                    PrivacyService, "withdraw_all_consent", return_value={"ok": True}
                ),
            ):
                async with app.test_request_context(
                    "/api/v1/privacy/me/consent/withdraw", method="POST"
                ):
                    from quart import g

                    g.current_user = _identity()
                    g.claims = {"tenant": "7"}
                    _, status = await handler.__wrapped__()
        assert status == 200


class TestAdminLayerStillEnterpriseGated:
    """Only the admin convenience layer (bulk ops, request dashboard) is paid."""

    ADMIN_ROUTES = [
        (privacy_admin.list_dsar_requests, "GET", "/requests", None),
        (privacy_admin.bulk_erase, "POST", "/bulk-erase", {"identity_ids": [1]}),
    ]

    @staticmethod
    def _tier_gated(route_func):
        """Unwrap @login_required and @admin_required, keeping @require_tier."""
        return route_func.__wrapped__.__wrapped__

    @pytest.mark.parametrize("route", ADMIN_ROUTES, ids=["list", "bulk-erase"])
    @pytest.mark.parametrize("tier", ["community", "professional"])
    async def test_admin_route_403_below_enterprise(self, app, route, tier):
        handler, method, path, body = route
        kwargs = {"method": method}
        if body is not None:
            kwargs["json"] = body
        async with app.app_context():
            with (
                patch.object(app, "db", MagicMock()),
                patch(
                    "apps.api.common.licensing.tier_gate.resolve_limits",
                    return_value=(tier, None),
                ),
                patch.object(PrivacyService, "bulk_erase") as bulk,
            ):
                async with app.test_request_context(
                    f"/api/v1/privacy-admin{path}", **kwargs
                ):
                    from quart import g

                    g.current_user = _identity()
                    g.claims = {"tenant": "7"}
                    _, status = await self._tier_gated(handler)()
        assert status == 403
        assert bulk.call_count == 0

    async def test_bulk_erase_on_enterprise_records_the_admin_and_skips_replays(
        self, app
    ):
        db = MagicMock()
        db.return_value.select.return_value.first.return_value = SimpleNamespace(
            legal_hold=False
        )
        results = {
            "results": [
                {"identity_id": 1, "anonymized": True, "already_erased": False},
                {"identity_id": 2, "anonymized": True, "already_erased": True},
                {"identity_id": 3, "anonymized": False, "error": "not found"},
            ]
        }
        async with app.app_context():
            with (
                patch.object(app, "db", db),
                patch(
                    "apps.api.common.licensing.tier_gate.resolve_limits",
                    return_value=("enterprise", None),
                ),
                patch.object(
                    PrivacyService, "bulk_erase", return_value=results
                ) as bulk,
            ):
                async with app.test_request_context(
                    "/api/v1/privacy-admin/bulk-erase",
                    method="POST",
                    json={"identity_ids": [1, 2, 3]},
                ):
                    from quart import g

                    g.current_user = _identity(id=9)
                    g.claims = {"tenant": "7"}
                    _, status = await self._tier_gated(privacy_admin.bulk_erase)()
        assert status == 200
        assert bulk.call_args.args[1:] == ([1, 2, 3], 7, False, 9)
        logged = [
            c.kwargs["identity_id"] for c in db.dsar_requests.insert.call_args_list
        ]
        assert logged == [1]  # only the new erasure, not the replay or the miss


async def _erase(app, body, *, user=None, token_claims=None, **service_patches):
    """POST /me/erase with `body`; returns (status, payload, erase mock, patches)."""
    db = MagicMock()
    db.return_value.select.return_value.first.return_value = SimpleNamespace(
        legal_hold=False
    )
    kwargs = {"method": "POST"}
    if body is not None:
        kwargs["json"] = body
    async with app.app_context():
        with (
            patch.object(app, "db", db),
            patch.object(
                PrivacyService,
                "erase_identity",
                return_value={"identity_id": 42, "anonymized": True, "receipt": {}},
                side_effect=service_patches.pop("erase_side_effect", None),
            ) as erase,
            patch.object(
                PrivacyService,
                "has_local_credential",
                return_value=service_patches.pop("has_credential", True),
            ),
            patch.object(
                PrivacyService,
                "verify_reauth",
                return_value=service_patches.pop("verified", True),
            ) as verify,
            patch(
                "apps.api.api.v1.privacy._token_claims",
                return_value=token_claims or {},
            ),
        ):
            async with app.test_request_context("/api/v1/privacy/me/erase", **kwargs):
                from quart import g

                g.current_user = user or _identity()
                g.claims = {"tenant": "7"}
                response, status = await privacy.erase_my_data.__wrapped__()
                payload = await response.get_json()
    return status, payload, erase, verify


class TestEraseConfirmationAndReauth:
    """M1: erasure is irreversible -- explicit confirmation plus one fresh factor."""

    @pytest.mark.parametrize(
        "body",
        [
            None,
            {},
            {"confirm": "erase"},
            {"confirm": "YES"},
            {"confirm": True},
            {"password": "pw"},
            ["ERASE"],
        ],
    )
    async def test_missing_or_wrong_confirmation_is_403_and_erases_nothing(
        self, app, body
    ):
        status, payload, erase, verify = await _erase(app, body)
        assert status == 403
        assert payload["confirmation_required"] is True
        assert "ERASE" in str(payload)  # the response says exactly what to send
        assert erase.call_count == 0
        assert verify.call_count == 0

    @pytest.mark.parametrize(
        "body",
        [
            {"confirm": "ERASE"},
            {"confirm": "ERASE", "password": "wrong"},
            {"confirm": "ERASE", "mfa_code": "000000"},
            {"confirm": "ERASE", "password": 12345},
        ],
    )
    async def test_failed_reauthentication_is_403_and_erases_nothing(self, app, body):
        status, payload, erase, _ = await _erase(app, body, verified=False)
        assert status == 403
        assert payload["reauthentication_required"] is True
        assert "password" in str(payload)
        assert "mfa_code" in str(payload)
        assert erase.call_count == 0

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ({"confirm": "ERASE", "password": "pw"}, ("pw", None)),
            ({"confirm": "ERASE", "mfa_code": "123456"}, (None, "123456")),
            ({"confirm": "ERASE", "password": 5, "mfa_code": ["x"]}, (None, None)),
        ],
    )
    async def test_supplied_factors_are_passed_to_verification(
        self, app, body, expected
    ):
        """One factor is enough; non-string junk is never forwarded."""
        status, _, erase, verify = await _erase(app, body)
        assert status == 200
        assert erase.call_count == 1
        args = verify.call_args.args
        assert args[1:3] == (42, 7)
        assert args[4:6] == expected

    async def test_portal_user_reauthenticates_with_their_own_credential(self, app):
        status, _, erase, verify = await _erase(
            app, {"confirm": "ERASE", "password": "pw"}, user=_portal_principal()
        )
        assert status == 200
        assert verify.call_args.args[3] is SubjectType.PORTAL_USER
        assert any(a is SubjectType.PORTAL_USER for a in erase.call_args.args)

    @pytest.mark.parametrize("age_seconds", [0, 60, 299])
    async def test_sso_subject_with_recent_login_needs_no_password(
        self, app, age_seconds
    ):
        """No local credential exists, so demanding one would deny the right."""
        import time

        status, _, erase, verify = await _erase(
            app,
            {"confirm": "ERASE"},
            has_credential=False,
            token_claims={"iat": time.time() - age_seconds, "type": "access"},
        )
        assert status == 200
        assert erase.call_count == 1
        assert verify.call_count == 0

    @pytest.mark.parametrize("iat", ["stale", "future", None, "abc", True])
    async def test_sso_subject_with_stale_login_is_403(self, app, iat):
        import time

        value = {"stale": time.time() - 301, "future": time.time() + 120}.get(iat, iat)
        status, payload, erase, _ = await _erase(
            app,
            {"confirm": "ERASE"},
            has_credential=False,
            token_claims={"iat": value, "type": "access"},
        )
        assert status == 403
        assert payload["recent_login_required"] is True
        assert "sign in again" in str(payload)
        assert erase.call_count == 0

    async def test_last_admin_is_409_with_transfer_instruction(self, app):
        status, payload, _, _ = await _erase(
            app,
            {"confirm": "ERASE", "password": "pw"},
            erase_side_effect=LastAdminError(
                "erasure blocked: you are the only active administrator of "
                "this tenant -- transfer the admin role to another user first"
            ),
        )
        assert status == 409
        assert payload["last_admin"] is True
        assert "transfer the admin role" in str(payload)


class TestOnlyInteractiveUserTokens:
    """Machine principals and refresh tokens can never exercise a subject's rights."""

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
    @pytest.mark.parametrize(
        "token_type", ["refresh", "service_worker", "coderun", "service_researcher"]
    )
    async def test_non_interactive_token_type_is_refused(self, app, route, token_type):
        with patch(
            "apps.api.api.v1.privacy._token_claims", return_value={"type": token_type}
        ):
            status, payload, service = await _call(
                app, route, user=_identity(), claims={"tenant": "7"}
            )
        assert status == 403
        assert "data_subject_required" in str(payload)
        assert service.call_count == 0

    @pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
    async def test_machine_principal_with_a_user_like_id_is_refused(self, app, route):
        """Even a nonzero id is refused once the principal carries a token_type."""
        status, _, service = await _call(
            app,
            route,
            user=_identity(id=42, token_type="service_worker"),
            claims={"tenant": "7"},
        )
        assert status == 403
        assert service.call_count == 0

    @pytest.mark.parametrize("token_type", ["access", "portal_user"])
    async def test_interactive_token_types_are_accepted(self, app, token_type):
        with patch(
            "apps.api.api.v1.privacy._token_claims", return_value={"type": token_type}
        ):
            status, _, _ = await _call(
                app, ROUTES[0], user=_identity(), claims={"tenant": "7"}
            )
        assert status == 200


class TestPerSubjectRateLimit:
    """M3: export/erase/consent/withdraw are paced per subject, never per IP."""

    REAL_GUARDS = True

    LIMITED = [r for r in ROUTES if r[4] != "get_consent"]

    @pytest.mark.parametrize("route", LIMITED, ids=[r[4] for r in LIMITED])
    async def test_exceeded_limit_is_429_with_retry_after(self, app, route):
        calls = []

        async def _deny(app_, *, subject, limit_per_minute, key_prefix):
            calls.append((subject, limit_per_minute, key_prefix))
            return False, {"Retry-After": "7"}

        handler, method, path, body, service_method = route
        kwargs = {"method": method}
        if body is not None:
            kwargs["json"] = body
        async with app.app_context():
            with (
                patch.object(app, "db", MagicMock()),
                patch("apps.api.api.v1.privacy.check_subject_rate_limit", _deny),
                patch.object(PrivacyService, service_method) as service,
                patch.object(PrivacyService, "verify_reauth") as verify,
            ):
                async with app.test_request_context(f"/api/v1/privacy{path}", **kwargs):
                    from quart import g

                    g.current_user = _identity()
                    g.claims = {"tenant": "7"}
                    response, status = await handler.__wrapped__()
        assert status == 429
        assert response.headers["Retry-After"] == "7"
        assert service.call_count == 0
        assert verify.call_count == 0  # limited before any password check
        subject, limit, prefix = calls[0]
        assert subject == f"7:{int(SubjectType.IDENTITY)}:42"
        assert limit == privacy.RATE_LIMITS[prefix.rsplit(":", 1)[1]]
        assert prefix.startswith("elder:privacy:")

    async def test_real_limiter_blocks_after_the_configured_erase_budget(self, app):
        """End to end through the shared limiter: budget+1 wrong-password tries."""
        statuses = []
        subject = _identity(id=987654)
        async with app.app_context():
            with (
                patch.object(app, "db", MagicMock()),
                patch.dict(app.config, {"RATELIMIT_ENABLED": True}),
                patch.object(PrivacyService, "has_local_credential", return_value=True),
                patch.object(PrivacyService, "verify_reauth", return_value=False),
                patch.object(PrivacyService, "erase_identity") as erase,
            ):
                for _ in range(privacy.RATE_LIMITS["erase"] + 1):
                    async with app.test_request_context(
                        "/api/v1/privacy/me/erase",
                        method="POST",
                        json={"confirm": "ERASE", "password": "guess"},
                    ):
                        from quart import g

                        g.current_user = subject
                        g.claims = {"tenant": "7"}
                        _, status = await privacy.erase_my_data.__wrapped__()
                        statuses.append(status)
        assert statuses == [403] * privacy.RATE_LIMITS["erase"] + [429]
        assert erase.call_count == 0

    async def test_other_subjects_are_not_affected_by_one_subjects_limit(self, app):
        seen = []

        async def _record(app_, *, subject, limit_per_minute, key_prefix):
            seen.append(subject)
            return True, {}

        for user in (_identity(id=1), _identity(id=2), _portal_principal()):
            with patch("apps.api.api.v1.privacy.check_subject_rate_limit", _record):
                await _call(app, ROUTES[0], user=user, claims={"tenant": "7"})
        assert seen == ["7:1:1", "7:1:2", "7:2:42"]  # tenant:subject_type:id


class TestExportResponseHardening:
    """The bundle is a JSON download: not cached, not sniffed, not rendered."""

    async def test_export_headers_and_json_escaping(self, app):
        hostile = {
            "identity": {"full_name": "=cmd|'/c calc'!A1", "note": "</script><b>x"},
            "categories": {},
        }
        db = MagicMock()
        async with app.app_context():
            with (
                patch.object(app, "db", db),
                patch.object(PrivacyService, "export_identity", return_value=hostile),
            ):
                async with app.test_request_context("/api/v1/privacy/me/export"):
                    from quart import g

                    g.current_user = _identity()
                    g.claims = {"tenant": "7"}
                    response, status = await privacy.export_my_data.__wrapped__()
                    raw = await response.get_data(as_text=True)
                    parsed = await response.get_json()
        assert status == 200
        assert response.mimetype == "application/json"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Content-Disposition"].startswith("attachment")
        assert parsed == hostile  # values round-trip as inert JSON strings
        assert raw.lstrip().startswith("{")
