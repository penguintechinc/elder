"""SSO OIDC route async-safety tests -- regression: security finding #7.

`oidc_logout`/`oidc_refresh` are `async def` routes but previously called
`OIDCService.logout()`/`refresh_tokens()` directly on the event loop --
both make blocking `requests`/authlib HTTP calls (OIDC discovery document
fetch + token endpoint exchange), so a slow/unresponsive IdP would stall
every other concurrent request on the same worker. Fixed by routing both
through `apps.api.utils.async_utils.run_in_threadpool`.

Ownership of the IdP config (the tenant-scoped gate that now runs first, also
via the thread pool) is covered with real rows in
``test_sso_oidc_login_hardening.py``; here it is stubbed so these tests stay
about *where the blocking IdP call runs*.
"""

from unittest.mock import AsyncMock, patch

import jwt
import pytest


def _portal_token(app) -> str:
    secret = app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY")
    payload = {"sub": "1", "tenant_id": 1, "type": "portal_user"}
    return jwt.encode(payload, secret, algorithm="HS256")


def _threadpool_passthrough():
    """AsyncMock replacement for run_in_threadpool that just calls func
    synchronously -- proves the call site awaits run_in_threadpool without
    needing a real thread pool in the test."""
    return AsyncMock(side_effect=lambda func, *args, **kwargs: func(*args, **kwargs))


def _pooled_functions(mock_pool) -> list:
    """The callables handed to run_in_threadpool, in call order."""
    return [call.args[0] for call in mock_pool.await_args_list]


@pytest.fixture(autouse=True)
def _scoped_gate_allows(mocker):
    """Stub the tenant-scoped ownership gate (tested with real rows elsewhere)."""
    return mocker.patch(
        "apps.api.api.v1.sso.OIDCService.get_idp_config_scoped",
        return_value={"id": 1},
    )


class TestOidcLogoutOffloadsToThreadpool:
    @pytest.mark.asyncio
    @patch("apps.api.api.v1.sso.OIDCService.logout")
    async def test_logout_calls_via_run_in_threadpool(
        self, mock_logout, async_client, app
    ):
        mock_logout.return_value = {"end_session_endpoint": "https://idp.example/end"}
        token = _portal_token(app)

        with patch(
            "apps.api.api.v1.sso.run_in_threadpool",
            new_callable=_threadpool_passthrough,
        ) as mock_pool:
            resp = await async_client.post(
                "/api/v1/sso/oidc/logout/1",
                json={"id_token_hint": "hint"},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
        assert _pooled_functions(mock_pool).count(mock_logout) == 1
        mock_logout.assert_called_once_with(1, "hint", None)

    @pytest.mark.asyncio
    @patch("apps.api.api.v1.sso.OIDCService.logout")
    async def test_logout_error_still_returns_400(self, mock_logout, async_client, app):
        mock_logout.return_value = {"error": "oidc_idp_not_found"}
        token = _portal_token(app)

        with patch(
            "apps.api.api.v1.sso.run_in_threadpool",
            new_callable=_threadpool_passthrough,
        ):
            resp = await async_client.post(
                "/api/v1/sso/oidc/logout/999",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 400


class TestOidcRefreshOffloadsToThreadpool:
    @pytest.mark.asyncio
    @patch("apps.api.api.v1.sso.OIDCService.refresh_tokens")
    async def test_refresh_calls_via_run_in_threadpool(
        self, mock_refresh, async_client, app
    ):
        mock_refresh.return_value = {"access_token": "new-access-token"}
        token = _portal_token(app)

        with patch(
            "apps.api.api.v1.sso.run_in_threadpool",
            new_callable=_threadpool_passthrough,
        ) as mock_pool:
            resp = await async_client.post(
                "/api/v1/sso/oidc/refresh/1",
                json={"refresh_token": "old-refresh-token"},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
        assert _pooled_functions(mock_pool).count(mock_refresh) == 1
        mock_refresh.assert_called_once_with(1, "old-refresh-token")

    @pytest.mark.asyncio
    @patch("apps.api.api.v1.sso.OIDCService.refresh_tokens")
    async def test_refresh_missing_token_rejected_before_threadpool(
        self, mock_refresh, async_client, app
    ):
        """Validation errors must short-circuit before touching the IdP --
        never spend a threadpool slot on a request that's already invalid."""
        token = _portal_token(app)
        with patch(
            "apps.api.api.v1.sso.run_in_threadpool",
            new_callable=_threadpool_passthrough,
        ) as mock_pool:
            resp = await async_client.post(
                "/api/v1/sso/oidc/refresh/1",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 400
        # Only the ownership gate used the pool; the IdP call never ran.
        assert mock_refresh not in _pooled_functions(mock_pool)
        mock_refresh.assert_not_called()
