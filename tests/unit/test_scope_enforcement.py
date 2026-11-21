"""Regression tests for @require_scope enforcement across modules.

Phase 2.5 retrofit: every authenticated module endpoint declares a
`@require_scope("<module>:<action>")` decorator. Existing module tests
authenticate as a superuser (which *bypasses* scope checks), so they do
not prove enforcement. These tests authenticate as a NON-superuser and
assert the scope gate behaves correctly:

  - missing scope  -> 403 "Missing required scope"
  - correct scope  -> the scope gate opens (never a "Missing required scope" 403)
  - wrong module   -> 403
  - superuser      -> bypasses scope checks

Note: the positive case asserts only that the *scope gate* opened, not that
the handler succeeds — some list handlers have unrelated runtime issues
(pre-existing threadpool/request-context bug) that are out of scope here.

regression: scope-retrofit
"""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio

SCOPE_DENIED = "missing required scope"

# Read endpoints with require_scope("<mod>:read"). Used for the deny paths, which
# reject at the scope gate *before* the handler body runs.
READ_ENDPOINTS = [
    ("/api/v1/entities", "infrastructure:read"),
    ("/api/v1/secrets", "secrets:read"),
    ("/api/v1/issues", "issues:read"),
    ("/api/v1/sbom/components", "sbom:read"),
    ("/api/v1/ipam/prefixes", "ipam:read"),
]

# Positive path executes the handler body, so restrict to endpoints whose list
# handlers run cleanly. (issues/sbom/ipam list handlers have a pre-existing
# threadpool/request-context bug — flagged separately, not a scope issue.)
POSITIVE_ENDPOINTS = [
    ("/api/v1/entities", "infrastructure:read"),
    ("/api/v1/secrets", "secrets:read"),
]


def _non_super_user():
    u = MagicMock()
    u.is_superuser = False
    u.id = 1
    u.tenant_id = 1
    return u


@pytest.mark.integration
class TestScopeEnforcement:
    """Non-superuser callers must present the correct scope."""

    @pytest.mark.parametrize("path,scope", READ_ENDPOINTS)
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_missing_scope_denied(
        self, mock_get_user, path, scope, async_client, generate_token
    ):
        """A token WITHOUT the required scope is rejected 403 by the scope gate."""
        mock_get_user.return_value = _non_super_user()
        token = generate_token(tenant_id=1, scopes=[])  # no scopes
        resp = await async_client.get(
            path, headers={"Authorization": f"Bearer {token}"}
        )
        assert (
            resp.status_code == 403
        ), f"{path} should 403 without scope, got {resp.status_code}"
        body = await resp.get_json()
        assert (
            SCOPE_DENIED in str(body).lower()
        ), f"{path} 403 should be a scope denial, got {body}"

    @pytest.mark.parametrize("path,scope", POSITIVE_ENDPOINTS)
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_correct_scope_opens_gate(
        self, mock_get_user, path, scope, async_client, generate_token
    ):
        """A token WITH the required scope passes the scope gate.

        The gate opening is proven by the absence of a "Missing required scope"
        403 — the handler may still return other statuses.
        """
        mock_get_user.return_value = _non_super_user()
        token = generate_token(tenant_id=1, scopes=[scope])
        resp = await async_client.get(
            path, headers={"Authorization": f"Bearer {token}"}
        )
        if resp.status_code == 403:
            body = await resp.get_json()
            assert (
                SCOPE_DENIED not in str(body).lower()
            ), f"{path} scope gate wrongly rejected valid scope {scope}: {body}"

    @pytest.mark.parametrize("path,scope", READ_ENDPOINTS)
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_wrong_module_scope_denied(
        self, mock_get_user, path, scope, async_client, generate_token
    ):
        """A scope for a different module does not grant access."""
        mock_get_user.return_value = _non_super_user()
        # Grant a scope from a different module than the endpoint requires.
        other = "documents:read" if not scope.startswith("documents") else "issues:read"
        token = generate_token(tenant_id=1, scopes=[other])
        resp = await async_client.get(
            path, headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403
        body = await resp.get_json()
        assert SCOPE_DENIED in str(body).lower()

    @patch("apps.api.auth.decorators.get_current_user")
    async def test_superuser_bypasses_scope(
        self, mock_get_user, async_client, generate_token
    ):
        """Superusers bypass scope checks (documented behaviour)."""
        su = MagicMock()
        su.is_superuser = True
        su.id = 1
        su.tenant_id = 1
        mock_get_user.return_value = su
        token = generate_token(tenant_id=1, scopes=[])  # no scopes
        resp = await async_client.get(
            "/api/v1/entities", headers={"Authorization": f"Bearer {token}"}
        )
        if resp.status_code == 403:
            body = await resp.get_json()
            assert SCOPE_DENIED not in str(body).lower()
